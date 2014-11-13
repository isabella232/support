import os
import os.path
import copy
import functools
import time
import imp
import traceback
import platform
import threading
import collections

from exceptions import ASFError
import gevent.pool
import gevent.socket
import gevent.threadpool
import gevent.greenlet
import faststat

import context

import ll

ml = ll.LLogger()


sleep = gevent.sleep  # alias gevent.sleep here so user doesn't have to know/worry about gevent
Timeout = gevent.Timeout  # alias Timeout here as well since this is a darn useful class
with_timeout = gevent.with_timeout
nanotime = faststat.nanotime


def staggered_retries(run, *a, **kw):
    """
        A version of spawn that will block will it is done
        running the function, and which will call the function
        repeatedly as time progresses through the timeouts list.

        Best used for idempotent network calls (e.g. UserRead).

        e.g.
        user_data = infra.async.staggered_retries(get_data, max_results,
                                                  latent_data_ok, public_credential_load,
                                                  timeouts_secs=[0.1, 0.5, 1, 2])

        returns None on timeout.

    """
    ctx = context.get_context()
    ready = gevent.event.Event()
    ready.clear()

    def call_back(source):
        if source.successful():
            ready.set()

    if 'timeouts_secs' in kw:
        timeouts_secs = kw.pop('timeouts_secs')
    else:
        timeouts_secs = [0.05, 0.1, 0.15, 0.2]
    if timeouts_secs[0] > 0:
        timeouts_secs.insert(0, 0)
    gs = spawn(run, *a, **kw)
    gs.link_value(call_back)
    running = [gs]
    for i in range(1, len(timeouts_secs)):
        this_timeout = timeouts_secs[i] - timeouts_secs[i - 1]
        if ctx.dev:
            this_timeout = this_timeout * 5.0
        ml.ld2("Using timeout {0}", this_timeout)
        try:
            with Timeout(this_timeout):
                ready.wait()
                break
        except Timeout:
            ml.ld2("Timed out!")
            ctx.cal.event('ASYNC-STAGGER', run.__name__, '0', {'timeout': this_timeout})
            gs = spawn(run, *a, **kw)
            gs.link_value(call_back)
            running.append(gs)
    vals = [l.value for l in running if l.successful()]
    for g in running:
        g.kill()
    if vals:
        return vals[0]
    else:
        return None


@functools.wraps(gevent.spawn)
def spawn(run, *a, **kw):
    gr = gevent.spawn(_exception_catcher, run, *a, **kw)
    ctx = context.get_context()
    ctx.greenlet_ancestors[gr] = gevent.getcurrent()
    if '_pid' not in kw:
        ctx.cal.event('ASYNC', 'spawn.' + run.__name__, '0', {'id': hex(id(gr))[-5:]})
    if hasattr(run, '__code__'):
        gr.spawn_code = run.__code__
    else:
        gr.spawn_code = None
    return gr


def join(reqs, raise_exc=False, timeout=None):
    with context.get_context().cal.atrans('ASYNC', 'join') as trans:
        greenlets = []
        for req in reqs:
            if isinstance(req, gevent.greenlet.Greenlet):
                greenlets.append(req)
            else:
                greenlets.append(spawn(_safe_req, req))
        gevent.joinall(greenlets, raise_error=raise_exc, timeout=timeout)
        results = []
        aliases = getattr(context.get_context().cal, "aliaser", None)
        aliases = aliases and aliases.mapping or {}
        for gr, req in zip(greenlets, reqs):
            # handle the case that we are joining on a greenlet that
            # wasn't spawned via async.spawn and so doesn't have a
            # spawn_code attribute
            name = getattr(gr, "spawn_code", None)
            name = (name and name.co_name or "") + "(" + hex(id(gr))[-5:] + ")"
            if gr.successful():
                results.append(gr.value)
                trans.msg[name] = "0"
            elif gr.ready():  # finished, but must have had error
                results.append(gr.exception)
                trans.msg[name] = "1"
            else:  # didnt finish, must have timed out
                results.append(AsyncTimeoutError(req, timeout))
                trans.msg[name] = "U"
            trans.msg[name] += ",tid=" + str(aliases.get(gr, "(-)"))
        return results

_CI = ((os.getpid() & 0xff) << 24)


