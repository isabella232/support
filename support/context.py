'''
This module defines a context object which holds on to all global state.
'''

import os.path
import sys
import time
import socket
import getpass
import weakref
import linecache
import threading
from weakref import WeakKeyDictionary
from collections import defaultdict, deque
from multiprocessing import cpu_count

import gevent
import greenlet
import faststat
import hyperloglog.hll

import ll
ml = ll.LLogger()

from support import log
from support import cache

#NOTE: do not import anything else from support at context import time
#this is a bit heavy-handed, but guarantees no circular import errors
#which are otherwise very easy to create


CONTEXT = None


def get_context():
    global CONTEXT
    if CONTEXT is None:
        CONTEXT = Context()
    return CONTEXT


def set_context(context):
    global CONTEXT
    CONTEXT = context


class Context(object):
    '''
    Context object is meant to be the clearinghouse for global data in
    an application written using SuPPort.  There is only one active
    Context at a time.  Access the context with
    infra.context.get_context().

    Two categories of data in here:

      1. Global data used internally by the infrastructure.

      2. Global data which would otherwise need to be kept track of by
      user code. (This stuff can be identified by the presence of
      getters)

    There are many configuration attributes. They ALL go to a sane
    default, it is not necessary to touch them but they are available
    for advanced users.

    ========================== ===================================== ==============================
    attribute                  description                           default
    ========================== ===================================== ==============================
    num_workers                number of pre-forked worker processes cpu_count-1, minimum 2

    worker_memlimit            maximum amount of RAM used by process 1 GiB
                               before worker suicide

    max_concurrent_clients     maximum number of client connections  1000
                               to spawn greenlets for before pausing
                               socket accept

    datacenter_connect_timeout max timeout for connecting to         0.05 (=50ms)
                               internal servers (that is servers
                               inside the EBay/PayPal data center)
                               in seconds

    process_group_file_path    pid file location (used for server    [appname].pid
                               shutdown)

    bakdoor_port               the port for the TCP REPL server      port + 2 in dev, None in live
                               (None means no REPL server)

    port                       the port that infra.serve() will use  topo[appname]["bind_port"], or
                                                                    8888 if in dev and no topo
                                                                    entry found

    accept_queue_maxlen        the depth of the user-space accept
                               queue.  If the queue exceeds this
                               length, connections will be closed.   128
    ========================== ===================================== ==============================

    '''
    def __init__(self, dev=False, stage_host=None):
        ml.ld("Initing Context {0}",  id(self))

        self.config = None

        #UFORK STUFF
        self.num_workers = max(cpu_count() - 1, 2)
        self.worker_memlimit = 2 ** 30
        # used in python as num_children
        # read from opscfg as max_connections
        self.pid = None
        self.process_group_file_path = ''

        #ASYNC RELATED STUFF
        self.greenlet_ancestors = WeakKeyDictionary()
        self.greenlet_correlation_ids = WeakKeyDictionary()
        self.exception_traces = WeakKeyDictionary()
        self.thread_locals = threading.local()
        self.cpu_thread_enabled = True

        self.cal = None  # TODO
        self.log = log.LoggingContext()
        self.greenlet_trans_stack = WeakKeyDictionary()

        # recent stuff
        self.recent = cache.DefaultLRU(4096, lambda: deque(maxlen=1024))
        self.recent['network'] = cache.DefaultLRU(512,
                                                  lambda: deque(maxlen=100))

        #PROTECTED RELATED STUFF
        self.protected = None

        import connection_mgr
        self.connection_mgr = connection_mgr.ConnectionManager()

        self.user = getpass.getuser()

        self._dev = dev
        self._debug_errors = False
        self.start_browser = False
        # whether to start a browser pointed at meta on server startup

        #NETWORK RELATED STUFF
        self.max_concurrent_clients = 1000
        if dev:
            self.datacenter_connect_timeout = 1.0  # for stages
        else:
            self.datacenter_connect_timeout = 0.05
        self.client_sockets = WeakKeyDictionary()
        self.server_group = None
        self.port = None
        self.admin_port = None
        self.backdoor_port = None
        self.ip = "127.0.0.1"
        self.hostname = socket.gethostname()
        self.fqdn = socket.getfqdn()
        self.name_cache = {}
        try:
            self.ip = socket.gethostbyname(self.hostname)
        except socket.error:
            try:
                self.ip = get_ip_from_hosts()
            except:
                for hostname, port in [("github.paypal.com", 80)]:
                    try:  # TODO: more hostname / ports to connect to
                        addr = socket.gethostbyname(hostname), port
                        conn = socket.create_connection(addr)
                        self.ip = conn.getsockname()[0]
                        conn.close()
                        break
                    except socket.error:
                        pass

        #TOPO RELATED STUFF
        self.topos = None  # TODO

        self.amqs = {}
        self.fpti_client = None  # set from contrib ?

        self.ops_config = None  # TODO
        self.opscfg_revmap = {}
        self.address_aliases = {}

        self._serve_ufork = None
        self._serve_daemon = None
        self._wsgi_middleware = None
        # whether or not dev mode servers should make client certs optional
        self.ssl_client_cert_optional_in_dev = True

        self.dev_service_repl_enabled = False
        self.dev_cal_print_logs = True
        self.dev_use_reloader = False
        # whether a greenlet REPL should be started when a server is run in dev mode
        self.asf_server = None
        self.cryptoclient_ping_time_secs = 180
        self.sockpool_enabled = True

        # print on bad log msgs or not
        # (If bad due to load, sholdn't print
        # if bad fmt string, should print
        self.log_failure_print = True  # set to false in post fork

        #MONITORING DATA
        self.stats = defaultdict(faststat.Stats)
        self.durations = defaultdict(faststat.Duration)
        self.intervals = defaultdict(faststat.Interval)
        self.markov_stats = defaultdict(faststat.Markov)
        self.volatile_stats = cache.DefaultLRU(2048, faststat.Stats)
        self.sketches = defaultdict(StreamSketch)
        self.profiler = None  # sampling profiler

        self.stopping = False
        self.sys_stats_greenlet = None
        self.monitor_interval = 0.1  # ~10x per second
        self.set_greenlet_trace(True)  # histogram of CPU runs
        self.set_monitoring_greenlet(True)  # monitor queue depths

        # CLIENT BEHAVIORS
        self.mayfly_client_retries = 3

        # Are we up yet as a server?
        self.running = False

        self.accept_queue_maxlen = 128

    def disable_recent_cache(self):
        '''
        Disable caching of recent outgoing network requests.
        This will help to keep memory footprint small in applications where
        that is important.
        '''
        self.recent = cache.DefaultEmptyCache(lambda: deque(maxlen=1))
        self.recent['network'] = cache.DefaultEmptyCache(lambda: deque(maxlen=1))

    def set_stage_host(self, stage_host, stage_ip=None):
        self.stage_host = None
        self.stage_ip = None  # net.find_host(stage_host)[0]  # TODO
        self._update_addresses()

    def set_config(self, config):
        self.config = config
        self._update_addresses()
        if self.appname:
            self.ops_config = None  # TODO

    def _update_addresses(self):
        import connection_mgr

        if self.topos:
            addresses = self.topos.get(self.appname) or {}
        else:
            addresses = {}

        self.address_groups = connection_mgr.AddressGroupMap(
            [(name, connection_mgr.AddressGroup((((1, address),),)))
             for name, address in addresses.items()])

    # empirically tested to take ~ 2 microseconds;
    # keep an eye to make sure this can't blow up
    def store_network_data(self, name, fd, direction, data):
        q = self.recent['network'][name]
        q.appendleft((fd, direction, time.time(), summarize(data, 4096)))
        if ll.get_log_level() >= ll.LOG_LEVELS['DEBUG2']:
            if hasattr(data, 'tobytes'):
                data = data.tobytes()
            ml.ld2("Network/SSL: Endpoint: {0}/FD {1}: {2}DATA: {{{3}}}",
                   name, fd, direction, data)

    @property
    def dev(self):
        return self._dev

    @property
    def port(self):
        if self._port is not None:
            return self._port
        if self.topos and self.topos.get(self.appname):
            app = self.topos.get(self.appname)
            if 'bind_port' in app.addresses:
                return int(app['bind_port'])
        if self.dev:
            return 8888
        return None

    @port.setter
    def port(self, val):
        self._port = val

    @property
    def admin_port(self):
        if self._admin_port is not None:
            return self._admin_port
        for topo_key in ['admin_ssl_connector_port', 'admin_connector_port']:
            if (self.topos and self.topos.get(self.appname) and
                    topo_key in self.topos.get(self.appname)):
                if int(self.topos.get(self.appname)[topo_key]) != self._port:
                    return int(self.topos.get(self.appname)[topo_key])
        if self.dev:
            if self.port is not None:
                return self.port + 1
            return 8889
        return None

    @admin_port.setter
    def admin_port(self, val):
        self._admin_port = val

    @property
    def backdoor_port(self):
        if self._backdoor_port is not None:
            return self._backdoor_port
        # TODO: should this come out of topos?
        if self.dev:
            if self.port is not None:
                return self.port + 2
            return 8890
        return None

    @backdoor_port.setter
    def backdoor_port(self, val):
        self._backdoor_port = val

    @property
    def debug_errors(self):
        return self._debug_errors

    @debug_errors.setter
    def debug_errors(self, val):
        if val:
            if not self.dev or self.serve_ufork:
                raise ValueError("_debug_errors may only be True"
                                 "if dev is True and serve_ufork is False")
        self._debug_errors = val

    @property
    def appname(self):
        if self.config:
            return self.config.appname
        return "support"

    #TODO: serve_ufork and serve_daemon should really be Config, not Context
    @property
    def serve_ufork(self):
        if self._serve_ufork is None:
            return not self.dev
        return self._serve_ufork

    @serve_ufork.setter
    def serve_ufork(self, val):
        if not val:
            self.debug_errors = False
        self._serve_ufork = val

    @serve_ufork.deleter
    def serve_ufork(self):
        self._serve_ufork = None

    @property
    def serve_daemon(self):
        if self._serve_daemon is None:
            return not self.dev
        return self._serve_daemon

    @serve_daemon.setter
    def serve_daemon(self, val):
        self._serve_daemon = val

    @serve_daemon.deleter
    def serve_daemon(self):
        self._serve_daemon = None

    def set_default_timeout(self, secs):
        import opscfg
        opscfg.DEFAULT_CONNECT_INFO = opscfg.DEFAULT_CONNECT_INFO._replace(
            response_timeout_ms=1000 * secs)

    @property
    def sampling(self):
        return self.profiler is not None

    def set_sampling(self, val):
        from sampro import sampro
        if val not in (True, False):
            raise ValueError("sampling may only be set to True or False")
        if val and not self.profiler:
            self.profiler = sampro.Sampler()
            self.profiler.start()
        if not val and self.profiler:
            self.profiler.stop()
            self.profiler = None

    @property
    def monitoring_greenlet(self):
        return self.sys_stats_greenlet is not None

    def set_monitoring_greenlet(self, val):
        if val not in (True, False):
            raise ValueError("sampling may only be set to True or False")
        if val and not self.sys_stats_greenlet:
            # do as I say, not as I do; using gevent.spawn instead of async.spawn
            # here to prevent circular import
            self.sys_stats_greenlet = gevent.spawn(_sys_stats_monitor, self)
        if not val and self.sys_stats_greenlet:
            self.sys_stats_greenlet.kill()
            self.sys_stats_greenlet = None

    def stop(self):
        '''
        Stop any concurrently running tasks (threads or greenlets)
        associated with this Context object.

        (e.g. sampling profiler thread, system monitor greenlet)
        '''
        self.stopping = True
        self.tracing = False
        if self.profiler:
            self.profiler.stop()
        if self.server_group:
            self.server_group.stop()

    @property
    def greenlet_settrace(self):
        'check if any greenlet trace function is registered'
        return bool(greenlet.gettrace())

    def set_greenlet_trace(self, value):
        'turn on tracking of greenlet switches'
        if value not in (True, False):
            raise ValueError("value must be True or False")
        if value and not getattr(self, "tracing", False):
            self.tracing = True
            self.thread_spin_monitor = _ThreadSpinMonitor(self)
        if value is False:
            self.tracing = False
            self.thread_spin_monitor = None
            try:
                greenlet.settrace(None)
            except AttributeError:
                pass  # not the end of the world

    def get_connection(self, *a, **kw):
        return self.connection_mgr.get_connection(*a, **kw)

    def __del__(self):
        self.stopping = True


