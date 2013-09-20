import socket
import time
import select

import ll
ml = ll.LLogger()


class SockPool(object):
    def __init__(self, timeout=0.25):
        import async  # breaks circular dependency

        self.timeout = timeout
        self.free_socks_by_addr = {}
        self.sock_idle_times = {}
        self.killsock = async.killsock

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
        except socket.error:
            return #if socket was closed, select will raise socket.error('Bad file descriptor')
        addr = sock.getpeername()
        self.free_socks_by_addr.setdefault(addr, []).append(sock)
        self.sock_idle_times[sock] = time.time()
        
    def cull(self):
        #cull sockets which are in a bad state
        culled = []
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
