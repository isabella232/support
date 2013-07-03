import socket
import time
import select

import async

class SockPool(object):
    def __init__(self, timeout=0.25):
        self.timeout = timeout
        self.free_socks_by_addr = {}
        self.sock_idle_times = {}

    def acquire(self, addr):
        #return a free socket, if one is availble; else None
        self.cull()
        socks = self.free_socks_by_addr.get(addr)
        if socks:
            sock = socks.pop()
            del self.sock_idle_times[sock]
            return sock
        return None
    
    def release(self, sock):
        #this is also a way of "registering" a socket with the pool
        #basically, this says "I'm done with this socket, make it available for anyone else"
        try:
            if select.select([sock], [], [], 0)[0]:
                async.killsock(sock)
                return #TODO: raise exception when handed messed up socket?
                #socket is readable means one of two things:
                #1- left in a bad state (e.g. more data waiting -- protocol state is messed up)
                #2- socket closed by remote (in which case read will return empty string)
        except socket.error:
            return #if socket was closed, select will raise socket.error('Bad file descriptor')
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
            async.killsock(sock)
