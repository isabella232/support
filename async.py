import os
import copy
import functools
import time
import imp
import traceback
import platform
import threading
import collections

from asf.asf_context import ASFError
#TODO: migrate ASFError out of ASF to a more root location
import gevent.pool
import gevent.socket
import gevent.threadpool
import gevent.greenlet
import gevent.hub  # for check_fork wrapper

import pp_crypt
import context

import ll

ml = ll.LLogger()


@functools.wraps(gevent.spawn)
def spawn(*a, **kw):
    if a:
        f = a[0]
    else:
        f = kw['run']
    # NOTE: tested functools.wraps to take 3.827 microseconds, which is okay
    gr = gevent.spawn(
        functools.wraps(f)(_exception_catcher), *a, **kw)
    context.get_context().greenlet_ancestors[gr] = gevent.getcurrent()
    return gr


sleep = gevent.sleep  # alias gevent.sleep here so user doesn't have to know/worry about gevent
Timeout = gevent.Timeout  # alias Timeout here as well since this is a darn useful class


def _exception_catcher(f, *a, **kw):
    try:
        return f(*a, **kw)
    except Exception as e:  # NOTE: would rather do this with weakrefs,
        if not hasattr(e, '__greenlet_traces'):  # but Exceptions are not weakref-able
            e.__greenlet_traces = []
        traces = e.__greenlet_traces
        traces.append(traceback.format_exc())
        traces.append(repr(gevent.getcurrent()))
        raise


def get_parent(greenlet=None):
    return context.get_context().greenlet_ancestors.get(greenlet or gevent.getcurrent())


def get_cur_correlation_id():
    ctx = context.get_context()
    ancestors = ctx.greenlet_ancestors
    corr_ids = ctx.greenlet_correlation_ids
    cur = gevent.getcurrent()
    #walk ancestors looking for a correlation id
    while cur not in corr_ids and cur in ancestors:
        cur = ancestors[cur]
    #if no correlation id found, create a new one at highest level
    if cur not in corr_ids:
        #this is reproducing CalUtility.cpp
        #TODO: where do different length correlation ids come from in CAL logs?
        t = time.time()
        corr_val = "{0}{1}{2}{3}".format(gevent.socket.gethostname(),
                                         os.getpid(), int(t), int(t % 1 * 10 ** 6))
        corr_id = "{0:x}{1:x}".format(pp_crypt.fnv_hash(corr_val), int(t % 1 * 10 ** 6))
        corr_ids[cur] = corr_id
    return corr_ids[cur]


def set_cur_correlation_id(corr_id):
    context.get_context().greenlet_correlation_ids[gevent.getcurrent()] = corr_id


def _make_threadpool_dispatch_decorator(name, size):
    def dispatch(f):
        '''
        decorator to mark a function as cpu-heavy; will be executed in a separate
        thread to avoid blocking any socket communication
        '''

        @functools.wraps(f)
        def g(*a, **kw):
            enqueued = curtime()  # better than microsecond precision
            ctx = context.get_context()
            tlocals = ctx.thread_locals
            started = []

            def in_thread(*a, **kw):
                ml.ld3("In thread {0}", f.__name__)
                started.append(curtime())
                return f(*a, **kw)
            #some modules import things lazily; it is too dangerous to run a function
            #in another thread if the import lock is held by the current thread
            #(this happens rarely -- only if the thread dispatched function is being executed
            #at the import time of a module)
            if not ctx.cpu_thread_enabled or imp.lock_held():
                ret = in_thread(*a, **kw)
            else:
                if getattr(tlocals, 'in_' + name + '_thread', False):
                    ret = in_thread(*a, **kw)
                else:
                    attr = name + '_thread'
                    if not hasattr(tlocals, attr):
                        setattr(tlocals, attr, gevent.threadpool.ThreadPool(size))
                        ml.ld2("Getting new thread pool for " + name)

                        def set_flag():
                            setattr(tlocals, 'in_' + name + '_thread', True)
                        getattr(tlocals, attr).apply_e((Exception,), set_flag, (), {})
                    pool = getattr(tlocals, attr)
                    ctx.stats[name + '.depth'].add(1 + len(pool))
                    ret = pool.apply_e((Exception,), in_thread, a, kw)
                    ml.ld3("Enqueued to thread {0}/depth {1}", f.__name__, len(pool))
            start = started[0]
            duration = curtime() - start
            queued = start - enqueued
            if hasattr(ret, '__len__') and callable(ret.__len__):
                ret_size = ret.__len__()
            else:
                ret_size = None
            _queue_stats(name, f.__name__, queued, duration, ret_size)
            return ret

        g.no_defer = f
        return g

    return dispatch