class _ThreadSpinMonitor(object):
    # just a little thingie to prevent multiple instances leaking threads;
    # note this is inherently a global class since it uses greenlet.settrace()
    MAIN_INSTANCE = None

    def __init__(self, ctx):
        _ThreadSpinMonitor.MAIN_INSTANCE = self
        self.main_thread_id = threading.current_thread().ident
        self.cur_pid = os.getpid()
        self.last_spin = 0
        self.ctx = ctx
        self.spin_count = 0
        self.last_cal_log = 0  # limit how often CAL logs
        self.stopping = False
        greenlet.settrace(self._greenlet_spin_trace)
        self._start_thread()

    def _start_thread(self):
        self.long_spin_thread = threading.Thread(
            target=self._thread_spin_monitor)
        self.long_spin_thread.daemon = True
        self.long_spin_thread.start()

    def _greenlet_spin_trace(self, why, gs):
        self.spin_count += 1
        if self.ctx.running and why:
            if self.cur_pid != os.getpid():
                self._start_thread()
                self.cur_pid = os.getpid()
            lt = self.last_spin
            ct = faststat.nanotime()
            self.last_spin = ct
            if not lt:
                return
            the_time = (ct - lt) * 1e6
            if gs[0] is gevent.hub.get_hub():
                self.ctx.stats['greenlet_idle(ms)'].add(the_time)
            else:
                self.ctx.stats['greenlet_switch(ms)'].add(the_time)
            ml.ld4("{1} {0}", why, the_time)

    def _thread_spin_monitor(self):
        while 1:
            time.sleep(0.05)
            if not self.ctx.tracing or self is not self.MAIN_INSTANCE:
                return
            if not self.last_spin:
                continue
            ct = faststat.nanotime()
            dur = ct - self.last_spin
            # if time is greater than 150 ms
            if dur > 150e6 and time.time() - self.last_cal_log > 1:
                tid = self.main_thread_id
                stack = _format_stack(sys._current_frames()[tid])
                self.ctx.log.info('LONG_SPIN').failure(time=dur/1e6,
                                                       slow_green=stack)
                self.last_cal_log = time.time()


