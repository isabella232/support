import gevent


_UNSET = object()


class BufferedSocket(object):
    def __init__(self, sock, timeout=10, maxbytes=32 * 1024):
        self.sock = sock
        self.sock.settimeout(None)
        self.rbuf = ""
        self.sbuf = []
        self.timeout = timeout
        self.maxbytes = maxbytes

    def settimeout(self, timeout):
        self.timeout = timeout

    def setmaxbytes(self, maxbytes):
        self.maxbytes = maxbytes

    def recv(self, size, flags=0, timeout=_UNSET):
        if timeout is _UNSET:
            timeout = self.timeout
        if flags:
            raise ValueError("flags not supported")
        if len(self.rbuf) >= size:
            data, self.rbuf = self.rbuf[:size], self.rbuf[size:]
            return data
        size -= len(self.rbuf)
        with gevent.Timeout(timeout, Timeout(timeout)):
            data = self.rbuf + self.sock.recv(size)
        # don't empty buffer till after network communication is complete,
        # to avoid data loss on transient / retry-able errors (e.g. read
        # timeout)
        self.rbuf = ""
        return data

    def peek(self, n, timeout=None):
        'peek n bytes from the socket, but keep them in the buffer'
        if len(self.rbuf) >= n:
            return self.rbuf[:n]
        data = self.recv_all(n, timeout=timeout)
        self.rbuf = data + self.rbuf
        return data

    def recv_until(self, marker, timeout=_UNSET, maxbytes=_UNSET):
        'read off of socket until the marker is found'
        if maxbytes is _UNSET:
            maxbytes = self.maxbytes
        if timeout is _UNSET:
            timeout = self.timeout
        chunks = []
        recvd = 0
        nxt = ''
        try:
            with gevent.Timeout(timeout, False):
                nxt = self.rbuf or self.sock.recv(32 * 1024)
                while nxt and marker not in nxt:
                    chunks.append(nxt)
                    recvd += len(nxt)
                    if maxbytes is not None and recvd > maxbytes:
                        raise NotFound(marker, recvd)
                    nxt = self.sock.recv(32 * 1024)
                if not nxt:
                    raise ConnectionClosed(
                        'connection closed after reading {0} bytes without'
                        ' finding symbol {1}'.format(recvd, marker))
            if marker not in nxt:
                raise Timeout(
                    timeout, 'read {0} bytes without finding symbol {1}'.format(
                        recvd, marker))
        except:  # in case of error, retain data read so far in buffer
            self.rbuf = ''.join(chunks)
            raise
        val, _, self.rbuf = nxt.partition(marker)
        return ''.join(chunks) + val

    def recv_all(self, size, timeout=_UNSET):
        'read off of socket until size bytes have been read'
        if timeout is _UNSET:
            timeout = self.timeout
        chunks = []
        total_bytes = 0
        try:
            with gevent.Timeout(timeout, False):
                nxt = self.rbuf or self.sock.recv(size)
                while nxt:
                    total_bytes += len(nxt)
                    if total_bytes >= size:
                        break
                    chunks.append(nxt)
                    nxt = self.sock.recv(size - total_bytes)
                else:
                    raise ConnectionClosed(
                        'connection was closed after reading'
                        ' {0} of {1} bytes'.format(total_bytes, size))
            if total_bytes < size:
                raise Timeout(
                    timeout, 'read {0} of {1} bytes'.format(total_bytes, size))
        except:  # in case of error, retain data read so far in buffer
            self.rbuf = ''.join(chunks)
            raise
        extra_bytes = total_bytes - size
        if extra_bytes:
            last, self.rbuf = nxt[:-extra_bytes], nxt[-extra_bytes:]
        else:
            last, self.rbuf = nxt, ""
        chunks.append(last)
        return ''.join(chunks)

    def send(self, data, flags=0, timeout=_UNSET):
        if timeout is _UNSET:
            timeout = self.timeout
        if flags:
            raise ValueError("flags not supported")
        self.sbuf = [''.join(self.sbuf) + data]
        with gevent.Timeout(timeout, False):
            while self.sbuf[0]:
                sent = self.sock.send(data)
                self.sbuf[0] = self.sbuf[0][sent:]
        if self.sbuf[0]:
            raise Timeout(
                timeout, "{0} bytes unsent".format(len(self.sbuf[0])))

    sendall = send

    def flush(self):
        self.send('')

    def buffer(self, data):
        self.sbuf.append(data)

    def close(self):
        self.sock.close()

    def shutdown(self, how):
        self.sock.shutdown(how)
    


class Error(Exception):
    pass


class ConnectionClosed(Error):
    pass


class Timeout(Error):
    def __init__(self, timeout, extra=""):
        if timeout is None:
            super(Timeout, self).__init__('timed out ' + extra)
        else:
            super(Timeout, self).__init__(
                'timed out after {0}ms '.format(timeout * 1e3) + extra)


class NotFound(Error):
    def __init__(self, symbol, bytes_read):
        super(NotFound, self).__init__(
            'read {1} bytes without finding symbol {0}'.format(
                symbol, bytes_read))