# TODO: build this out to support a pool of threads and fully support
# the gevent.threadpool.ThreadPool API and upstream back to gEvent
# so they have a threadpool which is purely event based instead of
# using polling
class CPUThread(object):
    '''
    Manages a single worker thread to dispatch cpu intensive tasks to.

    Signficantly less overhead than gevent.threadpool.ThreadPool() since it 
    uses prompt notifications rather than polling.  The trade-off is that only
    one thread can be managed this way.

    Since there is only one thread, hub.loop.async() objects may be used
    instead of polling to handle inter-thread communication.
    '''
    def __init__(self):
        self.in_q = collections.deque()
        self.out_q = collections.deque()
        self.in_async = None
        self.out_async = gevent.get_hub().loop.async()
        self.out_q_has_data = gevent.event.Event()
        self.out_async.start(self.out_q_has_data.set)
        self.worker = threading.Thread(target=self._run)
        self.worker.daemon = True
        self.stopping = False
        self.results = {}
        # start running thread / greenlet after everything else is set up
        self.worker.start()
        self.notifier = gevent.spawn(self._notify)

    def _run(self):
        try:
            self.in_async = gevent.get_hub().loop.async()
            self.in_q_has_data = gevent.event.Event()
            self.in_async.start(self.in_q_has_data.set)
            while not self.stopping:
                if not self.in_q:
                    # wait for more work
                    self.in_q_has_data.clear()
                    self.in_q_has_data.wait()
                    continue
                # arbitrary non-preemptive service discipline can go here
                # FIFO for now, but we should experiment with others
                jobid, func, args, kwargs, enqueued = self.in_q.popleft()
                started = curtime()
                try:
                    ret = self.results[jobid] = func(*args, **kwargs)
                except Exception as e:
                    ret = self.results[jobid] = self._Caught(e)
                self.out_q.append(jobid)
                self.out_async.send()
                # keep track of some statistics
                queued, duration = started - enqueued, curtime() - started
                size = None
                # ret s set up above before async send
                if hasattr(ret, '__len__') and callable(ret.__len__):
                    size = len(ret)
                    _queue_stats('cpu_bound', func.__name__, queued, duration, size)
        except:
            self._error()
            # this may always halt the server process

    def apply(self, func, args, kwargs):
        done = gevent.event.Event()
        self.in_q.append((done, func, args, kwargs, curtime()))
        context.get_context().stats['cpu_bound.depth'].add(1 + len(self.in_q))
        while not self.in_async:
            gevent.sleep(0.01)  # poll until worker thread has finished initializing
        self.in_async.send()
        done.wait()
        res = self.results[done]
        del self.results[done]
        if isinstance(res, self._Caught):
            raise res.err
        return res

    def _notify(self):
        try:
            while not self.stopping:
                if not self.out_q:
                    # wait for jobs to complete
                    self.out_q_has_data.clear()
                    self.out_q_has_data.wait()
                    continue
                self.out_q.popleft().set()
        except:
            self._error()

    class _Caught(object):
        def __init__(self, err):
            self.err = err

    def __repr__(self):
        return "<CPUThread@{0} in_q:{1} out_q:{2}>".format(id(self), len(self.in_q), len(self.out_q))

    def _error(self):
        # TODO: something better, but this is darn useful for debugging
        import traceback
        traceback.print_exc()
        if hasattr(context.get_context().thread_locals, 'cpu_bound_thread') and \
                   context.get_context().thread_locals.cpu_bound_thread is self:
            del context.get_context().thread_locals.cpu_bound_thread      


def _queue_stats(qname, fname, queued_ns, duration_ns, size_B=None):
    ctx = context.get_context()
    fprefix = qname + '.' + fname
    ctx.stats[fprefix + '.queued(ms)'].add(queued_ns * 1000)
    ctx.stats[fprefix + '.duration(ms)'].add(duration_ns * 1000)
    ctx.stats[qname + '.queued(ms)'].add(queued_ns * 1000)
    ctx.stats[qname + '.duration(ms)'].add(duration_ns * 1000)
    if size_B is not None:
        ctx.stats[fprefix + '.len'].add(size_B)
        if duration_ns:  # may be 0
            ctx.stats[fprefix + '.rate(B/ms)'].add(size_B / (duration_ns * 1000.0))


