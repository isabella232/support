import socket


class BufferedSocket(object):
    def __init__(self, bsock):
        self.bsock = bsock
        self._buf = ""

    def recv(self, size, flags=0):
        if not self._buf:
            return self.bsock.recv(size, flags)
        if len(self._buf) >= size:
            data, self._buf = self._buf[:size], self._buf[size:]
            return data
        size -= len(self._buf)
        data = self._buf + self.bsock.recv(size, flags)
        # don't empty buffer till after network communication is complete,
        # to avoid data loss on transient / retry-able errors (e.g. read
        # timeout)
        self._buf = ""
        return data

    def peek(self, n):
        'peek n bytes from the socket, but keep them in the buffer'
        if len(self._buf) >= n:
            return self._buf[:n]
        data = self.recv_all(n)
        self._buf = data + self._buf
        return data

    def recv_until(self, marker):
        'read off of socket until the marker is found'
        chunks = []
        try:
            nxt = self._buf or self.bsock.recv(32 * 1024)
            while nxt and marker not in nxt:
                chunks.append(nxt)
                nxt = self.bsock.recv(32 * 1024)
            if not nxt:
                raise socket.error('connection was closed')
        except:  # in case of error, retain data read so far in buffer
            self._buf = ''.join(chunks)
            raise
        val, _, self._buf = nxt.partition(marker)
        return ''.join(chunks) + val

    def recv_all(self, size):
        'read off of socket until size bytes have been read'
        chunks = []
        total_bytes = 0
        try:
            nxt = self._buf or self.bsock.recv(size)
            while nxt:
                total_bytes += len(nxt)
                if total_bytes >= size:
                    break
                chunks.append(nxt)
                nxt = self.bsock.recv(size - total_bytes)
            else:
                raise socket.error('connection was closed')
        except:  # in case of error, retain data read so far in buffer
            self._buf = ''.join(chunks)
            raise
        extra_bytes = total_bytes - size
        if extra_bytes:
            last, self._buf = nxt[:-extra_bytes], nxt[-extra_bytes:]
        else:
            last, self._buf = nxt, ""
        chunks.append(last)
        return ''.join(chunks)

    def __getattr__(self, attr):
        return getattr(self.bsock, attr)
