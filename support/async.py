
import os
import os.path
import sys
import functools
import time
import imp
import code
import platform
import threading
import collections
import weakref

import gevent
import gevent.pool
import gevent.socket
import gevent.threadpool
import gevent.greenlet
import faststat

from . import context

from . import ll

ml = ll.LLogger()


# TODO: investigate replacing curtime with nanotime
# (mostly used in Threadpool dispatch
if hasattr(time, "perf_counter"):
    curtime = time.perf_counter  # 3.3
elif platform.system() == "Windows":
    curtime = time.clock
else:
    curtime = time.time


class Greenlet(gevent.Greenlet):
    '''
    A subclass of gevent.Greenlet which adds additional members:

     - locals: a dict of variables that are local to the "spawn tree" of
       greenlets
     - spawner: a weak-reference back to the spawner of the
       greenlet
     - stacks: a record of the stack at which the greenlet was
       spawned, and ancestors
    '''
    def __init__(self, f, *a, **kw):
        super(Greenlet, self).__init__(f, *a, **kw)
        spawner = self.spawn_parent = weakref.proxy(gevent.getcurrent())
        if not hasattr(spawner, 'locals'):
            spawner.locals = {}
        self.locals = spawner.locals
        stack = []
        cur = sys._getframe()
        while cur:
            stack.extend((cur.f_code, cur.f_lineno))
            cur = cur.f_back
        self.stacks = (tuple(stack),) + getattr(spawner, 'stacks', ())[:10]


def get_spawntree_local(name):
    """
    Essentially provides dynamic scope lookup for programming aspects
    that cross greenlet borders. Be wary of overusing this
    functionality, as it effectively constitutes mutating global state
    which can lead to race conditions and architectural problems.
    """
    locals = getattr(gevent.getcurrent(), 'locals', None)
    if locals:
        return locals.get(name)
    return None


def set_spawntree_local(name, val):
    """
    Similar to get_spawntree_local except that it allows setting these
    values. Again, be wary of overuse.
    """
    cur = gevent.getcurrent()
    if not hasattr(cur, 'locals'):
        cur.locals = {}
    cur.locals[name] = val


