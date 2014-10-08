'''A simple HTTP client which mixes httplib with gevent and PayPal protecteds.

It provides convenience functions for the standard set of `HTTP methods`_:

>>> http_client.get('http://example.com/foo') # doctest: +SKIP

which are just shortcuts for the corresponding :py:func:`request` call:

>>> http_client.request("get", "http://example.com/foo") # doctest: +SKIP

.. _HTTP Methods: http://en.wikipedia.org/wiki/Hypertext_Transfer_Protocol\
#Request_methods

If you don't intend to read the response's body, you should use a
context manager:

>>> with http_client.get('http://www.example.com') as response: # doctest: +SKIP
...    assert response.status == 200

This will release the underlying socket back to the socket pool.
'''
import httplib
from urlparse import urlparse, urlunparse
import functools
import urllib2
import os
import json

import context
import connection_mgr

from gevent import socket

_CORR_ID_HEADERS = ['correlation-id',
                    'x-pp-corrid',
                    "x-paypal-correlation-id"]  # also in asf/server.py

# TODO: make and use a better HTTP library instead of wrapping httplib.
# hopefully this is at least a pretty stable abstraction that can migrate over
# ... if nothing else, much better than shrugging our shoulders when someone
# asks how to make an http request


class _GHTTPConnection(httplib.HTTPConnection):

    default_port = httplib.HTTP_PORT

    def __init__(self, host, port=None, strict=None,
                 timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                 protected=None):
        httplib.HTTPConnection.__init__(self, host, port, strict, timeout)
        self.protected = protected

    def connect(self):
        ctx = context.get_context()
        self.sock = ctx.connection_mgr.get_connection((self.host, self.port),
                                                      self.protected)
        if self._tunnel_host:
            self._tunnel()

    def release_sock(self):
        # print self._HTTPConnection__state, self.sock
        if (self._HTTPConnection__state == httplib._CS_IDLE
           and self._HTTPConnection__response is None
           and self.sock):
            context.get_context().connection_mgr.release_connection(self.sock)
            self.sock = None

    def _set_content_length(self, body):
        # Set the content-length based on the body.
        thelen = None
        try:
            thelen = str(len(body))
        except TypeError:
            # If this is a file-like object, try to
            # fstat its file descriptor
            try:
                thelen = str(os.fstat(body.fileno()).st_size)
            except (AttributeError, OSError):
                # Don't send a length if this failed
                if self.debuglevel > 0:
                    print "Cannot stat!!"

        if thelen is not None:
            self.putheader('Content-Length', thelen)

    def __del__(self):
        self.release_sock()


