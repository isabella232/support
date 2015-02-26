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
import gevent.resolver_thread
import gevent

import async
import context
import socket_pool

import ll

ml = ll.LLogger()


Address = collections.namedtuple('Address', 'ip port')


class SSLContext(object):  # TODO
    pass


KNOWN_KEYS = ("connect_timeout_ms", "response_timeout_ms", "max_connect_retry",
              "transient_markdown_enabled", "markdown")
ConnectInfo = collections.namedtuple("ConnectInfo", KNOWN_KEYS)
DEFAULT_CONNECT_INFO = ConnectInfo(5000, 30000, 1, False, False)


class ConnectionManager(object):

    def __init__(self,
                 address_groups=None,
                 address_aliases=None,
                 ssl_context=None):
        self.sockpools = weakref.WeakKeyDictionary()  # one socket pool per ssl
        # self.sockpools = {weakref(ssl_ctx): {socket_type: [list of sockets]}}
        self.address_groups = address_groups
        self.address_aliases = address_aliases
        self.ssl_context = ssl_context
        self.server_models = ServerModelDirectory()
        # map of user-level socket objects to MonitoredSocket instances
        self.user_socket_map = weakref.WeakKeyDictionary()
        # we need to use gevent.spawn instead of async.spawn because
        # at the time the connection manager is constructed, the support
        # context is not yet fully initialized
        self.culler = gevent.spawn(self.cull_loop)

    def get_connection(
            self, name_or_addr, ssl=False, sock_type=None, read_timeout=None):
        '''
        name_or_addr - the logical name to connect to, e.g. "db-r"
        ssl - if set to True, wrap socket with context.protected;
              if set to an SSL context, wrap socket with that
        sock_type - a type to wrap the socket in; the intention here is for protocols
              that want to run asynchronous keep-alives, or higher level handshaking
              (strictly speaking, this is just a callable which accepts socket and
               returns the thing that should be pooled, but for must uses it will
               probably be a class)
        '''
        ctx = context.get_context()
        address_aliases = self.address_aliases or ctx.address_aliases
        #ops_config = self.ops_config or ctx.ops_config
        #### POTENTIAL ISSUE: OPS CONFIG IS MORE SPECIFIC THAN ADDRESS (owch)
        if isinstance(gevent.get_hub().resolver, gevent.resolver_thread.Resolver):
            gevent.get_hub().resolver = _Resolver()  # avoid pointless thread dispatches

        if name_or_addr in address_aliases:
            name_or_addr = address_aliases[name_or_addr]

        if isinstance(name_or_addr, basestring):  # string means a name
            name = name_or_addr
            address_list = self.get_all_addrs(name)
        else:
            address_list = [name_or_addr]
            # default to a string-ification of ip for the name
            name = address_list[0][0].replace('.', '-')

        #if name:
        #    sock_config = ops_config.get_endpoint_config(name)
        #else:
        #    sock_config = ops_config.get_endpoint_config()
        sock_config = DEFAULT_CONNECT_INFO


        # ensure all DNS resolution is completed; past this point
        # everything is in terms of ips
        def get_gai(e):
            name = e[0].replace(".","-")
            with ctx.log.get_logger('DNS').info(name) as _log:
                gai = gevent.socket.getaddrinfo(*e, family=gevent.socket.AF_INET)[0][4]
            context.get_context().name_cache[e] = (time.time(), gai)
            return gai

        def cache_gai(e):
            if context.get_context().name_cache.has_key(e):
                age, value = context.get_context().name_cache[e]
                if time.time() - age > 600:
                    async.spawn(get_gai, e)
                return value
            else:
                return get_gai(e)

        with ctx.log.get_logger('DNS.CACHE').info(name) as _log:
            _log['len'] = len(address_list)
            address_list = [cache_gai(e) for e in address_list]

        with ctx.log.get_logger('COMPACT').info(name):
            self._compact(address_list, name)

        errors = []
        for address in address_list:
            try:
                log_name = '%s:%s' % (name, address[0])
                with ctx.log.get_logger('CONNECT').info(log_name) as _log:
                    s = self._connect_to_address(
                        name, ssl, sock_config, address, sock_type, read_timeout)
                    if hasattr(s, 'getsockname'):
                        _log["lport"] = s.getsockname()[1]
                    elif hasattr(s, '_sock'):
                        _log["lport"] = s._sock.getsockname()[1]
                    return s
            except socket.error as err:
                if len(address_list) == 1:
                    raise
                ml.ld("Connection err {0!r}, {1}, {2!r}", address, name, err)
                errors.append((address, err))
        raise MultiConnectFailure(errors)

    def get_all_addrs(self, name):
        '''
        returns the all addresses which the logical name would resolve to,
        or raises NameNotFound if there is no known address for the given name
        '''
        ctx = context.get_context()
        address_groups = self.address_groups or ctx.address_groups
        try:
            address_list = list(address_groups[name])
        except KeyError:
            err_str = "no address found for name {0}".format(name)
            if ctx.stage_ip is None:
                err_str += " (no stage communication configured; did you forget?)"
            raise NameNotFound(err_str)
        return address_list

    def get_addr(self, name):
        '''
        returns the first address which the logical name would resolve to,
        equivalent to get_all_addrs(name)[0]
        '''
        return self.get_all_addrs(name)[0]

    def _connect_to_address(
            self, name, ssl, sock_config, address, sock_type, read_timeout):
        '''
        internal helper function that does all the complex bits of establishing
        a connection, keeping statistics on connections, handling markdowns
        '''
        ctx = context.get_context()
        if address not in self.server_models:
            self.server_models[address] = ServerModel(address)
        server_model = self.server_models[address]

        if ssl:
            if ssl is True:
                ssl_context = self.ssl_context or ctx.ssl_context
                if ssl_context is None:
                    raise EnvironmentError("Unable to make protected connection to " +
                                           repr(name or "unknown") + " at " + repr(address) +
                                           " with no SSLContext loaded.")
            elif isinstance(ssl, SSLContext):
                protected = ssl
            elif ssl == PLAIN_SSL:
                protected = PLAIN_SSL_PROTECTED
        else:
            protected = NULL_PROTECTED  # something falsey and weak-refable

        if protected not in self.sockpools:
            self.sockpools[protected] = {}
        if sock_type not in self.sockpools[protected]:
            idle_timeout = getattr(sock_type, "idle_timeout", 0.25)
            self.sockpools[protected][sock_type] = socket_pool.SocketPool(timeout=idle_timeout)

        sock = self.sockpools[protected][sock_type].acquire(address)
        msock = None
        new_sock = False
        if not sock:
            if sock_config.transient_markdown_enabled:
                last_error = server_model.last_error
                if last_error and time.time() - last_error < TRANSIENT_MARKDOWN_DURATION:
                    raise MarkedDownError()

            failed = 0
            sock_state = None
            # is the connection within the data-center?
            # use tighter timeouts if so; using the presence of a
            # protected connection as a rough heuristic for now
            internal = (ssl and ssl != PLAIN_SSL) or 'mayfly' in name
            new_sock = False
            while True:
                try:
                    ml.ld("CONNECTING...")
                    sock_state = ctx.markov_stats['socket.state.' + str(address)].make_transitor('connecting')
                    log_name = str(address[0]) + ":" + str(address[1])
                    with ctx.log.get_logger('CONNECT.TCP').info(log_name) as _log:
                        timeout = sock_config.connect_timeout_ms / 1000.0
                        if internal:  # connect timeout of 50ms inside the data center
                            timeout = min(timeout, ctx.datacenter_connect_timeout)
                        sock = gevent.socket.create_connection(address, timeout)
                        _log['timeout'] = timeout
                        sock_state.transition('connected')
                        new_sock = True
                        ml.ld("CONNECTED local port {0!r}/FD {1}", sock.getsockname(), sock.fileno())
                    if ssl:  # TODO: how should SSL failures interact with markdown & connect count?
                        sock_state.transition('ssl_handshaking')
                        with ctx.log.get_logger('CONNECT.SSL').info(log_name) as _log:
                            if ssl == PLAIN_SSL:
                                sock = gevent.ssl.wrap_socket(sock)
                            else:
                                sock = async.wrap_socket_context(sock, protected.ssl_client_context)
                        sock_state.transition('ssl_established')
                    break
                except socket.error as err:
                    if sock_state:
                        sock_state.transition('closed_error')
                    if failed >= sock_config.max_connect_retry:
                        server_model.last_error = time.time()
                        if sock_config.transient_markdown_enabled:
                            ctx = context.get_context()
                            ctx.intervals['net.markdowns.' + str(name) + '.' +
                                          str(address[0]) + ':' + str(address[1])].tick()
                            ctx.intervals['net.markdowns'].tick()
                            ctx.log.get_logger('error').critical('TMARKDOWN').error(name=str(name),
                                                                                    addr=str(address))
                            # was event: ('ERROR', 'TMARKDOWN', '2', 'name=' + str(name) + '&addr=' + str(address))
                        ml.ld("Connection err {0!r}, {1}, {2!r}", address, name, err)
                        raise
                    failed += 1

            msock = MonitoredSocket(sock, server_model.active_connections, protected,
                                    name, sock_type, sock_state)
            server_model.sock_in_use(msock)

            if sock_type:
                if getattr(sock_type, "wants_protected", False):
                    sock = sock_type(msock, protected)
                else:
                    sock = sock_type(msock)
            else:
                sock = msock
        if read_timeout is None:
            sock.settimeout(sock_config.response_timeout_ms / 1000.0)
        else:
            sock.settimeout(read_timeout)
        if msock and sock is not msock:
            # if sock == msock, collection will not work
            self.user_socket_map[sock] = weakref.proxy(msock)
        self.user_socket_map.get(sock, sock).state.transition('in_use')
        sock.new_sock = new_sock
        return sock

    def release_connection(self, sock):
        # fetch MonitoredSocket
        msock = self.user_socket_map.get(sock, sock)
        # check the connection for updating of SSL cert (?)
        msock.state.transition('pooled')
        if context.get_context().sockpool_enabled:
            self.sockpools[msock._protected][msock._type].release(sock)
        else:
            async.killsock(sock)

    def cull_loop(self):
        while 1:
            for pool in sum([e.values() for e in self.sockpools.values()], []):
                async.sleep(CULL_INTERVAL)
                pool.cull()
            async.sleep(CULL_INTERVAL)

    def _compact(self, address_list, name):
        '''
        try to compact and make room for a new socket connection to one of
        address_list raises OutOfSockets() if unable to make room
        '''
        ctx = context.get_context()
        sock_log = ctx.log.get_logger('NET.SOCKET')
        all_pools = sum([e.values() for e in self.sockpools.values()], [])
        with ctx.log.get_logger('CULL').info(name) as _log:
            _log['len'] = len(all_pools)
            for pool in all_pools:
                pool.cull()

        total_num_in_use = sum([len(model.active_connections)
                                for model in self.server_models.values()])

        if total_num_in_use >= GLOBAL_MAX_CONNECTIONS:
            sock_log.critical('GLOBAL_MAX').success('culling sockets',
                                                    limit=GLOBAL_MAX_CONNECTIONS,
                                                    in_use=total_num_in_use)
            # try to cull sockets to make room
            made_room = False
            for pool in all_pools:
                if pool.total_sockets:
                    made_room = True
                    gevent.joinall(pool.reduce_size(pool.total_sockets / 2))
            if not made_room:
                ctx.intervals['net.out_of_sockets'].tick()
                raise OutOfSockets("maximum global socket limit {0} hit: {1}".format(
                    GLOBAL_MAX_CONNECTIONS, total_num_in_use))

        num_in_use = sum([len(self.server_models[address].active_connections)
                          for address in address_list])

        if num_in_use >= MAX_CONNECTIONS:
            sock_log.critical('ADDR_MAX').success('culling {addr} sockets',
                                                  limit=GLOBAL_MAX_CONNECTIONS,
                                                  in_use=total_num_in_use,
                                                  addr=repr(address_list))

            # try to cull sockets
            made_room = False
            for pool in all_pools:
                for address in address_list:
                    num_pooled = pool.socks_pooled_for_addr(address)
                    if num_pooled:
                        gevent.joinall(pool.reduce_addr_size(address, num_pooled / 2))
                        made_room = True
            if not made_room:
                ctx.intervals['net.out_of_sockets'].tick()
                ctx.intervals['net.out_of_sockets.' + str(name)].tick()
                raise OutOfSockets("maximum sockets for {0} already in use: {1}".format(
                    name, num_in_use))
        return


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
    MAX_CONNECTIONS = int(0.8 * resource.getrlimit(resource.RLIMIT_NOFILE)[0])
    GLOBAL_MAX_CONNECTIONS = MAX_CONNECTIONS
