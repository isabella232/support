'''
Quick and limited Redis client.
'''
import context
import socket


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
        if resp.startswith("-"):  # error string
            error, _ = _recv_until(sock, resp, '\r\n')
            raise RedisError(error[1:])
        elif resp.startswith('+'):  # simple string
            resp, _ = _recv_until(sock, resp, '\r\n')
            resp = resp[1:]
        elif resp.startswith('$'):  # bulk string
            length, rest = _recv_until(sock, resp, '\r\n')
            length = int(length[1:])
            if length == -1:
                return None
            resp, _ = _recv_all(sock, rest, length)
        cm.release_connection(sock)
        return resp

    def set(self, key, value):
        resp = self.call("SET", key, value)
        if resp != "OK":
            raise RedisError("unexpected response: " + repr(resp))

    def get(self, key):
        return self.call("GET", key)


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


def _recv_until(sock, sofar, marker):
    'read off of socket and sofar until the marker is found'
    chunks = []
    nxt = sofar or sock.recv(32 * 1024)
    if not sofar:
        context.get_context().store_network_data(
            (sock.getsockname(), sock.getpeername()), sock.fileno(), "IN", nxt)
    while nxt and marker not in nxt:
        chunks.append(nxt)
        nxt = sock.recv(32 * 1024)
        context.get_context().store_network_data(
            (sock.getsockname(), sock.getpeername()), sock.fileno(), "IN", nxt)
    if not nxt:
        raise socket.error('connection was closed')
    val, _, rest = nxt.partition(marker)
    return ''.join(chunks) + val, rest


def _recv_all(sock, sofar, size):
    'read off of socket and sofar until size bytes have been read'
    chunks = []
    total_bytes = 0
    nxt = sofar or sock.recv(32 * 1024)
    if not sofar:
        context.get_context().store_network_data(
            (sock.getsockname(), sock.getpeername()), sock.fileno(), "IN", nxt)
    while nxt:
        total_bytes += len(nxt)
        if total_bytes >= size:
            break
        chunks.append(nxt)
        nxt = sock.recv(32 * 1024)
        context.get_context().store_network_data(
            (sock.getsockname(), sock.getpeername()), sock.fileno(), "IN", nxt)
    else:
        raise socket.error('connection was closed')
    extra_bytes = total_bytes - size
    if extra_bytes:
        last, rest = nxt[:-extra_bytes], nxt[-extra_bytes:]
    else:
        last, rest = nxt, None
    chunks.append(last)
    return ''.join(chunks), rest
