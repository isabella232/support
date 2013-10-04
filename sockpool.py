'''
Protocol agnostic socket pooler.

This code is both extremely tested and hard to test.
Modify with caution :-)

"There are two ways of constructing a software design: 
One way is to make it so simple that there are obviously no deficiencies, 
and the other way is to make it so complicated that there are no obvious deficiencies."
-CAR Hoare, 1980 Turing Award lecture

In particular: it is tempting to attempt to auto-reconnect and re-try at this layer.
This is not possible to do correctly however, since only the protocol aware clients
know what a retry entails.  (e.g. SSL handshake, reset protocol state)
'''
import socket
import time
import select

import ll
ml = ll.LLogger()


class SockPool(object):
    def __init__(self, timeout=0.25, max_sockets=800):
        import async  # breaks circular dependency

        self.timeout = timeout
        self.free_socks_by_addr = {}
        self.sock_idle_times = {}
        self.killsock = async.killsock
        self.total_sockets = 0
        self.max_socks_by_addr = {}  # maximum sockets on an address-by-address basis
        self.default_max_socks_per_addr = 50
        self.max_sockets = 800

    def acquire(self, addr):
        #return a free socket, if one is availble; else None
        self.cull()
        socks = self.free_socks_by_addr.get(addr)
        if socks:
            sock = socks.pop()
            del self.sock_idle_times[sock]
            ml.ld("Getting sock {0}", str(id(sock)))
            return sock
        return None
    
    def release(self, sock):
        #this is also a way of "registering" a socket with the pool
        #basically, this says "I'm done with this socket, make it available for anyone else"
        ml.ld("Returning sock {0}", str(id(sock)))
        try:
            if select.select([sock], [], [], 0)[0]:
                self.killsock(sock)
                return #TODO: raise exception when handed messed up socket?
                #socket is readable means one of two things:
                #1- left in a bad state (e.g. more data waiting -- protocol state is messed up)
                #2- socket closed by remote (in which case read will return empty string)
        except:
            return #if socket was closed, select will raise socket.error('Bad file descriptor')
        addr = sock.getpeername()
        addr_socks = self.free_socks_by_addr.setdefault(addr, [])
        self.total_sockets += 1
        addr_socks.append(sock)
        self.sock_idle_times[sock] = time.time()
        # check if there are too many sockets and one needs to be removed
        culled = None  # socket pushed out
        # handle case of too many sockets for this address
        if len(addr_socks) >= self.max_socks_by_addr.get(addr, self.default_max_socks_per_addr):
            culled = max([(self.sock_idle_times[a], a) for a in addr_socks])[1]
        # handle case of too many sockets total
        elif self.total_sockets >= self.max_sockets:
            culled = max([(v, k) for k,v in self.sock_idle_times.iteritems()])[1]
        if culled:
            self.total_sockets -= 1
            self.free_socks_by_addr[culled.getpeername()].remove(culled)
            del self.sock_idle_times[culled]

        
    def cull(self):
        #cull sockets which are in a bad state
        culled = []
        self.total_sockets = 0
        #sort the living from the soon-to-be-dead
        for addr in self.free_socks_by_addr:
            live = []
            # STEP 1 - CULL IDLE SOCKETS
            for sock in self.free_socks_by_addr[addr]:
                if time.time() - self.sock_idle_times[sock] > self.timeout:
                    ml.ld("Going to Close sock {{{0}}}/FD {1}",
                          id(sock), sock.fileno())
                    culled.append(sock)
                else:
                    live.append(sock)
            # STEP 2 - CULL READABLE SOCKETS
            if live:  # (if live is [], select.select() would error)
                readable = set(select.select(live, [], [], 0)[0])
                # if a socket is readable that means one of two bad things:
                # 1- the socket has been closed (and sock.recv() would return '')
                # 2- the server has sent some data which no client has claimed
                #       (which will remain in the recv buffer and mess up the next client)
                live = [s for s in live if s not in readable]
                culled.extend(readable)
            self.free_socks_by_addr[addr] = live
            self.total_sockets += len(live)
        # shutdown all the culled sockets
        for sock in culled:
            del self.sock_idle_times[sock]
            self.killsock(sock)


class NullSockPool(object):
    def __init__(self):
        import async  # breaks circular dependency

        self.killsock = async.killsock

    def acquire(sock, addr):
        return None

    def release(self, sock):
        self.killsock(sock)

    def cull(self):
        return