def staggered_retries(run, *a, **kw):
    """
    A version of spawn that will block will it is done
    running the function, and which will call the function
    repeatedly as time progresses through the timeouts list.

    Best used for idempotent network calls (e.g. HTTP GETs).

    e.g.
    user_data = async.staggered_retries(get_data, max_results,
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
    gs = gevent.spawn(run, *a, **kw)
    gs.link_value(call_back)
    running = [gs]
    for i in range(1, len(timeouts_secs)):
        this_timeout = timeouts_secs[i] - timeouts_secs[i - 1]
        if ctx.dev:
            this_timeout = this_timeout * 5.0
        ml.ld2("Using timeout {0}", this_timeout)
        try:
            with gevent.Timeout(this_timeout):
                ready.wait()
                break
        except gevent.Timeout:
            ml.ld2("Timed out!")
            ctx.cal.event('ASYNC-STAGGER', run.__name__, '0', {'timeout': this_timeout})
            gs = gevent.spawn(run, *a, **kw)
            gs.link_value(call_back)
            running.append(gs)
    vals = [l.value for l in running if l.successful()]
    for g in running:
        g.kill()
    if vals:
        return vals[0]
    else:
        return None


def timed(f):
    '''
    wrap a function and time all of its execution calls in milliseconds
    '''
    fname = os.path.basename(f.__code__.co_filename) or '_'
    line_no = repr(f.__code__.co_firstlineno)
    name = 'timed.{0}[{1}:{2}](ms)'.format(f.__name__, fname, line_no)

    @functools.wraps(f)
    def g(*a, **kw):
        s = faststat.nanotime()
        r = f(*a, **kw)
        context.get_context().stats[name].add((faststat.nanotime() - s) / 1e6)
        return r
    return g


def _make_threadpool_dispatch_decorator(name, size):
    def dispatch(f):
        '''
        decorator to mark a function as cpu-heavy; will be executed in a
        separate thread to avoid blocking socket communication
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
            # some modules import things lazily; it is too dangerous
            # to run a function in another thread if the import lock is
            # held by the current thread (this happens rarely -- only
            # if the thread dispatched function is being executed at
            # the import time of a module)
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
            gevent.sleep(0.01)  # poll until worker thread has initialized
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
        cn = self.__class__.__name__
        return ("<%s@%s in_q:%s out_q:%s>" %
                (cn, id(self), len(self.in_q), len(self.out_q)))

    def _error(self):
        # TODO: something better, but this is darn useful for debugging
        import traceback
        traceback.print_exc()
        ctx = context.get_context()
        tl = ctx.thread_locals
        if hasattr(tl, 'cpu_bound_thread') and tl.cpu_bound_thread is self:
            del tl.cpu_bound_thread


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


# TODO: make size configurable
io_bound = _make_threadpool_dispatch_decorator('io_bound', 10)

# N.B.  In many cases fcntl could be used as an alternative method of
# achieving non-blocking file io on unix systems


def cpu_bound(f, p=None):
    '''
    Cause the decorated function to have its execution deferred to a
    separate thread to avoid blocking the IO loop in the main thread.
    Useful for wrapping encryption or serialization tasks.

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
        return ctx.thread_locals.cpu_bound_thread.apply(f, a, kw)
    g.no_defer = f
    return g


def cpu_bound_if(p):
    '''
    Similar to cpu_bound, but should be called with a predicate
    parameter which determines whether or not to dispatch to a
    cpu_bound thread.  The predicate will be passed the same
    parameters as the function itself.

    Example usage:

    # will be deferred to a thread if parameter greater than 16k,
    # else run inline
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


def killsock(sock):
    """Attempts to cleanly shutdown a socket. Regardless of cleanliness,
    ensures that upon return, the socket is fully closed, catching any
    exceptions along the way. A safe and prompt way to dispose of the
    socket, freeing system resources.
    """
    if hasattr(sock, '_sock'):
        ml.ld("Killing socket {0}/FD {1}", id(sock), sock._sock.fileno())
    else:
        ml.ld("Killing socket {0}", id(sock))
    try:
        # TODO: better ideas for how to get SHUT_RDWR constant?
        sock.shutdown(gevent.socket.SHUT_RDWR)
    except gevent.socket.error:
        pass  # just being nice to the server, don't care if it fails
    except Exception as e:
        context.get_context().cal.event("INFO", "SOCKET", "0",
                                        "unexpected error closing socket: " + repr(e))
    try:
        sock.close()
    except gevent.socket.error:
        pass  # just being nice to the server, don't care if it fails
    except Exception as e:
        context.get_context().cal.event("INFO", "SOCKET", "0",
                                        "unexpected error closing socket: " + repr(e))


PID = os.getpid()


def check_fork(fn):
        """Hack for Django/gevent interaction to reset after non-gevent fork"""
        @functools.wraps(fn)
        def wrapper(request):
                global PID
                if PID != os.getpid():
                        gevent.get_hub().loop.reinit()
                        PID = os.getpid()
                return fn(request)
        return wrapper


# a little helper for running a greenlet-friendly console
# implemented here since it directly references gevent


class GreenConsole(code.InteractiveConsole):
    @io_bound
    def raw_input(self, prompt=""):
        return code.InteractiveConsole.raw_input(self, prompt)


def start_repl(local=None, banner="infra REPL (exit with Ctrl+C)"):
    gevent.spawn(GreenConsole().interact, banner)


def greenify(banner="REPL is now greenlet friendly (exit with Ctrl+C)"):
    import __main__
    GreenConsole(__main__.__dict__).interact(banner)


# The following are imported/aliased for user import convenience
spawn = Greenlet.spawn
sleep = gevent.sleep
Timeout = gevent.Timeout
with_timeout = gevent.with_timeout
nanotime = faststat.nanotime
# end user imports/aliases
