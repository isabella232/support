'''
Defines a ServerGroup class for running and managing servers, as well as a set of 
base server types.

The following information is needed to start a server:
1- handler / wsgi app
2- address
3- ssl preference

'''
import collections
import socket
import os

if hasattr(os, "fork"):
    import ufork
else:
    ufork = None
from gevent.pywsgi import WSGIServer
import gevent.socket

import async
from protected import Protected
import context


class ServerGroup(object):
    def __init__(self, wsgi_apps=(), stream_handlers=(), prefork=False, daemonize=False, dev=False, **kw):
        self.prefork = prefork
        self.daemonize = daemonize
        self.post_fork = kw.get('post_fork')  # callback to be executed post fork
        self.server_log = kw.get('gevent_log')
        self.wsgi_apps = wsgi_apps
        self.stream_handlers = stream_handlers
        ctx = context.get_context()
        self.num_workers = ctx.num_workers
        self.servers = []
        self.socks = {}
        for app in wsgi_apps:
            app, address, ssl = app
            sock = _make_server_sock(address)
            if isinstance(ssl, Protected):
                protected = ssl
            elif ssl:
                protected = ctx.protected
            else:
                protected = None
            if protected:
                if dev:
                    if ctx.ssl_client_cert_optional_in_dev:
                        sslcontext = getattr(protected, 'ssl_dev_server_context')
                    else:
                        sslcontext = getattr(protected, 'ssl_server_context')
                    server = MultiProtocolWSGIServer(sock, app, context=sslcontext)
                else:
                    server = SslContextWSGIServer(sock, app, context=protected.ssl_server_context)
            else:
                server = WSGIServer(sock, app)
            server.log = self.server_log or RotatingGeventLog()
            self.servers.append(server)
            self.socks[server] = sock
            # prevent a "blocking" call to DNS on each request
            # (NOTE: although the OS won't block, gevent will dispatch to a threadpool which is expensive)
            server.set_environ({'SERVER_NAME': gevent.socket.getfqdn(address[0])})
        for handler in stream_handlers:
            pass

    def run(self):
        if not self.prefork:
            self.start()
            try:
                while 1:
                    async.sleep(1.0)
            finally:
                self.stop()
            return
        if not ufork:
            raise RuntimeError('attempting to run pre-forked on platform without fork')
        self.arbiter = ufork.Arbiter(
            post_fork=self._post_fork, child_pre_exit=self.stop, parent_pre_stop=self.stop,
            size=self.num_workers, sleep=async.sleep, fork=gevent.fork)
        if self.daemonize:
            self.arbiter.spaen_daemon(context.get_context().pid)
        else:
            self.arbiter.run()

    def _post_fork(self):
        # TODO: revisit this with newer gevent release
        gevent.hub.get_hub().loop.reinit()  # threadpools need to be restarted
        gevent.sleep(0)  # let reinit() scheduled greenlets run
        if self.post_fork:
            self.port_fork()
        self.start()

    def _stop_start(self, start):  # just a bit of DRY
        errs = {}
        for server in self.servers:
            try:
                if start:
                    server.start()
                else:
                    server.stop()
            except Exception as e:
                errs[server] = e
        if errs:
            raise RuntimeError(errs)

    def start(self):
        self._stop_start(True)

    def stop(self):
        self._stop_start(False)


def _make_server_sock(address):
    sock = gevent.socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(address)
    sock.listen(128)  # Note: 128 is a "hint" to the OS than a strict rule about backlog size
    return sock


def my_close(self):
    context.get_context().stats['fake_close'].add(1.0)

class SslContextWSGIServer(WSGIServer):
    def wrap_socket_and_handle(self, client_socket, address):
        if not self.ssl_args:
            raise ValueError('https server requires server-side'
                             ' SSL certificate (protected)')
        protocol = _socket_protocol(client_socket)
        if protocol == "ssl":
            #if hasattr(client_socket, "_sock"):
            #    client_socket._sock.close = my_close
            ssl_socket = async.wrap_socket_context(client_socket, **self.ssl_args)
            return self.handle(ssl_socket, address)
        elif protocol == "http":
            client_socket.send(_NO_SSL_HTTP_RESPONSE)
            async.killsock(client_socket)
        else:
            context.get_context().counts["server.pings"] += 1


class MultiProtocolWSGIServer(SslContextWSGIServer):
    def wrap_socket_and_handle(self, client_socket, address):
        protocol = _socket_protocol(client_socket)
        if protocol == "http":
            return self.handle(client_socket, address)
        elif protocol == "ssl":
            #client_socket.close = my_close
            ssl_socket = async.wrap_socket_context(client_socket, **self.ssl_args)
            return self.handle(ssl_socket, address)
        else:
            context.get_context().counts["server.pings"] += 1


#http://en.wikipedia.org/wiki/Hypertext_Transfer_Protocol#Request_methods
_HTTP_METHODS = ('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'TRACE', 'OPTION',
                 'CONNECT', 'PATCH')

_SSL_RECORD_TYPES = {
    '\x14': 'CHANGE_CIPHER_SPEC', '\x15': 'ALERT', '\x16': 'HANDSHAKE',
    '\x17': 'APPLICATION_DATA',
}

_SSL_VERSIONS = {
    '\x03\x00': 'SSL3', '\x03\x01': 'TLS1', '\x03\x02': 'TLS1.1', '\x03\x03': 'TLS1.2'
}


_NO_SSL_HTTP_RESPONSE = "\n\r".join([
    'HTTP/1.1 401 UNAUTHORIZED',
    'Content-Type: text/plain',
    'Content-Length: ' + str(len('SSL REQUIRED')),
    '',
    'SSL REQUIRED'
    ])  # this could probably be improved, but better than just closing the socket


