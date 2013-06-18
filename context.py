'''
This mnodule defines a context object which holds on to all global state.
'''
import sys
import getpass
from weakref import WeakKeyDictionary
from threading import local
from collections import namedtuple

#NOTE: do not import anything else from infra at context import time
#this is a bit heavy-handed, but guarantees no circular import errors
#which are otherwise very easy to create


class Context(object):
    '''
    Context object is meant to be the clearing-house for global data in an
    application written using Python Infrastructure.

    Two categories of data in here:

    1- Global data used internally by the infrastructure.

    2- Global data which would otherwise need to be kept track of by user code.
    (This stuff can be identified by the presence of getters)
    '''
    def __init__(self, dev=False, stage_host=None):
        import topos

        self.config = None

        #ASYNC RELATED STUFF
        self.greenlet_ancestors = WeakKeyDictionary()
        self.greenlet_correlation_ids = WeakKeyDictionary()
        self.exception_traces = WeakKeyDictionary()
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

        import sockpool
        self.sockpool = sockpool.SockPool()

        self.user = getpass.getuser()

        self._dev = dev

        #TOPO RELATED STUFF
        self.stage_address_map = topos.StageAddressMap()
        self.set_stage_host(stage_host)
        self.address_book = AddressBook([])

    def set_stage_host(self, stage_host, stage_ip=None):
        from contrib import net

        self.stage_host = stage_host
        if stage_ip:
            self.stage_ip = stage_ip
        elif stage_host:
            self.stage_ip = net.find_host(stage_host)[0]
        else:
            self.stage_ip = None

        # TODO: DRY here and set_config on addresses
        if self.stage_host:
            addresses = self.stage_address_map.get_host_map(self.stage_ip)
            addresses = dict([(k, (self.stage_ip, v)) for k, v in addresses.items()])
            addresses.update(CAL_DEV_ADDRESSES)
            if self.config:
                self.address_book = AddressBook(
                    [self.config.service_addrs, addresses], self.config.aliases)
            else:
                self.address_book = AddressBook([addresses])

    def set_config(self, config):
        self.config = config

        if self.stage_host:
            addresses = self.stage_address_map.get_host_map(self.stage_ip)
            addresses = dict([(k, (self.stage_ip, v)) for k, v in addresses.items()])
            addresses.update(CAL_DEV_ADDRESSES)
            self.address_book = AddressBook(
                [config.service_addrs, addresses], config.aliases)
        else:
            self.address_book = AddressBook(
                [config.service_addrs], config.aliases)

    def get_mayfly(self, name, namespace):
        try:
            ip, port = self.address_book.mayfly_addr(name)
        except KeyError:
            raise ValueError('Unknown Mayfly: '+repr(name))

        import mayfly
        return mayfly.Client(ip, port, self.appname, namespace)

    def make_occ(self, name):
        'make instead of get to indicate this is creating a stateful object'
        try:
            ip, port = self.address_book.occ_addr(name)
        except KeyError:
            raise ValueError('Uknonwn OCC: '+repr(name))

        import occ
        return occ.Connection(ip, port, self.protected)

    def get_addr(self, name):
        return self.address_book[name]

    @property
    def dev(self):
        return self._dev

    @property
    def appname(self):
        if self.config:
            return self.config.appname
        return "pyinfra"


# A set of *Conf classes representing the configuration of different things.
Address = namedtuple('Address', 'ip port')


class AddressBook(object):
    '''
    Responsible for everything to do with finding the ip and port for something
    at runtime.
    First, applies aliasing.
    Then, looks down address_chain for a match to a given key.
    '''
    'simple key-value store for addresses'
    def __init__(self, address_chain, aliases={}):
        self.address_chain = address_chain
        self.aliases = aliases

    def __getitem__(self, key):
        if key in self.aliases:
            realkey = self.aliases[key]
        else:
            realkey = key
        for addresses in self.address_chain:
            if realkey in addresses:
                return addresses[realkey]
        msg = "No address for "+repr(key)
        if realkey != key:
            msg += " (aliased to "+repr(realkey)+") "
        raise ValueError(msg)

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


CONTEXT = None 


def get_context():
    global CONTEXT
    if CONTEXT is None:
        CONTEXT = Context()
    return CONTEXT


def set_context(context):
    global CONTEXT
    CONTEXT = context


# see: https://confluence.paypal.com/cnfl/display/CAL/CAL+Quick+Links
CAL_DEV_ADDRESSES = {
    'cal-stage' : ('10.57.2.159', 1118), #cal-stage.qa.paypal.com
    'cal-qa' : ('10.57.2.152', 1118), #cal-qa.qa.paypal.com
    'cal-dev': ('10.57.2.157', 1118), #cal-dev.qa.paypal.com
}