# work around traceback.format_stack() linecache KeyError
def _format_stack(frame, maxlen=1024 * 3):
    frames = []
    while frame:
        frames.append(frame)
        frame = frame.f_back
    frames.reverse()
    stack = []
    for frame in frames:
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
        name = frame.f_code.co_name
        stack.append('  File "{0}", line {1}, in {2}\n'.format(
            filename, lineno, name))
        try:
            linecache.checkcache(filename)
            line = linecache.getline(filename, lineno, frame.f_globals)
        except KeyError:
            pass
        else:
            stack.append('    {0}\n'.format(line.strip()))
    trace = ''.join(stack)
    if trace < maxlen:
        return trace
    short = "({0} truncated bytes)".format(len(trace) - maxlen)
    return short + trace[:maxlen]


class StreamSketch(object):
    '''
    Tracking useful attributes of a data stream.
    (e.g. cardinality, total count)
    '''
    def __init__(self):
        self.hll = hyperloglog.hll.HyperLogLog(0.05)
        self.lossy_counting = StreamSketch.LossyCounting()
        self.n = 0

    def add(self, data):
        self.hll.add(data)
        self.n += 1
        self.lossy_counting.add(data)

    def card(self):
        return self.hll.card()

    def heavy_hitters(self):
        return dict(self.lossy_counting.d)

    class LossyCounting(object):
        '''
        Implements the "lossy counting" algorithm from
        "Approximate Frequency Counts over Data Streams" by Manku & Motwani

        Experimentally run-time is between 1-3 microseconds on core i7
        '''
        def __init__(self, epsilon=0.001):
            self.n = 0
            self.d = {}  # {key : (count, error)}
            self.b_current = 1
            self.w = int(1 / epsilon)

        def add(self, data):
            self.n += 1
            if data in self.d:
                self.d[data][0] += 1
            else:
                self.d[data] = [1, self.b_current - 1]

            if self.n % self.w == 0:
                self.d = dict([(k, v) for k, v in self.d.items()
                               if sum(v) > self.b_current])
                self.b_current += 1


