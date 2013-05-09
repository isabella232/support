'''
This mnodule defines a context object which holds on to all global state.
'''
from weakref import WeakKeyDictionary
from threading import local
from collections import namedtuple


class Context(object):
    '''
    Context object is meant to be the clearing-house for global data in an
    application written using Python Infrastructure.

    Two categories of data in here:

    1- Global data used internally by the infrastructure.

    2- Global data which would otherwise need to be kept track of by user code.
    (This stuff can be identified by the presence of getters)
    '''
    def __init__(self, dev=False):
        #ASYNC RELATED STUFF
        self.greenlet_ancestors = WeakKeyDictionary()
        self.greenlet_correlation_ids = WeakKeyDictionary()
        self.thread_locals = local()
        self.cpu_thread_enabled = True

        #CAL RELATED STUFF
        import cal
        self.cal = cal.DefaultClient()
        self.greenlet_trans_stack = WeakKeyDictionary()

        #ASF RELATED STUFF
        self.asf_context = None

        #PROTECTED RELATED STUFF
        self.protected = None

        #OCC RELATED STUFF
        self.occs = {}  # TODO: figure out what gets stored here?

        #MAYFLY RELATED STUFF
        self.mayflys = {}

    def set_config(self, config):
        self.config = config
        for key, address in config.mayflys:
            pass
        self.occs = config.occs

    def get_mayfly(self, name, namespace):
        if name not in self.mayflys:
            raise ValueError('Unknown Mayfly: '+repr(name))

        import mayfly
        ip, port = self.mayflys[name]
        return mayfly.Client(ip, port, self.appname, namespace)

    def make_occ(self, name):
        'make instead of get to indicate this is creating a stateful object'
        if name not in self.occs:
            raise ValueError('Uknonwn OCC: '+repr(name))

        import occ
        ip, port = self.occs[name]
        return occ.Connection(ip, port, self.protected)


class Config(object):
    '''
    Represents the configuration of a context.
    This is just a bag of constants which are used to initialize a context.
    '''
    def __init__(self, appname, mayflys, occs):
        self.appname = appname
        self.mayflys = mayflys
        self.occs = occs

# A set of *Conf classes representing the configuration of different things.
MayflyConf    = namedtuple('MayflyConf'   , 'ip port')
OccConf       = namedtuple('OccConf'      , 'ip port')


STAGE_CONFIG = Config()

CONTEXT = Context()  # initialize a default context


def get_context():
    return CONTEXT


def set_context(context):
    global CONTEXT
    CONTEXT = context
