'''
This mnodule defines a context object which holds on to all global state.
'''
import getpass
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
        from asf import asf_context
        self.asf_context = asf_context.ASFContext()

        #PROTECTED RELATED STUFF
        self.protected = None

        #OCC RELATED STUFF
        self.occs = {}  # TODO: figure out what gets stored here?

        #MAYFLY RELATED STUFF
        self.mayflys = {}

        import sockpool
        self.sockpool = sockpool.SockPool()

        self.user = getpass.getuser()

        self._dev = dev

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

    @property
    def dev(self):
        return self._dev


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
Address = namedtuple('Address', 'ip port')


class AddressBook(object):
    'simple key-value store for addresses'
    def __init__(self, **kw):
        self.addresses = kw

    def __getitem__(self, key):
        return self.addresses[key]

    def mayfly_addr(self, key=None):
        if not key:
            key = 'mayflydirectoryserv'
        if not key.startswith('mayflydirectoryserv'):
            key = 'mayflydirectoryserv-'+key
        return self[key]

    def occ_addr(self, key=None):
        if not key:
            key = 'occ'
        if not key.startswith('occ'):
            key = 'occ-'+key
        return self[key]



def make_stage_address_book(stage_ip, other_ports={}):
    'convenience function; sets up known ports for a stage environment'
    addr = {}
    addr.update(CAL_DEV_ADDRESSES)
    for ports in MAYFLY_STAGE_PORTS, other_ports:
        addr.update(dict(
            [(k, (stage_ip, v)) for k,v in ports.items()]))
    return AddressBook(addr)

CONTEXT = None 


def get_context():
    global CONTEXT
    if CONTEXT is None:
        CONTEXT = Context()
    return CONTEXT


def set_context(context):
    global CONTEXT
    CONTEXT = context


#TODO: handle this in a more generic way by generating ports from
# data files put into resource

MAYFLY_STAGE_PORTS = {
    "mayflydirectoryserv":10368, 
    "mayflydirectoryserv-auth":10726,
    "mayflydirectoryserv-bridge":11302,
    "mayflydirectoryserv-gops":10957,
    "mayflydirectoryserv-pmt":10915,
    "mayflydirectoryserv-risk":10567,
}


# see: https://confluence.paypal.com/cnfl/display/CAL/CAL+Quick+Links
CAL_DEV_ADDRESSES = {
    'cal-stage' : ('10.57.2.159', 1118), #cal-stage.qa.paypal.com
    'cal-qa' : ('10.57.2.152', 1118), #cal-qa.qa.paypal.com
    'cal-dev': ('10.57.2.157', 1118), #cal-dev.qa.paypal.com
}

