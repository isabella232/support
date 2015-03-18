'''
This module holds root exception types, as well as mix-ins
that can be subclassed for user code or used in try/except blocks
'''
import sys
import traceback
import os

import gevent


def current_code_list():
    'returns a code-list that can be formatted by code_list2trace_list'
    f = sys._getframe().f_back
    code_list = []
    while f:
        code_list.append(f.f_code)
        code_list.append(f.f_lineno)
        f = f.f_back
    return code_list


def code_list2trace_list(code_list):
    trace_list = []
    for code, lineno in zip(code_list[-2::-2], code_list[-1::-2]):
        line = LINECACHE.getline(code.co_filename, lineno)
        trace = '  File "{0}", line {1}, in {2}\n'.format(
            code.co_filename, lineno, code.co_name)
        if line:
            trace += '    {0}\n'.format(line.strip())
        trace_list.append(trace)
    return trace_list


class GLineCache(object):
    'same idea as linecache.py module, but uses gevent primitives and thread/greenlet safe'
    def __init__(self):
        self.cache = {}

    def getline(self, filename, lineno):
        if filename not in self.cache:
            gevent.get_hub().threadpool.apply(self.update, (filename,))
        lines = self.cache.get(filename, [])
        try:
            return lines[lineno]
        except IndexError:
            return ''

    def update(self, filename):
        if not self._trypath(filename, filename):
            # TODO: is loader.getsource() important here?
            # ... in practice are there a significant number of libs
            # distributed in zip or other non-source-file form?
            for directory in sys.path:
                path = os.path.join(directory, filename)
                if self._trypath(filename, path):
                    break

    def _trypath(self, filename, path):
        try:
            with open(path, 'Ur') as f:
                self.cache[filename] = f.readlines()
                return True
        except IOError:
            return False


LINECACHE = GLineCache()

