'''
Implementation of the STOMP (Streaming/Simple Text Oriented Message Protocol).

Used at PayPal for YAM, LAR, and possibly other message systems.
'''
import collections

from gevent import socket
import gevent.queue


class ClientConn(object):
    '''
    Represents a client connection.
    The client must be tied to a connection because it may recieve messages
    from the server at any time.
    '''
    def __init__(self, address, on_message=None, on_reciept=None, on_error=None):
        self.address = address
        if on_message:
            self.on_message = on_message
        if on_reciept:
            self.on_reciept = on_reciept
        if on_error:
            self.on_error = on_error
        self.send_q = gevent.queue.Queue()
        self.sock = None
        self.sock_ready = gevent.event.Event()
        self.sock_broken = gevent.event.Event()
        self.stopping = False
        self.started = False
        self.send_glet = None
        self.recv_glet = None
        self.sock_glet = None
    
    def send(self):
        pass

    def subscribe(self):
        pass

    def unsubscribe(self):
        pass

    def begin(self):
        raise NotImplementedError("STOMP transactions not supported")

    def commit(self):
        raise NotImplementedError("STOMP transactions not supported")

    def abort(self):
        raise NotImplementedError("STOMP transactions not supported")

    def ack(self):
        pass

    def nack(self):
        pass

    def disconnect(self, timeout=10):
        disconnect = Frame()
        self.send_q.put(disconnect)
        self.wait("RECIEPT")  # wait for a reciept from server acknowledging disconnect

    def start(self):
        self.send_glet = gevent.spawn(self._run_send)
        self.recv_glet = gevent.spaen(self._run_recv)
        self.sock_glet = gevent.spawn(self._run_socket_fixer)
        self._reconnect()
        connect = Frame()  # initial connect frame
        self.sock.sendall(connect.serialize())


    def stop(self):
        self.stopping = True

    def wait(self, msg_id, timeout=10):
        '''
        pause the current greenlet until either timeout has passed,
        or a message with a given message id has been recieved

        the purpose of this funciton is to simplify "call-response"
        client implementations
        '''
        pass

    def _reconnect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.address)

    def _run_send(self):
        while not self.stopping:
            try:
                cur = self.send_q.peek()
                self.sock.sendall(cur.serialize())
                self.send_q.get()
            except socket.error:
                # wait for socket ready again
                self.sock_broken.set()
                self.sock_ready.wait()

    def _run_recv(self):
        while not self.stopping:
            try:
                cur = Frame.parse_from_socket(self.sock)
                if cur.command == 'HEARTBEAT':
                    # discard
                    pass
                if cur.command == 'SEND':
                    pass

                if cur.command == "MESSAGE":
                    if 'ack' in cur.headers:
                        ack = Frame()
                        self.send_q.put(ack)

            except socket.error:
                # wait for socket to be ready again
                self.sock_broken.set()
                self.sock_ready.wait()

    def _run_socket_fixer(self):
        while not self.stopping:
            self.sock_broken.wait()
            try:
                self._reconnect()
                self.sock_ready.set()
            except:
                pass


class Server(object):
    def __init__(self):
        pass


class Frame(collections.namedtuple("STOMP_Frame", "command headers body")):
    def __new__(cls, command, headers, body=""):
        return super(cls, Frame).__new__(cls, command, headers, body)

    def serialize(self):
        if self.body and 'content-length' not in self.headers:
            self.headers['content-length'] = len(self.body)
        return (self.command + '\n' + '\n'.join(
            ['{0}:{1}'.format(k, v) for k,v in self.headers.items()])
            + '\n\n' + self.body + '\0')

    @classmethod
    def _parse_iter(cls):
        '''
        parse data in an iterator fashion;
        this is intended to allow clean separation of socket
        code from protocol code
        '''
        data = yield
        # TODO: are hearbeats null delimeted as well?
        # should this be '\x0a\0' ?
        if data[0] == '\x0a':
            yield cls('HEARTBEAT', None), 1

        consumed = 0
        sofar = []
        while '\n' not in data:
            consumed += len(data)
            sofar.append(data)
            data = yield None, consumed
            consumed = 0
        end, _, data = data.partition('\n')
        consumed += len(end) + len(_)
        sofar.append(end)
        command = ''.join(sofar)

        headers = {}
        sofar = []
        while '\n\n' not in data:
            sofar.append(data)
            consumed += len(data)
            data = yield None, consumed
            consumed = 0
        sofar.append(data)
        data = ''.join(sofar)
        header_str, _, data = data.partition('\n\n')
        consumed += len(header_str) + len(_)
        for cur_header in header_str.split('\n'):
            key, _, value = cur_header.partition(':')
            headers[key] = value

        sofar = []
        if 'content-length' in headers:
            bytes_to_go = headers['content-length'] + 1
            while len(data) - consumed < bytes_to_go:
                sofar.append(data)
                consumed += len(data)
                bytes_to_go -= len(data)
                data = yield None, consumed
                consumed = 0
            end_of_body, leftover = data[:bytes_to_go], data[bytes_to_go:]
            if end_of_body[-1] != '\0':
                raise ValueError('Frame not terminated with null byte.')
            consumed += len(end_of_body)
            end_of_body = end_of_body[:-1]
        else:
            while '\0' not in data:
                sofar.append(data)
                consumed += len(data)
                data = yield None, consumed
                consumed = 0
            end_of_body, _, leftover = data.partition('\0')
            consumed += len(end_of_body) + len(_)
        sofar.append(end_of_body)
        body = ''.join(sofar)
        yield Frame(command, headers, body), consumed
        raise StopIteration()

    @classmethod
    def parse(cls, data):
        parser = cls._parse_iter()
        parser.next()
        frame, bytes_consumed = parser.send(data)
        if bytes_consumed != len(data):
            raise ValueError("Excess data passed to stomp.Frame.parse(): "
                             + repr(data[bytes_consumed:])[:100])
        if frame is None:
            raise ValueError("Incomplete frame passed to stomp.Frame.parse():. "
                             "Consumed {0} bytes.".format(bytes_consumed))
        return frame

    @classmethod
    def parse_from_socket(cls, sock):
        parser = cls._parse_iter()
        parser.next()
        cur_data = sock.recv(4096, socket.MSG_PEEK)
        frame, bytes_consumed = parser.send(cur_data)
        while frame is None:
            sock.recv(bytes_consumed)  # throw away consumed data
            cur_data = sock.recv(4096, socket.MSG_PEEK)
            frame, bytes_consumed = parser.send(cur_data)
        return frame