def _sys_stats_monitor(context):
    import gc
    from gevent.hub import _get_hub
    from gevent import sleep

    context = weakref.ref(context)  # give gc a hand
    end = faststat.nanotime()  # current time throws off duration stats less than 0
    while 1:
        start = faststat.nanotime()
        tmp = context()
        if tmp is None or tmp.stopping:
            return
        # tmp.stats['gc.garbage'].add(len(gc.garbage))
        # NOTE: gc.garbage() only does something if gc module has debug flag set
        counts = gc.get_count()
        for i in range(len(counts)):
            tmp.stats['gc.count' + str(i)].add(counts[i])
        tmp.stats['greenlets.active'].add(_get_hub().loop.activecnt)
        tmp.stats['greenlets.pending'].add(_get_hub().loop.pendingcnt)
        try:
            tmp.stats['queues.cpu_bound.depth'].add(
                len(tmp.thread_locals.cpu_bound_thread.in_q))
        except AttributeError:
            pass
        try:
            tmp.stats['queues.io_bound.depth'].add(
                tmp.thread_locals.io_bound_thread.task_queue._qsize())
        except AttributeError:
            pass
        interval = tmp.monitor_interval
        end, prev = faststat.nanotime(), end
        # keep a rough measure of the fraction of time spent on monitoring
        if prev == end:
            tmp.stats['monitoring.overhead'].add(0)
        else:
            tmp.stats['monitoring.overhead'].add((end - start)/(end - prev))
        tmp.durations['monitoring.duration'].end(start)
        tmp = None
        sleep(interval)


def get_ip_from_hosts():
    '''
    get the current ip from the hosts file, without doing any DNS;
    available as a fallback
    '''
    import platform
    hostname = platform.node()
    with open('/etc/hosts') as hosts:
        for line in hosts:
            if hostname in line:
                return line.split()[0]


def counted(f):
    @functools.wraps(f)
    def g(*a, **kw):
        get_context().intervals['decorator.' + f.__name__] += 1
        return f(*a, **kw)
    return g


def summarize(data, size=64):
    data = repr(data)
    if len(data) < size:
        return data
    return data[:size / 2] + '"...({0} more bytes)..."'.format(len(data) - size) + data[-size / 2:]
