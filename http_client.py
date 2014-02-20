'''
A simple, plain HTTP client which mixes httplib with gevent and PayPal protecteds.

API is currently a single function:

http_client.request("get", "http://example.com/foo")
'''
import httplib
from urlparse import urlparse, urlunparse
import functools
import urllib2

import context

from gevent import socket


# TODO: make and use a better HTTP library instead of wrapping httplib.
# hopefully this is at least a pretty stable abstraction that can migrate over
# ... if nothing else, much better than shrugging our shoulders when someone
# asks how to make an http request


class _GHTTPConnection(httplib.HTTPConnection):

    def __init__(self, host, port=None, protected=None, strict=None,
                 timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        httplib.HTTPConnection.__init__(self, host, port, strict, timeout)
        self.protected = protected

    def connect(self):
        ctx = context.get_context()
        self.sock = ctx.connection_mgr.get_connection((self.host, self.port),
                                                      self.protected)
        if self._tunnel_host:
            self._tunnel()

    def release_sock(self):
        if self._HTTPConnection__state == "Idle" and self.sock:
            context.get_context().connection_mgr.release_connection(self.sock)
            self.sock = None

    def __del__(self):
        self.release_sock()


def urllib2_request(u2req, timeout=None):
    """\
    Translate a urllib2.Request to something we can pass to our
    request() function, and translate our Response to a
    urllib2.addinfourl object
    """
    # TODO: proxy support?
    method = u2req.get_method()
    url = u2req._Request__original
    body = u2req.get_data()
    headers = dict(u2req.unredirected_hdrs)
    headers.update((k, v) for k, v in u2req.headers.items()
                   if k not in headers)
    try:
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout
        resp = request(method, url, body, headers, **kwargs)
        hr = resp.http_response
        hr.recv = hr.read
        fp = socket._fileobject(hr, close=True)
        aiu = urllib2.addinfourl(fp=fp,
                                 headers=hr.msg,
                                 url=resp.request.url)
        aiu.code = hr.status
        aiu.msg = hr.reason
        return aiu
    except ValueError as e:
        raise urllib2.URLError(e.msg)


def request(method, url, body=None, headers={},
            literal=False, use_protected=False,
            timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
    if method not in _HTTP_METHODS:
        raise ValueError("invalid http method {0}".format(method))

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('unknown protocol %s' % parsed.scheme)
    domain, _, port = parsed.netloc.partition(':')
    try:
        port = int(port)
    except ValueError:
        port = 80 if parsed.scheme == 'http' else 443

    protected = (parsed.scheme == 'https') and (True if use_protected
                                                else "PLAIN_SSL")
    conn = _GHTTPConnection(domain, port, protected=protected, timeout=timeout)

    selector = urlunparse(parsed._replace(scheme='', netloc=''))

    skips = {'skip_host': True,
             'skip_accept_encoding': True} if literal else {}

    if not literal:
        headers.setdefault('User-Agent', 'python')

    conn.putrequest(method, selector, **skips)
    # OMD!
    if not literal and body is not None and 'Content-Length' not in headers:
        conn._set_content_length(body)

    for header, value in headers.items():
        if type(value) is list:
            for subvalue in value:
                conn.putheader(header, subvalue)
        else:
            conn.putheader(header, value)

    conn.endheaders()

    if body is not None:
        conn.send(body)
    raw = conn.getresponse()    # does NOT hold a reference to the
                                # HTTPConnection
    raw._connection = conn      # so the finalizer doesn't get called
                                # until the request has died
    return Response(
        Request(method, url, headers, body),
        raw.status, raw.getheaders(), raw)


class Request(object):
    def __init__(self, method, url, headers, body):
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body

    def __repr__(self):
        return "<http_client.Request {0} {1}>".format(self.method, self.url)


class Response(object):
    def __init__(self, request, status, headers, http_response):
        self.request = request
        self.status = status
        self.headers = headers
        self.http_response = http_response

    @property
    def body(self):
        return self.http_response.read()

    def __repr__(self):
        return "<http_client.Response ({0}) {1} {2}>".format(
            self.status, self.request.method, self.request.url)


#http://en.wikipedia.org/wiki/Hypertext_Transfer_Protocol#Request_methods
_HTTP_METHODS = ('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'TRACE', 'OPTION',
                 'CONNECT', 'PATCH')


def _init_methods():
    g = globals()
    for m in _HTTP_METHODS:
        g[m.lower()] = functools.partial(request, m)

_init_methods()
