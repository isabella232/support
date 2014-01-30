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
import threading

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
                # TODO: maybe this determination belongs centralized in context?
                if dev or env.pp_host_env() in ("STAGE2", "HYPER"):
                    if ctx.ssl_client_cert_optional_in_dev:
                        sslcontext = getattr(protected, 'ssl_dev_server_context')
                    else:
                        sslcontext = getattr(protected, 'ssl_server_context')
                    server = MultiProtocolWSGIServer(
                        sock, app, spawn=10000, context=sslcontext)
                else:
                    server = SslContextWSGIServer(
                        sock, app, spawn=10000, context=protected.ssl_server_context)
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
            ctx = context.get_context()
            pidfile = os.path.join(ctx.pid_file_path,
                                   '{0}.pid'.format(ctx.appname))
            self.arbiter.spawn_daemon(pidfile=pidfile)
        else:
            self.arbiter.run()

    def _post_fork(self):
        # TODO: revisit this with newer gevent release
        hub = gevent.hub.get_hub()
        hub.loop.reinit()  # reinitializes libev
        hub._threadpool = None  # eliminate gevent's internal threadpools
        gevent.sleep(0)  # let greenlets run
        # finally, eliminate our threadpools
        context.get_context().thread_locals = threading.local()
        if self.post_fork:
            self.post_fork()
        self.start()

    def start(self):
        errs = {}
        for server in self.servers:
            try:
                server.start()
            except Exception as e:
                errs[server] = e
        if errs:
            raise RuntimeError(errs)

    def stop(self, timeout=30.0):
        async.join([async.spawn(server.stop, timeout) for server in self.servers], raise_exc=True)


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


_NO_SSL_HTTP_RESPONSE = "\r\n".join([
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