def timed_execution(timeout_secs = 120.0, in_timeout_value = None):
    '''
    decorator to mark a function as wanting an unconditional timeout
    '''
    def decorat(f):
        @functools.wraps(f)
        def g(*a, **kw):
            the_timeout_value = object()
            rr = gevent.with_timeout(timeout_secs, f, timeout_value=the_timeout_value, *a, **kw)
            if rr == the_timeout_value:
                return in_timeout_value
            return rr
        return g
    return decorat


if hasattr(time, "perf_counter"):
    curtime = time.perf_counter  # 3.3
elif platform.system() == "Windows":
    curtime = time.clock
else:
    curtime = time.time


io_bound = _make_threadpool_dispatch_decorator('io_bound', 10)  # TODO: make size configurable
# N.B.  In many cases fcntl could be used as an alternative method of achieving non-blocking file
# io on unix systems

def cpu_bound(f):
    @functools.wraps(f)
    def g(*a, **kw):
        ctx = context.get_context()
        if not ctx.cpu_thread_enabled or imp.lock_held():
            return f(*a, **kw)
        if not hasattr(ctx.thread_locals, 'cpu_bound_thread'):
            ctx.thread_locals.cpu_bound_thread = CPUThread()
        ml.ld3("Calling in cpu thread {0}", f.__name__)
        return context.get_context().thread_locals.cpu_bound_thread.apply(f, a, kw)
    g.no_defer = f
    return g


def close_threadpool():
    tlocals = context.get_context().thread_locals
    if hasattr(tlocals, 'cpu_bound_thread'):
        ml.ld2("Closing thread pool {0}", id(tlocals.cpu_thread))
        cpu_thread = tlocals.cpu_bound_thread
        cpu_thread.stopping = True
        del tlocals.cpu_bound_thread


def _safe_req(req):
    'capture the stack trace of exceptions that happen inside a greenlet'
    try:
        return req()
    except Exception as e:
        raise ASFError(e)


def join(reqs, raise_exc=False, timeout=None):
    greenlets = []
    for req in reqs:
        if isinstance(req, gevent.greenlet.Greenlet):
            greenlets.append(req)
        else:
            greenlets.append(spawn(_safe_req, req))
    gevent.joinall(greenlets, raise_error=raise_exc, timeout=timeout)
    results = []
    for gr, req in zip(greenlets, reqs):
        if gr.successful():
            results.append(gr.value)
        elif gr.ready():  # finished, but must have had error
            results.append(gr.exception)
        else:  # didnt finish, must have timed out
            results.append(AsyncTimeoutError(req, timeout))
    return results


class AsyncTimeoutError(ASFError):
    def __init__(self, request=None, timeout=None):
        try:
            self.ip = request.ip
            self.port = request.port
            self.service_name = request.service
            self.op_name = request.operation
        except AttributeError:
            pass
        if timeout:
            self.timeout = timeout

    def __str__(self):
        ret = "AsyncTimeoutError"
        try:
            ret += " encountered while to trying execute " + self.op_name \
                   + " on " + self.service_name + " (" + str(self.ip) + ':'      \
                   + str(self.port) + ")"
        except AttributeError:
            pass
        try:
            ret += " after " + str(self.timeout) + " seconds"
        except AttributeError:
            pass
        return ret

ASFTimeoutError = AsyncTimeoutError
#NOTE: for backwards compatibility; name changed on March 2013
#after a decent interval, this can be removed


### What follows is code related to map() contributed from MoneyAdmin's asf_util
class Node(object):
    def __init__(self, ip, port, **kw):
        self.ip   = ip
        self.port = port

        # in case you want to add name/location/id/other metadata
        for k, v in kw.items():
            setattr(self, k, v)


# call it asf_map to avoid name collision with builtin map?
# return callable to avoid collision with kwargs?
def map_factory(op, node_list, raise_exc=False, timeout=None):
    """
    map_factory() enables easier concurrent calling across multiple servers,
    provided a node_list, which is an iterable of Node objects.
    """

    def asf_map(*a, **kw):
        return join([op_ip_port(op, node.ip, node.port).async(*a, **kw)
                     for node in node_list], raise_exc=raise_exc, timeout=timeout)
    return asf_map