except:
    MAX_CONNECTIONS = 800
    GLOBAL_MAX_CONNECTIONS = 800
# At least, move these to context object for now


class _Resolver(gevent.resolver_thread.Resolver):
    '''
    See gevent.resolver_thread module.  This is a way to avoid thread
    dispatch for getaddrinfo called on (ip, port) tuples, since that is
    such a common case and the thread dispatch seems to occassionally go
    off the rails in high-load environments like stage2.
    '''
    def getaddrinfo(self, *args, **kwargs):
        '''
        only short-cut for one very specific case which is extremely
        common in our code; don\'t worry about short-cutting the thread
        dispatch for all possible cases
        '''
        if len(args) == 2 and isinstance(args[1], (int, long)):
            try:
                socket.inet_aton(args[0])
            except socket.error:
                pass
            else:  # args is of form (ip_string, integer)
                return socket.getaddrinfo(*args)
        return super(_Resolver, self).getaddrinfo(*args, **kwargs)


class ServerModelDirectory(dict):
    def __missing__(self, key):
        self[key] = ServerModel(key)
        return self[key]


class ServerModel(object):
    '''
    This class represents an estimate of the state of a given "server".
    "Server" is defined here by whatever accepts the socket
    connections, which in practice may be an entire pool of server
    machines/VMS, each of which has multiple worker thread/procs.

    For example, estimate how many connections are currently open
    (note: only an estimate, since the exact server-side state of the
    sockets is unknown)

    '''
    def __init__(self, address):
        self.last_error = 0
        self.active_connections = weakref.WeakKeyDictionary()
        self.address = address

    def sock_in_use(self, sock):
        self.active_connections[sock] = time.time()

    def __repr__(self):
        if self.last_error:
            dt = datetime.datetime.fromtimestamp(int(self.last_error))
            last_error = dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            last_error = "(None)"
        return "<ServerModel {0} last_error={1} nconns={2}>".format(
            repr(self.address), last_error, len(self.active_connections))


