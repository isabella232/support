'''
This module provides the capability to from an abstract Paypal name,
such as "paymentserv", or "occ-conf" to an open connection.

The main entry point is the ConnectionManager.get_connection().
This function will promptly either:
   1- raise an Exception which is a subclass of socket.error
   2- return a socket

ConnectionManagers provide the following services:

1- name resolution ("paymentserv" to actual ip/port from topos)
2- transient markdown (keeping track of connection failures)
3- socket throttling (keeping track of total open sockets)
4- timeouts (connection and read timeouts from opscfg)
5- protecteds

In addition, by routing all connections through ConnectionManager,
future refactorings/modifications will be easier.  For example,
fallbacks or IP multi-plexing.
'''
import socket
import time
import datetime
import collections
import weakref
import random

import gevent.socket
import gevent.ssl
import gevent

import async
import context
import protected
import sockpool
from protected import Protected
import ll

ml = ll.LLogger()

class ConnectionManager(object):

    def __init__(self, address_groups=None, address_aliases=None, ops_config=None, protected=None):
        self.sockpools = weakref.WeakKeyDictionary()  # one sockpool per protected
        self.address_groups = address_groups
        self.address_aliases = address_aliases
        self.ops_config = ops_config  # NOTE: how to update this?
        self.protected = protected
        self.server_models = ServerModelDirectory()
        # do as I say, not as I do!  we need to use gevent.spawn instead of async.spawn
        # because at the time the connection manager is constructed, the infra context
        # is not yet fully initialized
        self.culler = gevent.spawn(self.cull_loop)

    def get_connection(self, name_or_addr, ssl=False):
        '''
        name_or_addr - the logical name to connect to, e.g. "paymentreadserv" or "occ-ctoc"
        ssl - if set to True, wrap socket with context.protected;
              if set to a protected.Protected object, wrap socket with that object
        '''
        ctx = context.get_context()
        address_groups = self.address_groups or ctx.address_groups
        address_aliases = self.address_aliases or ctx.address_aliases
        ops_config = self.ops_config or ctx.ops_config
        #### POTENTIAL ISSUE: OPS CONFIG IS MORE SPECIFIC THAN ADDRESS (owch)

        if name_or_addr in address_aliases:
            name_or_addr = address_aliases[name_or_addr]

        if isinstance(name_or_addr, basestring):  # string means a name
            name = name_or_addr
            try:
                address_list = list(address_groups[name])
            except KeyError:
                raise NameNotFound("no address found for name {0}".format(name))
        else:
            address_list = [name_or_addr]
            name = ctx.opscfg_revmap.get(name_or_addr)

        if name:
            sock_config = ops_config.get_endpoint_config(name)
        else:
            sock_config = ops_config.get_endpoint_config()

        if name is None:  # default to a string-ification of ip for the name
            name = address_list[0][0].replace('.', '-')

        for pool in self.sockpools.values():
            pool.cull()

        total_num_in_use = sum([len(model.active_connections) 
                                for model in self.server_models.values()])

        if total_num_in_use >= GLOBAL_MAX_CONNECTIONS:
            ctx.intervals['net.out_of_sockets'].tick()
            raise OutOfSockets("maximum global socket limit {0} hit: {1}".format(
                GLOBAL_MAX_CONNECTIONS, total_num_in_use))

        num_in_use = sum([len(self.server_models[address].active_connections)
                          for address in address_list])

        if num_in_use >= MAX_CONNECTIONS:
            ctx.intervals['net.out_of_sockets'].tick()
            ctx.intervals['net.out_of_sockets.' + str(name)].tick()
            raise OutOfSockets("maximum sockets for {0} already in use: {1}".format(name, num_in_use))

        errors = []
        for address in address_list:
            try:
                return self._connect_to_address(name, ssl, sock_config, address)
            except socket.error as err:
                if len(address_list) == 1:
                    raise
                ml.ld("Connection err {0!r}, {1}, {2!r}", address, name, err)
                errors.append((address, err))
        raise MultiConnectFailure(errors)

    def _connect_to_address(self, name, ssl, sock_config, address):
        ctx = context.get_context()

        if address not in self.server_models:
            self.server_models[address] = ServerModel(address)
        server_model = self.server_models[address]

        if ssl:
            if ssl is True:
                protected = self.protected or ctx.protected
                if protected is None:
                    raise EnvironmentError("Unable to make protected connection to " +
                        repr(name or "unknown") + " at " + repr(address) + 
                        " with no protected loaded."
                        " (maybe you forgot to call infra.init()/infra.init_dev()?)")
            elif isinstance(ssl, Protected):
                protected = ssl
            elif ssl == PLAIN_SSL:
                protected = PLAIN_SSL_PROTECTED
        else:
            protected = NULL_PROTECTED  # something falsey and weak-refable

        if protected not in self.sockpools:
            self.sockpools[protected] = sockpool.SockPool()

        sock = self.sockpools[protected].acquire(address)
        if not sock:
            if sock_config.transient_markdown_enabled:
                last_error = server_model.last_error
                if last_error and time.time() - last_error < TRANSIENT_MARKDOWN_DURATION:
                    raise MarkedDownError()

            failed = 0
            while True:
                try:
                    ml.ld("CONNECTING...")
                    with context.get_context().cal.trans('CONNECT',
                                                         str(address[0]) + ":" + str(address[1])):
                        sock = gevent.socket.create_connection(address, sock_config.connect_timeout_ms / 1000.0)
                        ml.ld("CONNECTED local port {0!r}/FD {1}", sock.getsockname(), sock.fileno())
                    break
                except socket.error as err:
                    if False:  # TODO: how to tell if this is an unrecoverable error
                        raise
                    if failed >= sock_config.max_connect_retry:
                        server_model.last_error = time.time()
                        if sock_config.transient_markdown_enabled:
                            ctx = context.get_context()
                            ctx.intervals['net.markdowns.' + str(name) + '.' +
                                          str(address[0]) + ':' + str(address[1])].tick()
                            ctx.intervals['net.markdowns'].tick()
                            ctx.cal.event('ERROR', 'TMARKDOWN', '2', 'name=' + str(name) + '&addr=' + str(address))
                        ml.ld("Connection err {0!r}, {1}, {2!r}", address, name, err)
                        raise
                    failed += 1

            if ssl:
                if ssl == PLAIN_SSL:
                    sock = gevent.ssl.wrap_socket(sock)
                else:
                    sock = async.wrap_socket_context(sock, protected.ssl_client_context)

            sock = MonitoredSocket(sock, server_model.active_connections, protected, name)
            server_model.sock_in_use(sock)

        sock.settimeout(sock_config.response_timeout_ms / 1000.0)
        return sock

    def release_connection(self, sock):
        # check the connection for updating of SSL cert (?)
        if context.get_context().sockpool_enabled:
            self.sockpools[sock._protected].release(sock)
        else:
            async.killsock(sock)

    def cull_loop(self):
        while 1:
            for pool in self.sockpools.values():
                async.sleep(CULL_INTERVAL)
                pool.cull()
            async.sleep(CULL_INTERVAL)