def op_ip_port(op, ip, port):
    serv = copy.copy(op.service)
    serv.meta = copy.copy(serv.meta)
    serv.meta.ip = ip
    serv.meta.port = port
    op = copy.copy(op)
    op.service = serv
    return op


#these words fit the following criteria:
#1- one or two syllables (fast to say), short (fast to write)
#2- simple to pronounce and spell (no knee/knife)
#3- do not have any homonyms (not aunt/ant, eye/I, break/brake, etc)
# they are used to generate easy to communicate correlation IDs
# should be edited if areas of confusion are discovered :-)
SIMPLE_WORDS_LIST = ["air", "art", "arm", "bag", "ball", "bank", "bath", "back",
                     "base", "bed", "beer", "bell", "bird", "block", "blood", "boat", "bone", "bowl",
                     "box", "boy", "branch", "bridge", "bus", "cake", "can", "cap", "car",
                     "case", "cat", "chair", "cheese", "child", "city", "class", "clock", "cloth",
                     "cloud", "coat", "coin", "corn", "cup", "day", "desk", "dish", "dog", "door",
                     "dream", "dress", "drink", "duck", "dust", "ear", "earth", "egg", "face", "fact",
                     "farm", "fat", "film", "fire", "fish", "flag", "food", "foot", "fork", "game",
                     "gate", "gift", "glass", "goat", "gold", "grass", "group", "gun", "hair",
                     "hand", "hat", "head", "heart", "hill", "home", "horse", "house", "ice",
                     "iron", "job", "juice", "key", "king", "lamp", "land", "leaf", "leg", "life",
                     "lip", "list", "lock", "luck", "man", "map", "mark", "meal", "meat", "milk",
                     "mind", "mix", "month", "moon", "mouth", "name", "net", "noise", "nose",
                     "oil", "page", "paint", "pan", "park", "party", "pay", "path", "pen",
                     "pick", "pig", "pin", "plant", "plate", "point", "pool", "press", "prize",
                     "salt", "sand", "seat", "ship", "soup", "space", "spoon", "sport",
                     "spring", "shop", "show", "sink", "skin", "sky", "smoke", "snow", "step", "stone",
                     "store", "star", "street", "sweet", "swim", "tea", "team", "test", "thing",
                     "tool", "tooth", "top", "town", "train", "tram", "tree", "type",
                     "wash", "west", "wife", "wind", "wire", "word", "work", "world", "yard", "zoo"]

# SSLSocket below started as a significant copy-pasta from gEvent, so we leave the license message
# here

'''
Permission is hereby granted, free of charge, to any person obtaining a copy of this software
and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
'''


from OpenSSL import SSL
from gevent.socket import socket, _fileobject, __socket__, error, timeout, EWOULDBLOCK
from gevent.socket import wait_read, wait_write, timeout_default
import sys

#__all__ = ['ssl', 'sslerror']

try:
    sslerror = __socket__.sslerror
except AttributeError:

    class sslerror(error):
        pass


def _wrap(v):
    def wrapper(self, *a, **kw):
        try:
            return v(SSL.Connection if type(self) is type else self._proxy,
                     *a, **kw)
        except SSL.ZeroReturnError:
            return ''
        except SSL.Error as e:
            t = type(e)  # convert OpenSSL errors to sub-classes of SSL.error
            if t not in SSL_EX_MAP:
                SSL_EX_MAP[t] = type(t.__name__, (__socket__.error, t), {})
            raise SSL_EX_MAP[t](*e.args)
    functools.update_wrapper(wrapper, v,
                             set(functools.WRAPPER_ASSIGNMENTS) & set(dir(v)))
    return wrapper


SSL_EX_MAP = {}


def make_sock_close_wrapper():
    items = dict(SSL.Connection.__dict__)
    wrap_items = {}
    for k, v in items.items():
        if k in ('__init__', '__module__', '__slots__', '__new__', '__getattribute__'):
            continue
        wrap_items[k] = _wrap(v) if callable(v) else v
    def __init__(self, sock):
        self._proxy = sock
    wrap_items['__init__'] = __init__
    wrap_items['close'] = lambda self, *a, **kw: None
    #OpenSSL.SSL.Connection itself uses __getattr__ for some things,
    #so in addition to copying over the dict we also need to pass through
    wrap_items['__getattr__'] = lambda self, k: getattr(self._proxy, k)
    return type('SockCloseWrapper', (object,), wrap_items)