def parallel(reqs):
    global _CI
    ctx = context.get_context()
    glets = []
    pid = id(gevent.getcurrent())
    with ctx.cal.trans('EXECT', 'M') as tran:
        tran.msg['PI'] = pid
        for x in reqs:
            if hasattr(x, '__call__'):
                # allow list of functions as well as list of spawn args
                x = [x]
            _CI += 1
            glets.append(spawn(*x, _pid=pid, _ci=_CI))
            ctx.cal.event('EXECP', 'P', '0', {'CI': _CI, 'Name': str(x[0].__name__)})

        results = join(glets)
    return results


def _exception_catcher(f, *a, **kw):
    ctx = context.get_context()
    try:
        if infra.cal.get_root_trans() is None:
            return f(*a, **kw)
        my_name = 'ASYNC-SPAWN.' + f.__name__.upper()
        if '_pid' in kw and '_ci' in kw:
            pid = kw.pop('_pid')
            _ci = kw.pop('_ci')
            if ctx.async_cal_visible:
                ctx.cal.event('ASYNC', "API", "1", {})
            with ctx.cal.trans('EXECP', my_name) as trans:
                trans.msg['CI'] = _ci
                trans.msg['PI'] = pid
                return f(*a, **kw)
        else:
            with ctx.cal.trans('ASYNC', my_name):
                return f(*a, **kw)
    except gevent.greenlet.GreenletExit:  # NOTE: would rather do this with weakrefs,
        ml.ld("Exited by majeur")
    except Exception as e:  # NOTE: would rather do this with weakrefs,
        if not hasattr(e, '__greenlet_traces'):  # but Exceptions are not weakref-able
            e.__greenlet_traces = []
        traces = e.__greenlet_traces
        traces.append(traceback.format_exc())
        traces.append(repr(gevent.getcurrent()))
        raise


def timed(f):
    '''
    wrap a function and time all of its execution calls in milliseconds
    '''
    fname = os.path.basename(f.__code__.co_filename) or '_'
    line_no = repr(f.__code__.co_firstlineno)
    name = 'timed.{0}[{1}:{2}](ms)'.format(f.__name__, fname, line_no)

    @functools.wraps(f)
    def g(*a, **kw):
        s = nanotime()
        r = f(*a, **kw)
        context.get_context().stats[name].add((nanotime() - s) / 1e6)
        return r
    return g


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
        import pp_crypt  # breaking circular import
        #this is reproducing CalUtility.cpp
        #TODO: where do different length correlation ids come from in CAL logs?
        t = time.time()
        corr_val = "{0}{1}{2}{3}".format(gevent.socket.gethostname(),
                                         os.getpid(), int(t), int(t % 1 * 10 ** 6))
        corr_id = "{0:x}{1:x}".format(
            pp_crypt.fnv_hash(corr_val) & 0xFFFFFFFF,
            int(t % 1 * 10 ** 6) & 0xFFFFFFFF)
        ml.ld2("Generated corr_id {0}", corr_id)
        corr_ids[cur] = corr_id
    return corr_ids[cur]


def set_cur_correlation_id(corr_id):
    context.get_context().greenlet_correlation_ids[gevent.getcurrent()] = corr_id


def unset_cur_correlation_id():
    greenlet_id = gevent.getcurrent()
    if greenlet_id in context.get_context().greenlet_correlation_ids:
        del context.get_context().greenlet_correlation_ids[greenlet_id]


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
                prsize = ret.__len__()  # parameter-or-return size
            elif a and hasattr(a[0], '__len__') and callable(a[0].__len__):
                prsize = a[0].__len__()
            else:
                prsize = None
            _queue_stats(name, f.__name__, queued, duration, prsize)
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
        # in_cpubound_thread is sentinel to prevent double thread dispatch
        context.get_context().thread_locals.in_cpubound_thread = True
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


if hasattr(time, "perf_counter"):
    curtime = time.perf_counter  # 3.3
elif platform.system() == "Windows":
    curtime = time.clock
else:
    curtime = time.time


io_bound = _make_threadpool_dispatch_decorator('io_bound', 10)  # TODO: make size configurable
# N.B.  In many cases fcntl could be used as an alternative method of achieving non-blocking file
# io on unix systems


