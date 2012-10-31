import copy
import functools
from asf_context import ASFError

import gevent.pool
import gevent.socket
import gevent.threadpool

CPU_THREAD = None # Lazily initialize -- mhashemi 6/11/2012
CPU_THREAD_ENABLED = True

def cpu_bound(f):
    '''
    decorator to mark a function as cpu-heavy; will be executed in a separate
    thread to avoid blocking any socket communication
    '''
    @functools.wraps(f)
    def g(*a, **kw):
        if not CPU_THREAD_ENABLED:
            return f(*a, **kw)
        global CPU_THREAD
        if CPU_THREAD is None:
            CPU_THREAD = gevent.threadpool.ThreadPool(1)
        return CPU_THREAD.apply_e((Exception,), f, a, kw)
    g.no_defer = f
    return g

def close_threadpool():
    global CPU_THREAD
    if CPU_THREAD:
        CPU_THREAD.join()
        CPU_THREAD.kill()
        CPU_THREAD = None
    return

def _safe_req(req):
    'capture the stack trace of exceptions that happen inside a greenlet'
    try:
        return req()
    except Exception as e:
        raise ASFError(e)

def join(asf_reqs, raise_exc=False, timeout=None):
    greenlets = [gevent.Greenlet.spawn(_safe_req, req) for req in asf_reqs]
    gevent.joinall(greenlets, raise_error=raise_exc, timeout=timeout)
    results = []
    for gr, req in zip(greenlets, asf_reqs):
        if gr.successful():
            results.append(gr.value)
        elif gr.ready(): #finished, but must have had error
            results.append(gr.exception)
        else: #didnt finish, must have timed out
            results.append(ASFTimeoutError(req, timeout))
    return results

class ASFTimeoutError(ASFError):
    def __init__(self, request=None, timeout=None):
        try:
            self.ip = request.ip
            self.port = request.port
            self.service_name = request.service
            self.op_name = request.operation
        except AttributeError as ae:
            pass
        if timeout:
            self.timeout = timeout

    def __str__(self):
        ret = "ASFTimeoutError"
        try:
            ret += " encountered while to trying execute "+self.op_name \
                   +" on "+self.service_name+" ("+str(self.ip)+':'      \
                   +str(self.port)+")"
        except AttributeError:
            pass
        try:
            ret += " after "+str(self.timeout)+" seconds"
        except AttributeError:
            pass
        return ret

### What follows is code related to map() contributed from MoneyAdmin's asf_util
class Node(object):
    def __init__(self, ip, port, **kw):
        self.ip   = ip
        self.port = port
        
        # in case you want to add name/location/id/other metadata
        for k,v in kw.items():
            setattr(self, k, v)

# call it asf_map to avoid name collision with builtin map?
# return callable to avoid collision with kwargs?
def map_factory(op, node_list, raise_exc=False, timeout=None):
    """
    map_factory() enables easier concurrent calling across multiple servers,
    provided a node_list, which is an iterable of Node objects.
    """
    def asf_map(*a, **kw):
        return join([op_ip_port(op, node.ip, node.port).async(*a, **kw)
                        for node in node_list], raise_exc=raise_exc, timeout=timeout)
    return asf_map

def op_ip_port(op, ip, port):
    serv = copy.copy(op.service)
    serv.meta = copy.copy(serv.meta)
    serv.meta.ip = ip
    serv.meta.port = port
    op = copy.copy(op)
    op.service = serv
    return op