class MonitoredSocket(object):
    '''
    A socket proxy which allows socket lifetime to be monitored.
    '''
    def __init__(self, sock, registry, protected,
                 name=None, type=None, state=None):
        self._msock = sock
        self._registry = registry  # TODO: better name for this
        self._spawned = time.time()
        self._protected = protected
        self._type = type
        # alias some functions through for improved performance
        #  (__getattr__ is pretty slow compared to normal attribute access)
        self.name = name
        self.state = state

    def send(self, data, flags=0):
        ret = self._msock.send(data, flags)
        context.get_context().store_network_data(
            (self.name, self._msock.getpeername()),
            self.fileno(), "OUT", data)
        return ret

    def sendall(self, data, flags=0):
        ret = self._msock.sendall(data, flags)
        context.get_context().store_network_data(
            (self.name, self._msock.getpeername()),
            self.fileno(), "OUT", data)
        return ret

    def recv(self, bufsize, flags=0):
        data = self._msock.recv(bufsize, flags)
        context.get_context().store_network_data(
            (self.name, self._msock.getpeername()),
            self.fileno(), "IN", data)
        return data

    def close(self):
        if self in self._registry:
            del self._registry[self]
        if self.state:
            self.state.transition('closed')
        return self._msock.close()

    def shutdown(self, how):  # not going to bother tracking half-open sockets
        if self in self._registry:  # (unlikely they will ever be used)
            del self._registry[self]
        return self._msock.shutdown(how)

    def __repr__(self):
        return "<MonitoredSocket " + repr(self._msock) + ">"

    def __getattr__(self, attr):
        return getattr(self._msock, attr)


