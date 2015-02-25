
import urllib2
import socket
from support import context
from support.http_client import _GHTTPConnection, _GHTTPSConnection

# convenience so that others can import urllib2 symbols from right here
from urllib2 import *


class LogAwareHandler(urllib2.AbstractHTTPHandler):
    LOG_LEVEL = 'info'
    TRANSACTION_TYPE = 'API'

    def get_log_kwargs(self, request):
        return {'type': self.TRANSACTION_TYPE,
                'name': request.get_host() + '.%s' % request.get_method()}

    def pre_request(self, log_record, request):
        pass

    def post_request(self, log_record, request, response):
        pass

    def do_open(self, conn_type, req):
        get_log_record = getattr(context.get_context().log, self.LOG_LEVEL)
        with get_log_record(**self.get_log_kwargs(req)) as log_record:
            self.pre_request(log_record, req)
            resp = urllib2.AbstractHTTPHandler.do_open(self, conn_type, req)
            self.post_request(log_record, req, resp)
            return resp


# need to do this the hard way because of the dir based
# metaprogramming inside urllib2.  unfortunately this returns a new
# style class.

def _make_handler(name, connection_class, base, protocol):
    def _open(self, req):
        return self.do_open(connection_class, req)

    _open.__name__ = protocol + '_open'
    request_method = protocol + '_request'

    return type(name, (base, object),
                {_open.__name__: _open,
                 request_method: urllib2.AbstractHTTPHandler.do_request_})


GHTTPHandler = _make_handler('GHTTPHandler', _GHTTPConnection,
                             LogAwareHandler, 'http')
GHTTPSHandler = _make_handler('GHTTPSHandler', _GHTTPSConnection,
                              LogAwareHandler, 'https')


def build_opener(*args, **kwargs):
    NewHTTPHandler = kwargs.pop('_http_replacement', GHTTPHandler)
    NewHTTPSHandler = kwargs.pop('_https_replacement', GHTTPSHandler)
    opener = urllib2.build_opener(*args, **kwargs)

    http_idx, https_idx = None, None
    handlers = []
    for i, handler in enumerate(opener.handlers[:]):
        if isinstance(handler, urllib2.HTTPHandler):
            http_idx = i
        elif isinstance(handler, urllib2.HTTPSHandler):
            https_idx = i
        else:
            handlers.append(handler)
    opener.handlers = handlers

    assert (http_idx is not None) and (https_idx is not None)

    for thing in [opener.handle_open,
                  opener.process_request,
                  opener.process_response]:
        thing['http'] = [handler for handler in thing['http']
                         if not isinstance(handler, urllib2.HTTPHandler)]
        thing['https'] = [handler for handler in thing['https']
                          if not isinstance(handler, urllib2.HTTPSHandler)]

    http = NewHTTPHandler()
    https = NewHTTPSHandler()
    opener.add_handler(http)
    opener.add_handler(https)

    for i, handler in (http_idx, http), (https_idx, https):
        opener.handlers.remove(handler)
        opener.handlers.insert(i, handler)
    return opener


_opener = None


def urlopen(url, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
    global _opener
    if _opener is None:
        _opener = build_opener()
    return _opener.open(url, data, timeout)


def install_opener(opener):
    global _opener
    _opener = opener
