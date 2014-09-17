import urllib2
from urllib2 import *
import socket
from . import context
from .http_client import _GHTTPConnection, _GHTTPSConnection


class CALAwareHandler(urllib2.AbstractHTTPHandler):
    TRANSACTION_TYPE = 'API'

    def transaction_args(self, req):
        return {'type': self.TRANSACTION_TYPE,
                'name': req.get_host() + '.%s' % req.get_method()}

    def before_request(self, cal, req):
        pass

    def after_request(self, cal, req, resp):
        pass

    def do_open(self, req, conn_type):
        cal = context.get_context().cal
        with cal.trans(**self.transaction_args(req)):
            self.before_request(cal, req)
            resp = urllib2.AbstractHTTPHandler.do_open(self, conn_type, req)
            self.after_request(cal, req, resp)
            return resp


# need to do this the hard way because of the dir based
# metaprogramming inside urllib2.  unfortunately this returns a new
# style class.  should be ok. . . . . . . . .
def _make_handler(name, connection_class, base, protocol):
    return type(name, (base, object),
                {protocol + '_open': (lambda self, req:
                                      self.do_open(req, connection_class)),
                 'http_request': urllib2.AbstractHTTPHandler.do_request_})


GHTTPHandler = _make_handler('GHTTPHandler', _GHTTPConnection,
                             CALAwareHandler, 'http')
GHTTPSHandler = _make_handler('GHTTPSHandler', _GHTTPSConnection,
                              CALAwareHandler, 'https')


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
                          if not isinstance(handler, urllib2.HTTPHandler)]

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
