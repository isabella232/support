'''
Implementation of the STOMP (Streaming/Simple Text Oriented Message Protocol).

Used at PayPal for YAM, LAR, and possibly other message systems.
'''
import collections
import weakref
import random

from gevent import socket
import gevent.queue

import context
import async

import ll
ml = ll.LLogger()


class Connection(object):
    '''\
    Represents a STOMP connection.  Note that the distinction between
    client and server in STOMP is fuzzy.

    This connection is a "client" in that it may SEND data
    synchronously to the broker.

    This connection is also a "server" in that it may get MESSAGE,
    RECEIPT, or ERROR frames from the broker at any time.
    '''
    def __init__(self, address, login="", passcode="",
                 on_message=None, on_reciept=None, on_error=None, ssl=True):
        self.ssl = ssl
        self.address = address
        self.login = login
        self.passcode = passcode
        self.on_message = on_message
        self.on_reciept = on_reciept
        self.on_error = on_error
        self.send_q = gevent.queue.Queue()
        self.sock = None
        # a bit of indirection for closing sock after GC
        self.sock_container = [self.sock]
        self.sock_ready = gevent.event.Event()
        self.sock_broken = gevent.event.Event()
        ml.ld2("init Setting sock broken")
        self.sock_broken.set()
        self.stopping = False
        self.started = False
        self.send_glet = None
        self.recv_glet = None
        self.sock_glet = None
        self.session = None
        self.server_info = None
        self.msg_id = 0
        self.sub_id = 0
        self.no_receipt = set()  # send ids for which there was no receipt
        self.start()

    def send(self, destination, body="", extra_headers=None):
        headers = {
            "destination": destination,
            "receipt": self.msg_id
        }
        self.no_receipt.add(self.msg_id)
        self.msg_id += 1
        # NOTE: STOMP 1.0, no content-type
        # if body:
        #     headers['content-type'] = 'text/plain'
        if extra_headers:
            headers.update(extra_headers)
        log_rec = context.get_context().log.info('STOMP', 'ENQUEUE')
        log_rec.success(length=len(body))
        self.send_q.put(Frame("SEND", headers, body))

    def subscribe(self, destination):
        self.sub_id += 1
        headers = {'subscription': destination,
                   'id': self.sub_id,
                   'ack': 'auto'}
        self.send_q.put(Frame("SUBSCRIBE", headers))

    def unsubscribe(self):
        raise NotImplementedError("IOU subscriptions")

    def begin(self):
        raise NotImplementedError("STOMP transactions not supported")

    def commit(self):
        raise NotImplementedError("STOMP transactions not supported")

    def abort(self):
        raise NotImplementedError("STOMP transactions not supported")

    def ack(self):
        raise NotImplementedError("handled implicitly")

    def nack(self):
        raise NotImplementedError("handled implicitly")

    def disconnect(self, timeout=10):
        self.send_q.put(Frame("DISCONNECT", {}))
        # wait for a reciept from server acknowledging disconnect
        self.wait("RECEIPT")

    def send_frame(self, frame):
        '''
        Send a raw Frame.  Warning -- this may break the STOMP state.
        As a simple example, a DISCONNECT frame could be sent this way.
        '''
        self.send_q.put(frame)

    def start(self):
        if self.started:
            raise ValueError("called stomp.Connection.start() twice")
        self.started = True
        ml.ld2("start Setting sock broken")
        self.sock_broken.set()
        ml.ld2("start clearing sock ready")
        self.sock_ready.clear()
        weak = weakref.proxy(self, _killsock_later(self.sock_container))
        self.send_glet = gevent.spawn(_run_send, weak)
        self.recv_glet = gevent.spawn(_run_recv, weak)
        self.sock_glet = gevent.spawn(_run_socket_fixer, weak)

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
        if self.sock:
            async.killsock(self.sock)
        ctx = context.get_context()
        self.sock = ctx.get_connection(self.address, ssl=self.ssl)
        self.sock_broken.clear()
        self.sock_container[0] = self.sock
        headers = {"login": self.login, "passcode": self.passcode}
        self.sock.sendall(Frame("CONNECT", headers).serialize())
        resp = Frame.parse_from_socket(self.sock)
        if resp.command != "CONNECTED":
            raise ValueError("Expected CONNECTED frame from server, got: %r"
                             % repr(resp))
        self.session = resp.headers['session']
        self.server_info = resp.headers.get('server')
        ml.ld2("reconn clearing sock broken")
        self.sock_broken.clear()
        ml.ld2("reconn setting sock ready")
        self.sock_ready.set()
        # once sock_ready.set() happens, others will resume execution