def _socket_protocol(client_socket):
    first_bytes = client_socket.recv(10, socket.MSG_PEEK)
    if any([method in first_bytes for method in _HTTP_METHODS]):
        return "http"
    elif not first_bytes:
        # TODO: empty requests are sometimes sent ([socket] keepalives?)
        return
    elif first_bytes[0] in _SSL_RECORD_TYPES and first_bytes[1:3] in _SSL_VERSIONS:
        return "ssl"
    else:
        raise IOError("unrecognized client protocol: {0!r}".format(first_bytes))


class RotatingGeventLog(object):
    def __init__(self, size=10000):
        self.msgs = collections.deque()
        self.size = size

    def write(self, msg):
        self.msgs.appendleft(msg)
        if len(self.msgs) > self.size:
            self.msgs.pop()


'''
THE_PID = os.getpid()
# current PID -- used for detecting if gevent threads need re-initing
# TODO: is there a cleaner way to achieve this by inspecting the gevent hub object?


class ASFServer(object):
    def __init__(self, service_map, address, protocol=None,
                 service_dispatcher=None, dev=False, sslcontext=None,
                 **kw):
        import meta_service  # avoiding circular dependency

        self.service_map = service_map
        self.service_dispatcher = service_dispatcher or ServiceDispatcher
        self.num_workers = max(kw.get('num_workers'), 0) or None
        self.instance_map = dict([(k, v() if isinstance(v, type) else v) for k, v in service_map.items()])
        ctx = context.get_context()
        if ctx.admin_port:
            self.admin_instance_map = {}
            self.admin_instance_map['MetaService'] = meta_service.MetaService(self)
        else:
            self.instance_map['MetaService'] = meta_service.MetaService(self)
        self.address = address
        self.sock = gevent.socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(self.address)
        self.sock.listen(128)  # TODO: what is best value?
        service_dispatcher = self.service_dispatcher(self.instance_map)
        if kw.get("_wsgi_middleware"):
            self.wsgi = kw["_wsgi_middleware"](service_dispatcher)
        else:
            self.wsgi = service_dispatcher
        asf_ctx = asf_context.get_context()
        self.protocol = protocol or ('http' if dev else 'https')
        if ctx.dev and ctx.ssl_client_cert_optional_in_dev:
            ssl_context_name = 'ssl_dev_server_context'
        else:
            ssl_context_name = 'ssl_server_context'
        if not sslcontext:
            sslcontext = getattr(asf_ctx.protected, ssl_context_name, None)
        if not sslcontext:
            self.protected = ctx.protected
            sslcontext = getattr(self.protected, ssl_context_name, None)
        self.sslcontext = sslcontext
        if self.protocol == 'https':
            if not self.sslcontext:
                raise ValueError('could not find/load server-side'
                                 ' SSL certificate (protected)')
            self.server = SslContextWSGIServer(self.sock, self.wsgi, context=self.sslcontext)
        else:
            # test/dev mode; be http and https cross-compatible
            self.server = MultiProtocolWSGIServer(self.sock, self.wsgi, context=self.sslcontext)
        if context.get_context().admin_port:
            self.admin_wsgi = self.service_dispatcher(self.admin_instance_map)
            self.admin_sock = gevent.socket.socket()
            self.admin_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.admin_sock.bind(('127.0.0.1', context.get_context().admin_port))
            ml.la("Admin listening to 127.0.0.1:{0}", context.get_context().admin_port)
            self.admin_sock.listen(128)  # TODO: what is best value?
            self.admin_server = MultiProtocolWSGIServer(self.admin_sock, self.admin_wsgi, context=self.sslcontext)
        else:
            self.admin_sock = None
            self.admin_server = None
            self.admin_wsgi = None

        self.server.log = kw.get('gevent_log') or RotatingGeventLog()

        self.post_fork = kw.get('post_fork')
        #flags
        self.serve_ufork = kw.get('serve_ufork', not dev)
        self.serve_daemon = kw.get('serve_daemon', not dev)

        self.pid = kw.get('pid', self.serve_ufork)
        if self.pid is None and self.serve_ufork:
            self.pid = context.get_context().appname + ".pid"

    def run(self):
        if not self.serve_ufork:
            self._post_fork()
            try:
                while 1:
                    async.sleep(1.0)  # otherwise REPL will block server
            finally:
                self.sock.close()
                self.server.stop()
            return

        if not ufork:
            raise RuntimeError('called run_service without dev=True'
                               ' on platform without fork().')

        self.arbiter = ufork.Arbiter(
            post_fork=self._post_fork, child_pre_exit=self.server.stop,
            parent_pre_stop=self.sock.close, size=self.num_workers,
            sleep=async.sleep, fork=gevent.fork, printfunc=lambda x: sys.stdout.write(str(x)))

        # work-around for infra/gevent/ufork interaction which
        # causes getfqdn to have issues in child threads
        # TODO: figure out what is happening there
        self.server.set_environ({'SERVER_NAME': gevent.socket.getfqdn(self.address[0])})

        if self.serve_daemon:
            ll.use_the_file()  # log to disk in daemonized
            self.arbiter.spawn_daemon(self.pid)
        else:
            self.arbiter.run()

    def _post_fork(self):
        global THE_PID
        if THE_PID != os.getpid():  # _post_fork not just for forks
            gevent.hub.get_hub().loop.reinit()  # threads were hosed
            gevent.sleep(0)  # let them get unhosed
            THE_PID = os.getpid()
        if self.post_fork:
            self.post_fork()
        ml.ld("Post Fork")
        self.server.start()
        if self.admin_server:
            self.admin_server.start()
'''

