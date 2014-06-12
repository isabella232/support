'''
This module defines a context object which holds on to all global state.
'''
import getpass
from weakref import WeakKeyDictionary
from multiprocessing import cpu_count
import weakref
from threading import local
from collections import defaultdict, deque
import socket
import os.path
import sys

import traceback
import time

import faststat

import ll
ml = ll.LLogger()

#NOTE: do not import anything else from infra at context import time
#this is a bit heavy-handed, but guarantees no circular import errors
#which are otherwise very easy to create


class Context(object):
    '''
    Context object is meant to be the clearing-house for global data in an
    application written using Python Infrastructure.  There should only be
    one Context at a time.  Access the context with infra.context.get_context().

    Two categories of data in here:

    1- Global data used internally by the infrastructure.

    2- Global data which would otherwise need to be kept track of by user code.
    (This stuff can be identified by the presence of getters)

    There are many configuration attributes.  They ALL go to a sane default,
    it is not necessary to touch them but they are available for advanced users.

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

    pid_file_path              pid file location (used for server    [appname].pid 
                               shutdown)

    bakdoor_port               the port for the TCP REPL server      port + 2 in dev, None in live
                               (None means no REPL server)

    port                       the port that infra.serve() will use  topo[appname]["bind_port"], or
                                                                    8888 if in dev and no topo
                                                                    entry found

    cal                        the current infra.cal.Client() object (appname, '127.0.0.1', 1118)
    ========================== ===================================== ==============================
    '''
    def __init__(self, dev=False, stage_host=None):
        import topos
        import gevent

        ml.ld("Initing Context {0}",  id(self))

        self.config = None

        #UFORK STUFF
        self.num_workers = max(cpu_count() - 1, 2)
        self.worker_memlimit = 2 ** 30
        # used in python as num_children
        # read from opscfg as max_connections
        self.pid = None
        self.pid_file_path = ''

        #ASYNC RELATED STUFF
        self.greenlet_ancestors = WeakKeyDictionary()
        self.greenlet_correlation_ids = WeakKeyDictionary()
        self.exception_traces = WeakKeyDictionary()
        self.thread_locals = local()
        self.cpu_thread_enabled = True

        #CAL RELATED STUFF
        import cal
        self.cal = cal.DefaultClient('python-default')
        self.greenlet_trans_stack = WeakKeyDictionary()

        # recent stuff
        self.recent = defaultdict(lambda: deque(maxlen=1024))
        self.recent['network'] = defaultdict(lambda: deque(maxlen=100))
        
        #ASF RELATED STUFF
        from asf import asf_context
        self.asf_context = asf_context.ASFContext()

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
        try:
            self.topos = topos.TopoFile(ip=self.ip)
        except EnvironmentError:
            self.topos = None
        self.set_stage_host(stage_host)
        self.address_groups = {}
        self.service_server_map = topos.ServiceServerMap()
        self.address_aliases = dict(
            [(k, v[0]) for k, v in self.service_server_map.items() if len(v) == 1])

        self.amqs = {}

        import opscfg
        self.ops_config = opscfg.DefaultConfig()
        self.opscfg_revmap = opscfg.ReverseMap()

        self._serve_ufork = None
        self._serve_daemon = None
        self._wsgi_middleware = None
        self.ssl_client_cert_optional_in_dev = True
        # whether or not dev mode servers should make client certs optional
        self.dev_service_repl_enabled = True
        self.dev_cal_print_logs = True
        # whether a greenlet REPL should be started when a server is run in dev mode
        self.asf_server = None
        self.cryptoclient_ping_time_secs = 180
        self.sockpool_enabled = True

        #MONITORING DATA
        self.stats = defaultdict(faststat.Stats)
        self.durations = defaultdict(faststat.Duration)
        self.intervals = defaultdict(faststat.Interval)
        self.markov_stats = defaultdict(faststat.Markov)
        self.profiler = None  # sampling profiler

        self.stopping = False
        self.sys_stats_greenlet = None
        self.monitor_interval = 0.1  # ~10x per second
        self.set_greenlet_trace(True)  # histogram of CPU runs
        self.set_monitoring_greenlet(True)  # monitor queue depths

        # CLIENT BEHAVIORS
        self.mayfly_client_retries = 3

    def set_stage_host(self, stage_host, stage_ip=None):
        from contrib import net

        self.stage_host = stage_host
        if stage_ip:
            self.stage_ip = stage_ip
        elif stage_host:
            self.stage_ip = net.find_host(stage_host)[0]
        else:
            self.stage_ip = None

        self._update_addresses()

    def set_config(self, config):
        self.config = config
        self._update_addresses()
        if self.appname:
            import opscfg
            self.ops_config = opscfg.OpsCfg(self.appname)

    def _update_addresses(self):
        stage_path = '/x/web/' + self.hostname.upper() + '/topo/STAGE2.default.topo'
        if self.stage_host:
            import topos
            if os.path.exists(stage_path):
                self.topos = topos.TopoFile(stage_path, ip=self.stage_ip)
            else:
                self.topos = topos.TopoFile(
                    os.path.expanduser('~/.pyinfra/topo/STAGE2.default.topo'),
                    ip=self.stage_ip)
        if self.topos:
            addresses = self.topos.get(self.appname) or {}
        else:
            addresses = {}

        import connection_mgr

        self.address_groups = connection_mgr.AddressGroupMap(
            [(name, connection_mgr.AddressGroup((((1, address),),)))
             for name, address in addresses.items()])
        # combine _r1, _r2, _r3... read backups into a single AddressGroup
        read_backups = defaultdict(list)
        for i in range(10):
            suffix = "_r" + str(i)
            for name, address in addresses.items():
                if name.endswith(suffix):
                    read_backups[name[:-3]].append( ((1, address),) )
        for key, value in read_backups.items():
            self.address_groups[key] = connection_mgr.AddressGroup(value)

    def get_mayfly(self, name, namespace):
        name2 = None
        if name in self.address_groups:
            name2 = name
        else:
            for prefix in ("mayflydirectoryserv", "mayfly"):
                if not name.startswith(prefix):
                    name2 = prefix + "-" + name
                    if name2 in self.address_groups:
                        break
        if name2:
            import mayfly
            return mayfly.Client(name2, self.appname, namespace)
        else:
            raise ValueError('Unknown Mayfly: %r' % name)

    def get_warnings(self):
        return _find_warnings(self)

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

    def get_feel(self):
        if not hasattr(self, "_feel"):
            import feel
            self._feel = feel.LAR()
            feel_addr = self._feel.lar.conn.address
            self.cal.event("MSG", "INIT", '0', "server=%r" % feel_addr)
        return self._feel

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
        return "pyinfra"

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
        import gevent
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
        if self.profiler:
            self.profiler.stop()
        if self.server_group:
            self.server_group.stop()
        if self.cal:
            self.cal.close()

    @property
    def greenlet_settrace(self):
        'check if any greenlet trace function is registered'
        import greenlet
        return bool(greenlet.gettrace())

    def set_greenlet_trace(self, value):
        'turn on tracking of greenlet switches'
        if value not in (True, False):
            raise ValueError("value must be True or False")
        if value is False:
            try:
                greenlet.settrace(None)
            except AttributeError:
                pass  # oh well
        import greenlet
        import gevent
        import threading
        from async import curtime

        last_time = [curtime()]

        def trace(why, gs):
            if why:
                ct = curtime()
                the_time = (ct - last_time[0]) * 1000.0
                last_time[0] = ct
                if gs[0] is gevent.hub.get_hub():
                    self.stats['greenlet_idle(ms)'].add(the_time)
                else:
                    self.stats['greenlet_switch(ms)'].add(the_time)
                    if the_time > 150:
                        ml.ld("Long spin {0}", the_time)
                        if self.cal:
                            self.cal.event("GEVENT", "LONG_SPIN", '1',
                                           "time={0}&"  # note continues
                                           "slow_green={1}".format(the_time,
                                                                   traceback.format_stack(gs[0].gr_frame)))

                ml.ld4("{1} {0}", why, the_time)

        self._trace = trace

    def get_connection(self, *a, **kw):
        return self.connection_mgr.get_connection(*a, **kw)

    def __del__(self):
        self.stopping = True


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
            tmp.stats['queues.cal.depth'].add(tmp.cal.actor.queue._qsize())
        except AttributeError:
            pass
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


