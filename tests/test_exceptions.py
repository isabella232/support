import time
import sys
import traceback

try:
    from support import exceptions
except:
    sys.path.append('..')
    from support import exceptions


def r(n=5, m=1, f=exceptions.current_code_list):
    if n:
        return r(n - 1, m)
    for i in range(m):
        return f()


def inline(n=5, m=10000):
    if n:
        return inline(n - 1, m)
    for i in range(m):
        f = sys._getframe().f_back
        code_list = []
        while f:
            code_list.append(f.f_code)
            code_list.append(f.f_lineno)
            f = f.f_back


def test_current_code_list():
    r()


def test_code_list2trace_list():
    exceptions.code_list2trace_list(r())


if __name__ == "__main__":
    s = time.time()
    r(m=10000)
    f = time.time()
    print "current_code_list() took", (f - s) * 100, "microseconds"
    s = time.time()
    inline()
    f = time.time()
    print "inline code-list capture took", (f - s) * 100, "microseconds"
    s = time.time()
    r(m=10000, f=traceback.format_stack)
    f = time.time()
    print "traceback.format_stack() took", (f - s) * 100, "microseconds"
    s = time.time()
    r(m=10000, f=traceback.extract_stack)
    f = time.time()
    print "traceback.extract_stack() took", (f - s) * 100, "microseconds"