#this class is a proxy for an underlying socket (OpenSSL.SSL.Connection in this case)
#with the exception of close, which it ignores for the purposes of not closing the
#socket before OpenSSL buffers have been cleared to the underlying socket when
#gevent.pywsgi calls close()
SockCloseWrapper = make_sock_close_wrapper()


class SSLSocket(socket):

    def __init__(self, sock, server_side=False):
        'sock is an instance of OpenSSL.SSL.Connection'
        if server_side:  # work-around gevent.pywsgi hard-closing the underlying socket
            sock = SockCloseWrapper(sock)
        socket.__init__(self, _sock=sock)
        self._makefile_refs = 0
        if server_side:
            self._sock.set_accept_state()
        else:
            self._sock.set_connect_state()

    def __getattr__(self, item):
        assert item != '_sock', item
        # since socket no longer implements __getattr__ let's do it here
        # even though it's not good for the same reasons it was not good on socket instances
        # (it confuses sublcasses)
        return getattr(self._sock, item)

    def _formatinfo(self):
        return socket._formatinfo(self) + ' state_string=%r' % self._sock.state_string()

    def accept(self):
        sock, addr = socket.accept(self)
        client = SSLSocket(sock._sock, server_side=True)
        client.do_handshake()
        return client, addr

    def do_handshake(self, timeout=timeout_default):
        if timeout is timeout_default:
            timeout = self.timeout
        # TODO: how to handle if timeout is 0.0, e.g. somebody
        # wants to use this thing in a select() loop?
        # don't worry about for now, because you'd have to be crazy
        # to put a select loop when there are all these excellent greenlets available
        while True:
            try:
                self._sock.do_handshake()
                break
            except SSL.WantReadError:
                sys.exc_clear()
                wait_read(self.fileno(), timeout=timeout)
            except SSL.WantWriteError:
                sys.exc_clear()
                wait_write(self.fileno(), timeout=timeout)
            except SSL.SysCallError, ex:
                raise sslerror(SysCallError_code_mapping.get(ex.args[0], ex.args[0]), ex.args[1])
            except SSL.Error, ex:
                raise sslerror(str(ex))

    def connect(self, *args):
        socket.connect(self, *args)
        self.do_handshake()

    def send(self, data, flags=0, timeout=timeout_default):
        ml.ld2("SSL: {{{0}}}/FD {1}: OUTDATA: {{{2}}}",
               id(self), self._sock.fileno(),
               data)
        if timeout is timeout_default:
            timeout = self.timeout
        while True:
            try:
                return self._sock.send(data, flags)
            except SSL.WantWriteError as ex:
                if self.timeout == 0.0:
                    raise timeout(str(ex))
                else:
                    sys.exc_clear()
                    wait_write(self.fileno(), timeout=timeout)
            except SSL.WantReadError as ex:
                if self.timeout == 0.0:
                    raise timeout(str(ex))
                else:
                    sys.exc_clear()
                    wait_read(self.fileno(), timeout=timeout)
            except SSL.SysCallError as ex:
                if ex[0] == -1 and data == "":
                    # errors when writing empty strings are expected and can be ignored
                    return 0
                raise sslerror(SysCallError_code_mapping.get(ex.args[0], ex.args[0]), ex.args[1])
            except SSL.Error as ex:
                raise sslerror(str(ex))

    def recv(self, buflen, flags=0):
        pending = self._sock.pending()
        if pending:
            retval = self._sock.recv(min(pending, buflen), flags)
            ml.ld2("SSL: {{{0}}}/FD {1}: INDATA: {{{2}}}",
                   id(self), self._sock.fileno(), retval)
            return retval
        while True:
            try:
                retval = self._sock.recv(buflen)
                ml.ld2("SSL: {{{0}}}/FD {1}: INDATA: {{{2}}}",
                       id(self), self._sock.fileno(), retval)
                return retval
            except SSL.WantReadError as ex:
                if self.timeout == 0.0:
                    raise timeout(str(ex))
                else:
                    sys.exc_clear()
                    wait_read(self.fileno(), timeout=self.timeout)
            except SSL.WantWriteError as ex:
                if self.timeout == 0.0:
                    ml.ld2("SSL: {{{0}}}/FD {1}: Timing out", id(self), self._sock.fileno())
                    raise timeout(str(ex))
                else:
                    sys.exc_clear()
                    wait_read(self.fileno(), timeout=self.timeout)
            except SSL.ZeroReturnError:
                ml.ld2("SSL: {{{0}}}/FD {1}:  INDATA: {{}}", id(self), self._sock.fileno())
                return ''
            except SSL.SysCallError as ex:
                ml.ld2("SSL: {{{0}}}/FD {1}: Call Exception", id(self), self._sock.fileno())
                raise sslerror(SysCallError_code_mapping.get(ex.args[0], ex.args[0]), ex.args[1])
            except SSL.Error as ex:
                ml.ld2("SSL: {{{0}}}/FD {1}: Exception", id(self), self._sock.fileno())
                raise sslerror(str(ex))

    def read(self, buflen=1024):
        """
        NOTE: read() in SSLObject does not have the semantics of file.read
        reading here until we have buflen bytes or hit EOF is an error
        """
        return self.recv(buflen)

    def write(self, data):
        try:
            return self.sendall(data)
        except SSL.Error, ex:
            raise sslerror(str(ex))

    def makefile(self, mode='r', bufsize=-1):
        self._makefile_refs += 1
        return _fileobject(self, mode, bufsize, close=True)

    def shutdown(self, how):
        self._sock.shutdown()
        #accept how parameter for compatibility with normal sockets,
        #although OpenSSL.SSL.Connection objects do not accept how

    def close(self):
        if self._makefile_refs < 1:
            self._sock.shutdown()
            # QQQ wait until shutdown completes?
            socket.close(self)
        else:
            self._makefile_refs -= 1


