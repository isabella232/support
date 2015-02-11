'''
This module holds root exception types, as well as mix-ins
that can be subclassed for user code or used in try/except blocks
'''
import sys
import traceback

import gevent


class GLineCache(object):
    'same idea as linecache.py module, but uses gevent primitives and thread/greenlet safe'
    def __init__(self):
        self.cache = {}

    def getline(self, filename, lineno):
        if filename not in self.cache:
            self.update(filename)
        lines = self.cache.get(filename, [])
        try:
            return lines[lineno]
        except IndexError:
            return ''

    def update(self, filename):
        def trypath(path):
            try:
                with open(path, 'Ur') as f:
                    return f.readlines()
            except IOError:
                return None

        lines = trypath(filename)
        if lines is None:
            # TODO: is loader.getsource() important here?
            # ... in practice are there a significant number of libs
            # distributed in zip or other non-source-file form?
            for directory in sys.path:
                lines = trypath(os.path.join(directory, filename))
                if lines is not None:
                    break
        return lines


class ASFError(Exception):
    """
    This exception is kind of nice because it can wrap an existing
    exception and save information, such as the stack trace, from the
    time of Exception creation.
    """
    def __init__(self, exception=None):
        Exception.__init__(self, exception)
        if exception:
            if isinstance(exception, BaseException):
                self.exception = exception
                exc_type, exc_value = sys.exc_info()[:2]
                if exc_value == exception:
                    exc_parts = traceback.format_exception_only(exc_type,
                                                                exc_value)
                    self.exc_string = ''.join(exc_parts)
                    self.stack_trace = ''.join(traceback.format_exc())
                else:
                    ecn = exception.__class__.__name__
                    self.exc_string = '%s: %s' % (ecn, exception)
            elif isinstance(exception, basestring):
                self.exception = self
                self.exc_string = "ASFError: " + exception
            else:
                self.exception = Exception(exception)
                ecn = self.exception.__class__.__name__
                self.exc_string = '%s: %s' % (ecn, self.exception)

    def __str__(self):
        ret = "ASFError"
        try:
            ret += ": " + self.exc_string
            ret += "\n\n" + self.stack_trace
        except AttributeError:
            pass
        ret += "\n\n%s" % (ASFError, self).__str__()
        return ret