def _find_warnings(root, max_depth=6, _cur_depth=1, _sofar=None):
    '''
    recursively walk attributes and items to find all warnings attributes
    '''
    if _sofar is None:
        _sofar = {}
    warnings = {}

    if id(root) in _sofar:
        return _sofar[id(root)]

    children = {}
    try:
        if hasattr(root, "__dict__"):
            children.update(root.__dict__)
    except:
        pass
        #import traceback; traceback.print_exc()
    try:
        if hasattr(root, "items") and callable(root.items):
            children.update(root)
    except:
        pass
        #import traceback; traceback.print_exc()

    if _cur_depth <= max_depth:
        for key, val in children.items():
            sub_warnings = _find_warnings(val, max_depth, _cur_depth + 1, _sofar)
            if sub_warnings:
                warnings[key] = sub_warnings

    if hasattr(root, "warnings") and root.warnings:
        warnings['warnings'] = root.warnings

    if hasattr(root, "errors") and root.errors:
        warnings['errors'] = root.errors

    _sofar[id(root)] = warnings
    return warnings


CONTEXT = None


def get_context():
    global CONTEXT
    if CONTEXT is None:
        CONTEXT = Context()
    return CONTEXT


def set_context(context):
    global CONTEXT
    CONTEXT = context


def counted(f):
    @functools.wraps(f)
    def g(*a, **kw):
        get_context().intervals['decorator.' + f.__name__] += 1
        return f(*a, **kw)
    return g


# see: https://confluence.paypal.com/cnfl/display/CAL/CAL+Quick+Links
CAL_DEV_ADDRESSES = {
    'cal-stage': ('10.57.2.159', 1118),  # cal-stage.qa.paypal.com
    'cal-qa': ('10.57.2.152', 1118),  # cal-qa.qa.paypal.com
    'cal-dev': ('10.57.2.157', 1118)  # cal-dev.qa.paypal.com
}


def summarize(data, size=64):
    data = repr(data)
    if len(data) < size:
        return data
    return data[:size / 2] + '"...({0} more bytes)..."'.format(len(data) - size) + data[-size / 2:]
