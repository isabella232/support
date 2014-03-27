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
from gevent import pywsgi
import gevent.server
import gevent.socket

import async
from protected import Protected
import context
import env

import ll

ml = ll.LLogger()


class ServerGroup(object):
    def __init__(self, wsgi_apps=(), stream_handlers=(), prefork=False, daemonize=False, dev=False, **kw):
        '''Create a new ServerGroup which will can be started / stopped / forked as a group.

        *wsgi_apps* should be of the form  [ (wsgi_app, address, ssl), ...  ]

        *stream_handlers* should be of the form  [ (handler_func, address), ...  ]

        address here refers to a tuple (ip, port), or more generally anything which is
        acceptable as the address parameter to
        `socket.bind() <http://docs.python.org/2/library/socket.html#socket.socket.bind>`_.

        handler_func should have the following signature: f(socket, address), following
        the `convention of gevent <http://www.gevent.org/servers.html>`_.
        '''
        self.prefork = prefork
        self.daemonize = daemonize
        self.post_fork = kw.get('post_fork')  # callback to be executed post fork
        self.server_log = kw.get('gevent_log')
        self.wsgi_apps = wsgi_apps
        self.stream_handlers = list(stream_handlers)
        ctx = context.get_context()
        if ctx.backdoor_port is not None:
            self.stream_handlers.append((console_sock_handle, ("0.0.0.0", ctx.backdoor_port)))
        self.num_workers = ctx.num_workers
        self.servers = []
        self.socks = {}
        for app, address, ssl in wsgi_apps:
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
                server = pywsgi.WSGIServer(sock, app)
            server.log = self.server_log or RotatingGeventLog()
            self.servers.append(server)
            self.socks[server] = sock
            # prevent a "blocking" call to DNS on each request
            # (NOTE: although the OS won't block, gevent will dispatch to a threadpool which is expensive)
            server.set_environ({'SERVER_NAME': socket.getfqdn(address[0])})
        for handler, address in self.stream_handlers:
            sock = _make_server_sock(address)
            server = gevent.server.StreamServer(sock, handler, spawn=10000)
            self.servers.append(server)

    def run(self):
        if not self.prefork:
            self.start()
            ml.ld("Now really running-init over")
            try:
                while 1:
                    async.sleep(1.0)
            finally:
                self.stop()
            return
        if not ufork:
            raise RuntimeError('attempting to run pre-forked on platform without fork')
        ctx = context.get_context()
        self.arbiter = ufork.Arbiter(
            post_fork=self._post_fork, child_pre_exit=self.stop, parent_pre_stop=self.stop,
            size=self.num_workers, sleep=async.sleep, fork=gevent.fork, child_memlimit=ctx.worker_memlimit)
        if self.daemonize:
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
        import ll
        if context.get_context().serve_daemon:
            ll.use_the_file()  # stdout was closed during daemonization.
                               # plus ufork stdout logging issue
        if self.post_fork:
            self.post_fork()
        context.get_context().cal.event('WORKER', 'STARTED', '0', {'pid': os.getpid()})
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
    ml.ld("about to bind to {0!r}", address)
    sock = gevent.socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(address)
    sock.listen(128)  # Note: 128 is a "hint" to the OS than a strict rule about backlog size
    ml.la("Listen to {0!r} gave this socket {1!r}", address, sock)
    return sock


def my_close(self):
    context.get_context().stats['fake_close'].add(1.0)


class MakeFileCloseWSGIHandler(pywsgi.WSGIHandler):
    '''
    quick work-around to re-enable gevent's work-around of the
    makefile() call in the pywsgi handler keeping sockets alive
    past their shelf life
    '''
    def __init__(self, socket, address, server, rfile=None):
        if rfile is None and hasattr(socket, "_makefile_refs"):
            rfile = socket.makefile()
            # restore gEvent's work-around of not letting wsgi.environ['input']
            # keep the socket alive in CLOSE_WAIT state after client is gone
            # to work with async.SSLSocket
            socket._makefile_refs -= 1
        super(MakeFileCloseWSGIHandler, self).__init__(socket, address, server, rfile)


class SslContextWSGIServer(pywsgi.WSGIServer):
    handler_class = MakeFileCloseWSGIHandler    

    def wrap_socket_and_handle(self, client_socket, address):
        context.get_context().client_sockets[client_socket] = 1

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
        context.get_context().client_sockets[client_socket] = 1

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


### a little helper for running an interactive console over a socket
import code
import sys

class SockConsole(code.InteractiveConsole):
    def __init__(self, sock):
        code.InteractiveConsole.__init__(self)
        self.sock = sock
        self.sock_file = sock.makefile()

    def write(self, data):
        self.sock.sendall(data)

    def raw_input(self, prompt=""):
        self.write(prompt)
        inp = self.sock_file.readline()
        if inp == "":
            raise OSError("client socket closed")
        inp = inp[:-1]
        return inp

    def _display_hook(self, obj):
        '''
        Handle an item returned from the exec statement.
        '''
        self._last = ""
        if obj is None:
            return
        self.locals['_'] = None
        self._last = repr(obj)
        self.locals['_'] = obj

    def runcode(self, _code):
        '''
        Wraps base runcode to put displayhook around it.
        TODO: add a new displayhook dispatcher so that the
        current SockConsole is a greenlet-tree local.
        (In case the statement being executed itself
        causes greenlet switches.)
        '''
        prev = sys.displayhook
        sys.displayhook = self._display_hook
        self._last = None
        code.InteractiveConsole.runcode(self, _code)
        sys.displayhook = prev
        if self._last is not None:
            self.write(self._last)


def console_sock_handle(sock, address):
    console = SockConsole(sock)
    console.interact("Connected to pid {0} from {1}...".format(
        os.getpid(), address))