CULL_INTERVAL = 1.0

# something falsey, and weak-ref-able
NULL_PROTECTED = type("NullProtected", (object,), {'__nonzero__': lambda self: False})()
# a marker for doing plain ssl with no protected
PLAIN_SSL = "PLAIN_SSL"
PLAIN_SSL_PROTECTED = type("PlainSslProtected", (object,), {})()

# TODO: better sources for this?
TRANSIENT_MARKDOWN_DURATION = 10.0  # seconds
try:
    import resource
    MAX_CONNECTIONS = int(0.8 * resource.getrlimit(resource.RLIMIT_NOFILE))
    GLOBAL_MAX_CONNECTIONS = MAX_CONNECTIONS
except:
    MAX_CONNECTIONS = 800
    GLOBAL_MAX_CONNECTIONS = 800
# At least, move these to context object for now


class ServerModelDirectory(dict):
    def __missing__(self, key):
        self[key] = ServerModel(key)
        return self[key]


class ServerModel(object):
    '''
    This class represents an estimate of the state of a given "server".
    ("Server" is defined here by whatever accepts the socket connections, which in practice
        may be an entire pool of server machines/VMS, each of which has multiple worker thread/procs)

    For example:
      * estimate how many connections are currently open
         - (note: only an estimate, since the exact server-side state of the sockets is unknown)
    '''
    def __init__(self, address):
        self.last_error = 0
        self.active_connections = {}
        self.address = address

    def sock_in_use(self, sock):
        self.active_connections[sock] = time.time()

    def __repr__(self):
        return ("<ServerModel " + repr(self.address) + " last_error=" + 
            datetime.datetime.fromtimestamp(int(self.last_error)).strftime('%Y-%m-%d %H:%M:%S') + ">")


ConnectionConfig = collections.namedtuple("connect_timeout", ("response_timeout", "retries",
    "markdown_time", "protected"))  # ?


