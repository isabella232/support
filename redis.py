'''
Quick and limited Redis client.
'''
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
        sock = cm.get_connection(self.address)
        # ARRAY: first byte *, decimal length, \r\n, contents
        out = ['*' + str(len(commands))] + \
            ["${0}\r\n{1}".format(len(e), e) for e in commands]
        out = "\r\n".join(out) + "\r\n"
        sock.send(out)
        resp = sock.recv(4096)
        if resp.startswith("-"):
            raise RedisError(resp[1:])
        cm.release_connection(sock)
        return resp

    def set(self, key, value):
        resp = _parse(self.call("SET", key, value))
        if resp != "OK":
            raise RedisError("unexpected response: " + repr(resp))

    def get(self, key):
        return _parse(self.call("GET", key))


# TODO: make this into a loop that consumes and parses data better
def _parse(data):
    if data[0] == '+':
        assert data.endswith('\r\n')
        return data[1:-2]
    if data[0] == '$':
        length, _, value = data[1:].partition('\r\n')
        if length == "-1":
            return None
        return value[:int(length)]


class RedisError(ValueError):
    pass
