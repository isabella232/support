'''
This module defines a context object which holds on to all global state.
'''
import getpass
from weakref import WeakKeyDictionary
import weakref
from threading import local
from collections import namedtuple, defaultdict, deque
import socket
import faststat

import ll
ml = ll.LLogger()

#NOTE: do not import anything else from infra at context import time
#this is a bit heavy-handed, but guarantees no circular import errors
#which are otherwise very easy to create


class Context(object):
    '''
    Context object is meant to be the clearing-house for global data in an
    application written using Python Infrastructure.

    Two categories of data in here:

    1- Global data used internally by the infrastructure.

    2- Global data which would otherwise need to be kept track of by user code.
    (This stuff can be identified by the presence of getters)
    '''
    def __init__(self, dev=False, stage_host=None):
        import topos
        import gevent

        ml.ld("Allocating Context {0}",  id(self))

        self.config = None

        #UFORK STUFF
        self.num_workers = None  # used in python as num_children
                                 # read from opscfg as max_connections
        self.pid = None

        #ASYNC RELATED STUFF
        self.greenlet_ancestors = WeakKeyDictionary()
        self.greenlet_correlation_ids = WeakKeyDictionary()
        self.exception_traces = WeakKeyDictionary()
        self.thread_locals = local()
        self.cpu_thread_enabled = True

        #CAL RELATED STUFF
        import cal
        self.cal = cal.DefaultClient()
        self.greenlet_trans_stack = WeakKeyDictionary()

        #ASF RELATED STUFF
        from asf import asf_context
        self.asf_context = asf_context.ASFContext()

        #PROTECTED RELATED STUFF
        self.protected = None

        import sockpool
        self.sockpool = sockpool.SockPool()

        self.user = getpass.getuser()

        self._dev = dev
        self._debug_errors = False

        #TOPO RELATED STUFF
        self.stage_address_map = topos.StageAddressMap()
        try:
            self.topos = topos.TopoFile()
        except EnvironmentError:
            self.topos = None
        self.set_stage_host(stage_host)
        self.address_book = AddressBook([])

        #NETWORK RELATED STUFF
        self.port = None
        self.ip = "127.0.0.1"
        try:
            self.ip = socket.gethostbyname(socket.gethostname())
        except socket.error:
            for hostname, port in [("github.paypal.com", 80)]:
                try:  # TODO: more hostname / ports to connect to
                    addr = socket.gethostbyname(hostname), port
                    conn = socket.create_connection(addr)
                    self.ip = conn.getsockname()[0]
                    conn.close()
                    break
                except socket.error:
                    pass
        self._serve_ufork = None
        self._serve_daemon = None
        self.asf_server = None

        #MONITORING DATA
        self.network_exchanges_stored = 100
        self.stored_network_data = defaultdict(deque)

        self.stats = defaultdict(faststat.Stats)
        self.counts = defaultdict(int)
        self.profiler = None  # sampling profiler

        self.stopping = False
        self.sys_stats_greenlet = None
        self.monitor_interval = 0.01  # ~100x per second
        self.greenlet_settrace = True  # histogram of CPU runs

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

    def _update_addresses(self):
        if self.stage_host:
            addresses = self.stage_address_map.get_host_map(self.stage_ip)
            addresses = dict([(k, (self.stage_ip, v))
                              for k, v in addresses.items()])
            addresses.update(CAL_DEV_ADDRESSES)
        elif self.topos:
            addresses = self.topos.get(self.appname) or {}
        else:
            addresses = {}

        if self.config:
            self.address_book = AddressBook([self.config.addresses, addresses], 
                                            self.config.aliases)
        else:
            self.address_book = AddressBook([addresses])

    def get_mayfly(self, name, namespace):
        try:
            ip, port = self.address_book.mayfly_addr(name)
        except KeyError:
            raise ValueError('Unknown Mayfly: %r' % name)

        import mayfly
        return mayfly.Client(ip, port, self.appname, namespace)

    def make_occ(self, name):
        'make instead of get to indicate this is creating a stateful object'
        try:
            ip, port = self.address_book.occ_addr(name)
        except KeyError:
            raise ValueError('Unknown OCC: %r' % name)

        import occ
        return occ.Connection(ip, port, self.protected)

    def get_addr(self, name):
        return self.address_book[name]

    # TODO: go around and instrument code to call this function
    # on network send/recv
    def store_network_data(self, name, direction, data):
        q = self.stored_network_data[name]
        q.appendleft((direction, summarize(data, 4096)))
        while len(q) > self.network_exchanges_stored:
            q.pop()

    @property
    def dev(self):
        return self._dev

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
    def sockpool_enabled(self):
        import sockpool

        return isinstance(self.sockpool, sockpool.SockPool)

    @sockpool_enabled.setter
    def sockpool_enabled(self, val):
        import sockpool

        if val and not isinstance(self.sockpool, sockpool.SockPool):
            self.sockpool = sockpool.SockPool()
        elif not val and isinstance(self.sockpool, sockpool.SockPool):
            self.sockpool = sockpool.NullSockPool()

    @property
    def sampling(self):
        return self.profiler is not None

    @sampling.setter
    def sampling(self, val):
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

    @monitoring_greenlet.setter
    def monitoring_greenlet(self, val):
        import gevent
        if val not in (True, False):
            raise ValueError("sampling may only be set to True or False")
        if val and not self.sys_stats_greenlet:
            # do as I say, not as I do; using gevent.spawn instead of async.spawn
            # here to prevent circular import
            self.sys_stats_greenlet = gevent.spawn(_sys_stats_monitor)
        if not val and self.sys_stats_greenlet:
            self.sys_stats_greenlet.kill()
            self.sys_stats_greenlet = None

    def stop(self):
        '''
        Stop any concurrently running tasks (threads or greenlets)
        associated with this Context object.

        (e.g. sampling profiler thread, system monitor greenlet)
        '''
        if self.profiler:
            self.profiler.stop()
        self.stopping = True

    def __del__(self):
        self.stopping = True