def cpu_bound(f, p=None):
    '''
    Cause the decorated function to have its execution deferred to a separate thread to avoid
    blocking the IO loop in the main thread.

    Example usage:

    @async.cpu_bound
    def my_slow_function():
        pass
    '''
    @functools.wraps(f)
    def g(*a, **kw):
        ctx = context.get_context()
        # in_cpubound_thread is sentinel to prevent double-thread dispatch
        if (not ctx.cpu_thread_enabled or imp.lock_held()
                or getattr(ctx.thread_locals, 'in_cpubound_thread', False)):
            return f(*a, **kw)
        if not hasattr(ctx.thread_locals, 'cpu_bound_thread'):
            ctx.thread_locals.cpu_bound_thread = CPUThread()
        ml.ld3("Calling in cpu thread {0}", f.__name__)
        return context.get_context().thread_locals.cpu_bound_thread.apply(f, a, kw)
    g.no_defer = f
    return g


def cpu_bound_if(p):
    '''
    Similar to cpu_bound, but should be called with a predicate parameter which determines
    whether or not to dispatch to a cpu_bound thread.  The predicate will be passed the same
    parameters as the function itself.

    Example usage:

    # will be deferred to a thread if parameter greater than 16k, else run locally
    @async.cpu_bound_if(lambda s: len(s) > 16 * 1024)
    def my_string_function(data):
        pass
    '''
    def g(f):
        f = cpu_bound(f)

        @functools.wraps(f)
        def h(*a, **kw):
            if p(*a, **kw):
                return f(*a, **kw)
            return f.no_defer(*a, **kw)

        return h
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
from gevent.socket import socket, timeout_default
import sys

try:
    sslerror = gevent.socket.__socket__.sslerror
except AttributeError:
    class sslerror(gevent.socket.error):
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
                SSL_EX_MAP[t] = type(t.__name__, (gevent.socket.__socket__.error, t), {})
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