# these are essentially methods of the Connection class; they are
# broken out to regular functions so that bound methods in greenlet
# call stacks do not interfere with garbage collection
def _run_send(self):
    ml.ld2("run send Waiting for sock ready")
    self.sock_ready.wait()
    while not self.stopping:
        stomp_log = context.get_context().log.get_logger('STOMP')
        try:
            cur = self.send_q.peek()
            serial_cur = cur.serialize()
            ml.ld2("Lar sender dequed {{{0}}}", serial_cur)
            self.sock.sendall(serial_cur)
            stomp_log.info("DEQUEUE").success(length=len(serial_cur))
            self.send_q.get()
        except socket.error as e:
            stomp_log.critical("EXCEPTION").failure(exc=repr(e), green='send')
            ml.ld2("Got exception {0!r}", e)
            # wait for socket ready again
            ml.ld2("run send clearing sock ready")
            self.sock_ready.clear()
            ml.ld2("run send setting sock broken")
            self.sock_broken.set()
            ml.ld2("run send Waiting for sock ready")
            self.sock_ready.wait()


def _run_recv(self):
    ml.ld2("run recv waiting for sock ready")
    self.sock_ready.wait()
    while not self.stopping:
        try:
            cur = Frame.parse_from_socket(self.sock)
            ml.ld2("rec recv Lar recver got {0!r}", cur)
            if cur.command == 'HEARTBEAT':
                # discard
                pass
            # print "GOT", cur.command, "\n", cur.headers, "\n", cur.body
            if cur.command == "MESSAGE":
                if 'ack' in cur.headers:
                    ack = Frame("ACK", {})
                    self.send_q.put(ack)
            if cur.command == 'RECEIPT':
                if cur.headers.get('receipt-id') in self.no_receipt:
                    self.no_receipt.remove(cur.headers.get('receipt-id'))
            if cur.command == 'ERROR':
                pass

        except socket.timeout as e:
            ml.ld2("Got exception {0!r}", e)
            pass  # expected if no traffic
        except socket.error as e:
            ml.ld2("Got exception {0!r}", e)
            stomp_log = context.get_context().log.get_logger('STOMP')
            stomp_log.critical("EXCEPTION").failure(exc=repr(e), green='recv')
            # wait for socket to be ready again
            ml.ld2("run recv clearing sock ready")
            self.sock_ready.clear()
            ml.ld2("run recv setting sock broken")
            self.sock_broken.set()
            ml.ld2("run recv Waiting for sock ready")
            self.sock_ready.wait()


def _run_socket_fixer(self):
    last_time = 0.1
    while not self.stopping:
        ml.ld2("fixer Waiting for sock broken")
        self.sock_broken.wait()
        try:
            self._reconnect()
            last_time = 0.1
        except Exception as e:
            ml.ld2("Got exception {0!r}", e)
            stomp_log = context.get_context().log.get_logger('STOMP')
            stomp_log.critical("EXCEPTION").failure(exc=repr(e), green='fixer')
            gevent.sleep(last_time + random.random())
            if last_time < 300 and self.send_q.qsize() == 0:
                last_time = last_time * 2.0
            else:
                last_time = 0.1


def _killsock_later(sock_container):
    return lambda weak: async.killsock(sock_container[0])


CLIENT_CMDS = set(["SEND", "SUBSCRIBE", "UNSUBSCRIBE", "BEGIN", "COMMIT",
                   "ABORT", "ACK", "NACK", "DISCONNECT", "CONNECT", "STOMP"])

SERVER_CMDS = set(["CONNECTED", "MESSAGE", "RECEIPT", "ERROR"])

ALL_CMDS = CLIENT_CMDS | SERVER_CMDS


class Frame(collections.namedtuple("STOMP_Frame", "command headers body")):
    def __new__(cls, command, headers, body=""):
        if command not in ALL_CMDS:
            raise ValueError("invalid STOMP command: " + repr(command) +
                " (valid commands are " + ", ".join([repr(c) for c in ALL_CMDS]) + ")")
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
        data = ""
        consumed = 0
        # clear between-message newlines
        while not data:
            data = yield None, consumed
            consumed = len(data) - len(data.lstrip('\n'))
            data = data[consumed:]
        # check for heartbeat
        if data[0] == '\x0a':
            yield cls('HEARTBEAT', None), consumed + 1
        # parse command
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
        # parse headers
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
        # parse body
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
            raise ValueError("Incomplete frame passed to stomp.Frame.parse(): "
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
        sock.recv(bytes_consumed)  # throw away consumed data
        ml.ld2("returning {0} from  parse from socket", bytes_consumed)
        return frame