def _sys_stats_monitor(context):
    import gc
    import time
    from gevent.hub import _get_hub
    from gevent import sleep

    context = weakref.ref(context)  # give gc a hand
    end = 0
    while 1:
        start = time.time()
        tmp = context()
        if tmp is None or tmp.stopping:
            return
        tmp.stats['gc.garbage'].add(len(gc.garbage))
        tmp.stats['greenlets.active'].add(_get_hub().loop.activecnt)
        tmp.stats['greenlets.pending'].add(_get_hub().loop.pendingcnt)
        try:
            tmp.stats['queues.cal.depth'].add(tmp.cal.actor.queue.qsize())
        except AttributeError:
            pass
        try:
            tmp.stats['queues.cpu_bound.depth'].add(
                tmp.thread_locals.cpu_bound_thread.task_queue.qsize())
        except AttributeError:
            pass
        try:
            tmp.stats['queues.io_bound.depth'].add(
                tmp.thread_locals.io_bound_thread.task_queue.qsize())
        except AttributeError:
            pass
        interval = tmp.monitor_interval
        end, prev = time.time(), end
        # keep a rough measure of the fraction of time spent on monitoring
        tmp.stats['monitoring.overhead'].add((end - start)/(prev - end))
        tmp.stats['monitoring.duration'].add(end - start)
        tmp = None
        sleep(interval)



# A set of *Conf classes representing the configuration of different things.
Address = namedtuple('Address', 'ip port')


class AddressBook(object):
    '''
    Responsible for everything to do with finding the ip and port for something
    at runtime.
    First, applies aliasing.
    Then, looks down address_chain for a match to a given key.
    '''
    def __init__(self, address_chain, aliases={}):
        self.address_chain = address_chain
        self.aliases = aliases

    def __getitem__(self, key):
        if key in self.aliases:
            realkey = self.aliases[key]
        else:
            realkey = key
        for addresses in self.address_chain:
            if realkey in addresses:
                return addresses[realkey]
        msg = "No address for %r" % key
        if realkey != key:
            msg += " (aliased to %r)" % realkey
        raise ValueError(msg)

    def mayfly_addr(self, key=None):
        for prefix in ("mayflydirectoryserv", "mayfly"):
            key2 = key or prefix
            if not key2.startswith(prefix):
                key2 = prefix + '-' + key2
            try:
                return self[key2]
            except ValueError:
                pass
        raise ValueError("no address for mayfly " + repr(key))

    def occ_addr(self, key=None):
        if not key:
            key = 'occ'
        if not key.startswith('occ'):
            key = 'occ-' + key
        return self[key]


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
        get_context().counts[f.__name__] += 1
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