class _GHTTPSConnection(_GHTTPConnection):

    default_port = httplib.HTTPS_PORT

    def __init__(self, host, port=None, strict=None,
                 timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        _GHTTPConnection.__init__(self, host, port, strict, timeout,
                                  protected=connection_mgr.PLAIN_SSL)


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


def request(method, url, body=None, headers=None,
            literal=False, use_protected=False,
            timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
    '''\
    A function to issue HTTP requests.

    **NB: If you want to issue ASF requests, you should be using
    idealclient!**

    :param method: the `HTTP method`_ for this request. Case
      insensitive.

    :param url: the URL to request. Must include a protocol
      (e.g. `http`, `https`).

    :param body: the body of the request, if applicable

    :type body: a string or file-like object (i.e, an object that has
      a ``read`` method).  It could also be a dict, in which case
      it is stringified, and the header set to application/json

    :param headers: A dictionary of request headers

    :type headers: :py:class:`dict`

    :param literal: if true, instruct
      :py:class:`~httplib.HTTPConnection` **not** to set the ``Host`` or
      ``Accept-Encoding`` headers automatically.  Useful for testing

    :param use_protected: if true, use the appropriate protected for
      this call.

    :param timeout: connection timeout for this request.

    :returns: a :py:class:`Response` object.

    An example, calling up google with a custom host header:

    >>> request('get',
    ...         'http://google.com',
    ...         headers={'Host': 'www.google.com'},
    ...         literal=True)
    <http_client.Response (200) GET http://google.com>

    .. _HTTP Method: http://en.wikipedia.org/wiki/\
    Hypertext_Transfer_Protocol#Request_methods

    '''
    method = method.upper()
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

    if headers is None:
        headers = {}

    if not literal:
        headers.setdefault('User-Agent', 'python')

    if isinstance(body, dict):
        body = json.dumps(body)
        if 'Content-Type' not in headers:
            headers['Content-Type'] = 'application/json'
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
        raw.status, raw.msg, raw)


class Request(object):
    '''\
    A simple wrapper for HTTP Requests

    .. py:attribute:: method

       The method used for this request (e.g., `POST`, `GET`).

    .. py:attribute:: url

       The requested URL.

    .. py:attribute:: headers

       The request headers (a :py:class:`list` of two-item :py:class:`tuples`)

    .. py:attribute:: body

       The body if present, otherwise `None`.
    '''

    def __init__(self, method, url, headers, body):
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body

    def __repr__(self):
        return "<http_client.Request {0} {1}>".format(self.method, self.url)


class Response(object):
    r'''\
    A simple wrapper for HTTP responses.

    .. py:attribute:: request

      the :py:class:`Request` object that lead to this response

    .. py:attribute:: status

      the numeric status code for this Response

    .. py:attribute:: headers

      an :py:class:`~httplib.HTTPMessage` object containing this
      response's headers.  You can treat this as a dictionary: for
      example, you can get the value for the ``Host`` header with
      ``msg['Host']``.  **You should, however, be careful with
      duplicate headers.**

      Consider the following headers:

      >>> headers = '\r\n'.join(['X-First-Header: First, Value',
      ...                       'X-First-Header: Second, Value',
      ...                       'X-Second-Header: Final, Value',
      ...                       ''])

      Note that the header ``X-First-Header`` appears twice.

      >>> from StringIO import StringIO
      >>> from httplib import HTTPMessage
      >>> msg = HTTPMessage(StringIO(headers))
      >>> msg['X-First-Header']
      'First, Value, Second, Value'

      :py:class:`HTTPMessage` has *concatenated* the two values we
      provided for `X-First-Header` (`First, Value` and `Second,
      Value`) with a comma.  Unfortunately both of these values
      contain a comma.  That means a simple :py:meth:`str.split` can't
      Recover the original values:

      >>> msg['X-First-Header'].split(', ')
      ['First', 'Value', 'Second', 'Value']

      The same behavior occurs with :meth:`HTTPMessage.items`:

      >>> msg.items() # doctest: +NORMALIZE_WHITESPACE
      [('x-second-header', 'Final, Value'),
       ('x-first-header', 'First, Value, Second, Value')]

      To correctly recover values from duplicated header fields, use
      :meth:`HTTPMessage.getheaders`:

      >>> msg.getheaders('X-First-Header')
      ['First, Value', 'Second, Value']

    .. py:attribute:: http_response

       the underlying :py:class:`~httplib.HTTPResponse` object for
       this response.
    '''

    def __init__(self, request, status, headers, http_response):
        self.request = request
        self.status = status
        self.headers = headers
        self.http_response = http_response
        self._body = None

    def close(self):
        """Release the underlying socket back to the connection pool.  This
        will be automatically called by :attribute:`~Response.body`
        after the body has been read.  You should arrange to have this called (

        """
        if hasattr(self.http_response, '_connection'):
            self.http_response._connection.release_sock()
            del self.http_response._connection

        self.http_response.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    @property
    def body(self):
        """the body of the request, if applicable.

        Since this value is lazily loaded, if you never access it the
        response's body will never be downloaded.  Once loaded it's
        stored locally, so repeated accesses won't trigger repeated
        network calls.
        """

        if self._body is None:
            with self:
                self._body = self.http_response.read()
        return self._body

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