SysCallError_code_mapping = {-1: 8}


def wrap_socket_context(sock, context, server_side=False):
    timeout = sock.gettimeout()
    try:
        sock = sock._sock
    except AttributeError:
        pass
    connection = SSL.Connection(context, sock)
    ssl_sock = SSLSocket(connection, server_side)
    ssl_sock.settimeout(timeout)

    try:
        sock.getpeername()
    except Exception:
        # no, no connection yet
        pass
    else:
        # yes, do the handshake
        ssl_sock.do_handshake()
    return ssl_sock


def killsock(sock):
    if hasattr(sock, '_sock'):
        ml.ld("Killing socket {0}/FD {1}", id(sock), sock._sock.fileno())
    else:
        ml.ld("Killing socket {0}", id(sock))
    try:
        # TODO: better ideas for how to get SHUT_RDWR constant?
        sock.shutdown(gevent.socket.SHUT_RDWR)
    except (error, SSL.Error):
        pass  # just being nice to the server, don't care if it fails
    except Exception as e:
        context.get_context().cal.event("INFO", "SOCKET", "0",
                                        "unexpected error closing socket: " + repr(e))
    try:
        sock.close()
    except (error, SSL.Error):
        pass  # just being nice to the server, don't care if it fails
    except Exception as e:
        context.get_context().cal.event("INFO", "SOCKET", "0",
                                        "unexpected error closing socket: " + repr(e))


PID = os.getpid()


def check_fork(fn):
        """Hack for Django/infra interaction to reset after non-gevent fork"""
        @functools.wraps(fn)
        def wrapper(request):
                global PID
                if PID != os.getpid():
                        gevent.hub.get_hub().loop.reinit()
                        PID = os.getpid()
                return fn(request)
        return wrapper


### a little helper for running a greenlet-friendly console
## implemented here since it directly references gevent

import sys
import code
import gevent.fileobject


class GreenConsole(code.InteractiveConsole):
    def __init__(self):
        code.InteractiveConsole.__init__(self)
        self._green_stdin = gevent.fileobject.FileObject(sys.stdin)
        self._green_stdout = gevent.fileobject.FileObject(sys.stdout)
        self._green_stderr = gevent.fileobject.FileObject(sys.stderr)

    def write(self, data):
        while data:  # print in chunks < 4096
            self._green_stderr.write(data[:4096])
            self._green_stderr.flush()
            data = data[4096:]
            sleep(0)

    def raw_input(self, prompt=""):
        self.write(prompt)
        inp = self._green_stdin.readline()
        if inp == "":
            raise OSError("standard in was closed while running REPL")
        inp = inp[:-1]
        return inp


def start_repl(local=None, banner="infra REPL (exit with Ctrl+C)"):
    gevent.spawn(GreenConsole().interact(banner))
