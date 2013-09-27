import os
import copy
import functools
import time
import imp
import traceback
import platform

from asf.asf_context import ASFError
#TODO: migrate ASFError out of ASF to a more root location
import gevent.pool
import gevent.socket
import gevent.threadpool
import gevent.greenlet

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


def cpu_bound(f):
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
        #(this happens rarely -- only if the cpu_bound function is being executed
        #at the import time of a module)
        if not ctx.cpu_thread_enabled or imp.lock_held():
            ret = in_thread(*a, **kw)
        else:
            if getattr(tlocals, 'in_cpu_thread', False):
                ret = in_thread(*a, **kw)
            else:
                if not hasattr(tlocals, 'cpu_thread'):
                    tlocals.cpu_thread = gevent.threadpool.ThreadPool(1)
                    ml.ld2("Getting new thread pool for CPU bound {0}", id(tlocals.cpu_thread))

                    def set_flag():
                        tlocals.in_cpu_thread = True
                    tlocals.cpu_thread.apply_e((Exception,), set_flag, (), {})
                ret = tlocals.cpu_thread.apply_e((Exception,), in_thread, a, kw)
                ctx.stats['cpu_bound.depth'].add(tlocals.cpu_thread.task_queue.qsize())
                ml.ld3("Enqueued to thread {0}", f.__name__)
        start = started[0]
        duration = curtime() - start
        queued = start - enqueued
        ctx.stats['cpu_bound.' + f.__name__ + '.queued(ms)'].add(queued * 1000)
        ctx.stats['cpu_bound.' + f.__name__ + '.duration(ms)'].add(duration * 1000)
        ctx.stats['cpu_bound.queued(ms)'].add(queued * 1000)
        ctx.stats['cpu_bound.duration(ms)'].add(duration * 1000)
        if hasattr(ret, '__len__') and callable(ret.__len__):
            length = ret.__len__()
            ctx.stats['cpu_bound.' + f.__name__ + '.len'].add(length)
            if duration:  # may be 0
                ctx.stats['cpu_bound.' + f.__name__ + '.rate(B/ms)'].add(length / (duration * 1000.0))
        return ret

    g.no_defer = f
    return g


if hasattr(time, "perf_counter"):
    curtime = time.perf_counter  # 3.3
elif platform.system() == "Windows":
    curtime = time.clock
else:
    curtime = time.time


def close_threadpool():
    tlocals = context.get_context().thread_locals
    if hasattr(tlocals, 'cpu_thread'):
        ml.ld2("Closing thread pool {0}", id(tlocals.cpu_thread))
        cpu_thread = tlocals.cpu_thread
        cpu_thread.join()
        cpu_thread.kill()
        del tlocals.cpu_thread
    return


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

'''
#TODO: translate SSLObject below into SSLProtectedSocket or equivalent
class SSLProtectedSocket(gevent.socket.socket):

    def __init__(self, sock, protected):
        gevent.socket.socket.__init__(self, _sock = sock)
        self._makefile_refs = 0
        if server_side:
            self._sock.set_accept_state()
        else:
            self._sock.set_connect_state()
'''

#From gevent, although gevent has deprecated it, we can use this code to enable OpenSSL context
#objects

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


class SSLObject(socket):

    def __init__(self, sock, server_side=False):
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
        client = SSLObject(sock._sock, server_side=True)
        client.do_handshake()
        return client, addr

    def do_handshake(self):
        while True:
            try:
                self._sock.do_handshake()
                break
            except SSL.WantReadError:
                sys.exc_clear()
                wait_read(self.fileno())
            except SSL.WantWriteError:
                sys.exc_clear()
                wait_write(self.fileno())
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
            except SSL.WantWriteError, ex:
                if self.timeout == 0.0:
                    raise timeout(str(ex))
                else:
                    sys.exc_clear()
                    wait_write(self.fileno(), timeout=timeout)
            except SSL.WantReadError, ex:
                if self.timeout == 0.0:
                    raise timeout(str(ex))
                else:
                    sys.exc_clear()
                    wait_read(self.fileno(), timeout=timeout)
            except SSL.SysCallError, ex:
                if ex[0] == -1 and data == "":
                    # errors when writing empty strings are expected and can be ignored
                    return 0
                raise sslerror(SysCallError_code_mapping.get(ex.args[0], ex.args[0]), ex.args[1])
            except SSL.Error, ex:
                raise sslerror(str(ex))

    def recv(self, buflen):
        pending = self._sock.pending()
        if pending:
            retval = self._sock.recv(min(pending, buflen))
            ml.ld2("SSL: {{{0}}}/FD {1}: INDATA: {{{2}}}",
                   id(self), self._sock.fileno(), retval)
            return retval
        while True:
            try:
                retval = self._sock.recv(buflen)
                ml.ld2("SSL: {{{0}}}/FD {1}: INDATA: {{{2}}}",
                       id(self), self._sock.fileno(), retval)
                return retval
            except SSL.WantReadError, ex:
                if self.timeout == 0.0:
                    raise timeout(str(ex))
                else:
                    sys.exc_clear()
                    wait_read(self.fileno(), timeout=self.timeout)
            except SSL.WantWriteError, ex:
                if self.timeout == 0.0:
                    ml.ld2("SSL: {{{0}}}/FD {1}: Timing out", id(self), self._sock.fileno())
                    raise timeout(str(ex))
                else:
                    sys.exc_clear()
                    wait_read(self.fileno(), timeout=self.timeout)
            except SSL.ZeroReturnError:
                ml.ld2("SSL: {{{0}}}/FD {1}:  INDATA: {{}}", id(self), self._sock.fileno())
                return ''
            except SSL.SysCallError, ex:
                ml.ld2("SSL: {{{0}}}/FD {1}: Call Exception", id(self), self._sock.fileno())
                raise sslerror(SysCallError_code_mapping.get(ex.args[0], ex.args[0]), ex.args[1])
            except SSL.Error, ex:
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
    ssl_sock = SSLObject(connection, server_side)
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
    ml.ld("Killing socket")
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
