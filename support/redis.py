'''
Quick and limited Redis client.
'''
from boltons.socketutils import BufferedSocket

import context


class Client(object):
    '''
    Simple limited client for the redis protocol that works cleanly
    with event loop, participates in socket pooling and CAL logging.
    '''
    def __init__(self, address):
        self.address = address

    def call(self, *commands):
        '''
        Helper function that implements a (subset of) the RESP
        protocol used by Redis >= 1.2
        '''
        cm = context.get_context().connection_mgr
        sock = BufferedSocket(cm.get_connection(self.address))
        # ARRAY: first byte *, decimal length, \r\n, contents
        out = ['*' + str(len(commands))] + \
            ["${0}\r\n{1}".format(len(e), e) for e in commands]
        out = "\r\n".join(out) + "\r\n"
        sock.send(out)
        fbyte = sock.peek(1)
        if fbyte == "-":  # error string
            raise RedisError(sock.recv_until('\r\n'))
        elif fbyte == '+':  # simple string
            resp = sock.recv_until('\r\n')[1:]
        elif fbyte == '$':  # bulk string
            length = int(sock.recv_until('\r\n')[1:])
            if length == -1:
                resp = None
            else:
                resp = sock.recv_size(length)
        cm.release_connection(sock)
        return resp

    def set(self, key, value):
        resp = self.call("SET", key, value)
        if resp != "OK":
            raise RedisError("unexpected response: " + repr(resp))

    def get(self, key):
        return self.call("GET", key)


class RedisError(ValueError):
    pass