#TODO: convert to subclass of OpenSSL.SSL.Connection?
# then, replace the _sock attribute of an existing gevent socket with this?
# would this allow for the SockCloseWrapper to be eliminated?
class SSLSocket(gevent.socket.socket):

    def __init__(self, sock, server_side=False):
        'sock is an instance of OpenSSL.SSL.Connection'
        if server_side:  # work-around gevent.pywsgi hard-closing the underlying socket
            sock = SockCloseWrapper(sock)
        socket.__init__(self, _sock=sock)
        self._makefile_refs = 0
        self.peek_buf = ""  # buffer used to enable msg_peek
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
        ml.ld2("Accepted {0!r} {1!r}", sock, addr)
        client = SSLSocket(sock._sock, server_side=True)
        client.do_handshake()
        return client, addr

    def do_handshake(self, timeout=timeout_default):
        self._do_ssl(self._sock.do_handshake, timeout)
        # TODO: how to handle if timeout is 0.0, e.g. somebody
        # wants to use this thing in a select() loop?
        # don't worry about for now, because you'd have to be crazy
        # to put a select loop when there are all these excellent greenlets available
        peer_cert = self.get_peer_certificate()
        if peer_cert:  # may be None if no cert
            peer_cert = peer_cert.get_subject().get_components()
        context.get_context().recent['peer_certs'].append(
            (self._sock.getpeername(), time.time(), peer_cert))

    def set_renegotiate(self, timeout=timeout_default):
        '''
        Set the renegotiate flag so that the next send/recv
        or do_handshake will cause the session to be
        renegotiated.
        This allows session to be updated to reflect
        changes on SSL context (e.g. change ciphers).
        '''
        self._do_ssl(self._sock.renegotiate, timeout)

    def connect(self, *args):
        socket.connect(self, *args)
        self.do_handshake()

    def send(self, data, flags=0, timeout=timeout_default):
        if ll.get_log_level() >= ll.LOG_LEVELS['DEBUG2']:
            if hasattr(data, 'tobytes'):
                ml.ld2("SSL: {{{0}}}/FD {1}: OUTDATA: {{{2}}}",
                       id(self), self._sock.fileno(), data.tobytes())
            else:
                ml.ld2("SSL: {{{0}}}/FD {1}: OUTDATA: {{{2}}}",
                       id(self), self._sock.fileno(), data)

        # tobytes() fails on strings -- how did this ever work?
        # data is buffer on many platforms - thought it was only 2.6
        return self._do_ssl(lambda: self._sock.send(data, flags), timeout)

    def recv(self, buflen, flags=0):
        if self.peek_buf:
            if len(self.peek_buf) >= buflen:
                if flags & socket.MSG_PEEK:
                    return self.peek_buf[:buflen]
                retval, self.peek_buf = self.peek_buf[:buflen], self.peek_buf[buflen:]
                return retval
            else:
                buflen -= len(self.peek_buf)
        pending = self._sock.pending()
        if pending:
            retval = self._sock.recv(min(pending, buflen))
        else:
            retval = self._do_ssl(lambda: self._sock.recv(buflen))
        ml.ld2("SSL: {{{0}}}/FD {1}: INDATA: {{{2}}}",
               id(self), self._sock.fileno(), retval)
        if self.peek_buf:
            retval, self.peek_buf = self.peek_buf + retval, ""
        if flags and flags & socket.MSG_PEEK:
            self.peek_buf = retval
        return retval

    def read(self, buflen=1024):
        """
        NOTE: read() in SSLObject does not have the semantics of file.read
        reading here until we have buflen bytes or hit EOF is an error
        """
        return self.recv(buflen)

    def write(self, data):
        try:
            return self.sendall(data)
        except SSL.Error as ex:
            raise sslerror(str(ex))

    def makefile(self, mode='r', bufsize=-1):
        self._makefile_refs += 1
        return gevent.socket._fileobject(self, mode, bufsize, close=True)

    def shutdown(self, how, timeout=timeout_default):
        if timeout is timeout_default:
            timeout = self.timeout
        self._do_ssl(self._sock.shutdown)
        #accept how parameter for compatibility with normal sockets,
        #although OpenSSL.SSL.Connection objects do not accept how

    def close(self):
        if self._makefile_refs < 1:
            self.shutdown(gevent.socket.SHUT_RDWR)
            socket.close(self)
        else:
            self._makefile_refs -= 1

    def _do_ssl(self, func, timeout=timeout_default):
        'call some network funcion, re-handshaking if necessary'
        if timeout is timeout_default:
            timeout = self.timeout
        while True:
            try:
                ml.ld2("Calling {0} for do_ssl", func.__name__)
                return func()
            except SSL.WantReadError as ex:
                ml.ld2("SSL: {{{0}}}/FD {1}:  INDATA: Want Read", id(self), self._sock.fileno())
                if timeout == 0.0:
                    raise gevent.socket.timeout(str(ex))
                else:
                    sys.exc_clear()
                    gevent.socket.wait_read(self._sock.fileno(), timeout=timeout)
            except SSL.WantWriteError as ex:
                ml.ld2("SSL: {{{0}}}/FD {1}:  INDATA: Want Write {{}}", id(self), self._sock.fileno())
                if timeout == 0.0:
                    raise gevent.socket.timeout(str(ex))
                else:
                    sys.exc_clear()
                    gevent.socket.wait_write(self._sock.fileno(), timeout=timeout)
            except SSL.ZeroReturnError:
                ml.ld2("SSL: {{{0}}}/FD {1}:  INDATA: {{}}", id(self), self._sock.fileno())
                return ''
            except SSL.SysCallError as ex:
                ml.ld2("SSL: {{{0}}}/FD {1}: Call Exception", id(self), self._sock.fileno())
                raise sslerror(SysCallError_code_mapping.get(ex.args[0], ex.args[0]), ex.args[1])
            except SSL.Error as ex:
                ml.ld2("SSL: {{{0}}}/FD {1}: Exception", id(self), self._sock.fileno())
                raise sslerror(str(ex))


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
    except (gevent.socket.error, SSL.Error):
        pass  # just being nice to the server, don't care if it fails
    except Exception as e:
        context.get_context().cal.event("INFO", "SOCKET", "0",
                                        "unexpected error closing socket: " + repr(e))
    try:
        sock.close()
    except (gevent.socket.error, SSL.Error):
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
                        gevent.get_hub().loop.reinit()
                        PID = os.getpid()
                return fn(request)
        return wrapper


### a little helper for running a greenlet-friendly console
## implemented here since it directly references gevent

import sys
import code
import gevent.os
import gevent.fileobject


class GreenConsole(code.InteractiveConsole):
    @io_bound
    def raw_input(self, prompt=""):
        return code.InteractiveConsole.raw_input(self, prompt)


def start_repl(local=None, banner="infra REPL (exit with Ctrl+C)"):
    gevent.spawn(GreenConsole().interact, banner)


def greenify(banner="REPL is now greenlet friendly (exit with Ctrl+C)"):
    import __main__
    GreenConsole(__main__.__dict__).interact(banner)