class MonitoredSocket(object):
    '''
    A socket proxy which allows socket lifetime to be monitored.
    '''
    def __init__(self, sock, registry, protected, name=None):
        self._msock = sock
        self._registry = registry  # TODO: better name for this
        self._spawned = time.time()
        self._protected = protected
        # alias some functions through for improved performance
        #  (__getattr__ is pretty slow compared to normal attribute access)
        #self.send = sock.send
        #self.recv = sock.recv
        self.sendall = sock.sendall
        self.name = name

    def send(self, data, flags=0):
        ret = self._msock.send(data, flags)
        context.get_context().store_network_data(
            (self.name, self._msock.getpeername()), 
            "send", data)

    def sendall(self, data, flags=0):
        ret = self._msock.sendall(data, flags)
        context.get_context().store_network_data(
            (self.name, self._msock.getpeername()), 
            "sendall", data)

    def recv(self, bufsize, flags=0):
        data = self._msock.recv(bufsize, flags)
        context.get_context().store_network_data(
            (self.name, self._msock.getpeername()),
            "recv", data)
        return data

    def close(self):
        if self in self._registry:
            del self._registry[self]
        return self._msock.close()

    def shutdown(self, how):  # not going to bother tracking half-open sockets
        if self in self._registry:  # (unlikely they will ever be used)
            del self._registry[self]
        return self._msock.shutdown(how)

    def __repr__(self):
        return "<MonitoredSocket " + repr(self._msock) + ">"

    def __getattr__(self, attr):
        return getattr(self._msock, attr)

    # TODO: is there another way to handle this without a __del__?
    # Perhaps using weakrefs from the referrent side.  __del__ should
    # be avoided if possible since garbage cycles involving objects
    # with __del__ defined cannot be automatically collected
    # (see http://docs.python.org/2/library/gc.html#gc.garbage)
    def __del__(self):
        #note: this way there is no garbage printed about "KeyError in __del__"
        #empirically, even with a try/except a warning is printed out
        #if an exception happens here
        registry = getattr(self, "_registry", None)
        if registry and self in registry:
            del registry[self]


Address = collections.namedtuple('Address', 'ip port')


class AddressGroup(object):
    '''
    An address group represents the set of addresses known by a specific name
    to a client at runtime.  That is, in a specific environment (stage, live, etc), 
    an address group represents the set of <ip, port> pairs to try.

    An address group consists of tiers.
    Each tier should be fully exhausted before moving on to the next.
    (That is, tiers are "fallbacks".)
    A tier consists of prioritized addresses.
    Within a tier, the addresses should be tried in a priority weighted random order.

    The simplest way to use an address group is just to iterate over it, and try
    each address in the order returned.

    tiers: [ [(weight, (ip, port)), (weight, (ip, port)) ... ] ... ]
    '''
    def __init__(self, tiers):
        if not any(tiers):
            raise ValueError("no addresses provided for address group")
        self.tiers = tiers

    def connect_ordering(self):
        plist = []
        for tier in self.tiers:
            # Kodos says: "if you can think of a simpler way of achieving a weighted random
            # ordering, I'd like to hear it"  (http://en.wikipedia.org/wiki/Kang_and_Kodos)
            tlist = [(random.random() * e[0], e[1]) for e in tier]
            tlist.sort()
            plist.extend([e[1] for e in tlist])
        return plist

    def __iter__(self):
        return iter(self.connect_ordering())

    def __repr__(self):
        return "<AddressGroup " + repr(self.tiers) + ">"


class AddressGroupMap(dict):
    '''
    For dev mode, will lazily pull in additional addresses.
    '''
    def __missing__(self, key):
        ctx = context.get_context()
        if ctx.stage_ip and ctx.topos:
            newkey = None
            for k in (key, key + "_r1", key + "_ca", key + "_r1_ca"):
                if k in ctx.topos.apps:
                    newkey = k
                    break
            if newkey is not None:
                # TODO: maybe do r1 / r2 fallback; however, given this is stage only
                #  that use case is pretty slim
                ports = [int(ctx.topos.get_port(newkey))]
                val = AddressGroup( ([(1, (ctx.stage_ip, p)) for p in ports],) )
                self.__dict__.setdefault("warnings", {}).setdefault("inferred_addresses", [])
                self.warnings["inferred_addresses"].append((key, val))
                self[key] = val
                if key != newkey:
                    self.warnings["inferred_addresses"].append((newkey, val))
                self[newkey] = val
                return val
        self.__dict__.setdefault("errors", {}).setdefault("unknown_addresses", set())
        self.errors["unknown_addresses"].add(key)
        ctx.intervals["error.address.missing." + repr(key)].tick()
        ctx.intervals["error.address.missing"].tick()
        raise KeyError("unknown address requested " + repr(key))

_ADDRESS_SUFFIXES = ["_r" + str(i) for i in range(10)]
_ADDRESS_SUFFIXES = ("_ca",) + tuple(["_r" + str(i) for i in range(10)])


class MarkedDownError(socket.error):
    pass


class OutOfSockets(socket.error):
    pass


class NameNotFound(socket.error):
    pass


class MultiConnectFailure(socket.error):
    pass


def get_topos(name):
    return context.get_context().get_topos(name)

def get_opscfg(name):
    return context.get_context().get_opscfg(name)