class AddressGroup(object):
    '''
    An address group represents the set of addresses known by a
    specific name to a client at runtime.  That is, in a specific
    environment (stage, live, etc), an address group represents the
    set of <ip, port> pairs to try.

    An address group consists of tiers. Each tier should be fully
    exhausted before moving on to the next; tiers are "fallbacks".  A
    tier consists of prioritized addresses.  Within a tier, the
    addresses should be tried in a priority weighted random order.

    The simplest way to use an address group is just to iterate over
    it, and try each address in the order returned.

    tiers: [ [(weight, (ip, port)), (weight, (ip, port)) ... ] ... ]

    '''
    def __init__(self, tiers):
        if not any(tiers):
            raise ValueError("no addresses provided for address group")
        self.tiers = tiers

    def connect_ordering(self):
        plist = []
        for tier in self.tiers:
            # Kodos says: "if you can think of a simpler way of
            # achieving a weighted random ordering, I'd like to hear
            # it" (http://en.wikipedia.org/wiki/Kang_and_Kodos)
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
                # TODO: maybe do r1 / r2 fallback; however, given this
                # is stage only that use case is pretty slim
                ports = [int(ctx.topos.get_port(newkey))]
                val = AddressGroup(([(1, (ctx.stage_ip, p)) for p in ports],))
                self.__dict__.setdefault("warnings", {})
                self.setdefault("inferred_addresses", []).append((key, val))
                self[key] = val
                if key != newkey:
                    self.warnings["inferred_addresses"].append((newkey, val))
                self[newkey] = val
                return val
        self.__dict__.setdefault("errors", {})
        self.errors.setdefault("unknown_addresses", set()).add(key)

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
