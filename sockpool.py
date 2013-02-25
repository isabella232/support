import socket
import time
import gevent.select

class SockPool(object):
    def __init__(self, timeout=0.25):
        self.timeout = timeout
        self.free_socks_by_addr = {}
        self.sock_idle_times = {}

    def acquire(self, addr):
        #return a free socket, if one is availble; else None
        socks = self.free_socks_by_addr.get(addr)
        if socks:
            sock = socks.pop()
            del self.sock_idle_times[sock]
            return sock
        return None
    
    def release(self, sock):
        #this is also a way of "registering" a socket with the pool
        #basically, this says "I'm done with this socket, make it available for anyone else"
        addr = sock.getpeername()
        self.free_socks_by_addr.setdefault(addr, []).append(sock)
        self.sock_idle_times[sock] = time.time()
        
    def cull(self):
        #cull sockets which have been idle for too long
        culled = []
        #sort the living from the soon-to-be-dead
        for addr in self.free_socks_by_addr:
            live = []
            for sock in self.free_socks_by_addr[addr]:
                if time.time() - self.sock_idle_times[sock] > self.timeout:
                    culled.append(sock)
                    del self.sock_idle_times[sock]
                else:
                    live.append(sock)
            self.free_socks_by_addr[addr] = live
        #shutdown all the culled sockets
        for sock in culled:
            killsock(sock)

    def ctx_sock(self, addr, create_sock):
        return CtxSock(self, addr, create_sock)

def killsock(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except socket.error:
        pass #just being nice to the server, don't care if it fails
    try:
        sock.close()
    except socket.error:
        pass #just being nice to the server, don't care if it fails

class CtxSock(object):
    def __init__(self, pool, addr, create_sock):
        self.pool = pool
        self.addr = addr
        self.create_sock = create_sock

    def __enter__(self):
        self.sock = self.pool.acquire(self.addr) or self.create_sock(self.addr)

    def __exit__(self, exc_type, exc_value, traceback):
        #do as much checking as possible to make sure the socket is clean and ready to
        #go back into the pool
        if exc_type: #if any exception was raised, don't trust socket
            killsock(self.sock) #(who knows what state the protocol is in)
            return
        if gevent.select.select([self.sock], [], [], 0)[0]:
            killsock(self.sock)
            return
            #socket is readable means one of two things:
            #1- left in a bad state (e.g. more data waiting)
            #2- socket closed by remote (in which case read will return empty string)

        self.pool.release(self.sock)




