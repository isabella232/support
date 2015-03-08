# SuPPort

An evented server framework designed for building scalable
and introspectable services. Built at PayPal, based on gevent.


## Introducing SuPPort: Evented Server Framework for Python

- gevent is powerful, but with power comes complexity
- support is one way gevent has been used to scale and works for applications with a hundred requests per day or a hundred requests per second
- most of all we hope that it's an educational design entry for anyone choosing between evented architectures in Python

### How to get SuPPort

```
pip install SuPPort
```

You'll also need the following system libraries for the pip install to
go through smoothly:

 - libssl-dev
 - libffi-dev

### How to use SuPPort

The main entry point into SuPPort is the Group:

```
import support

server_group = support.Group()

server_group.serve_forever()
```

Support is completely programmatically configured, so just take a look at the docstring

### When to use SuPPort

If you need a simple, but tested, preforking webserver to host a WSGI
application, use SuPPort. If you need to write servers for non-web
protocols, also use SuPPort. If you have a backend service, WSGI or
otherwise, SuPPort is much more convenient than setting up
Apache/mod_wsgi or other more complex server environments.

### Design highlights

Even though it represents only a fraction of our Python infrastructure
codebase, SuPPort still encompasses too many learnings, big and small,
to list here. That said, there are a few aspects of the design worth
highlighting.

#### Threadpool usage

Threadpool usage -> "What? I thought the whole point of greenlets and
gevent was to eliminate evil evil threads," I hear the strawmen
say. False, greenlet are excellent and powerful, and we want to add
that power to our process and thread-based world. There's no point
running from standard OS capabilities; threads still have their
place. We decided we didn't want n threads, we wanted k threads. Many
architectures adopt a thread-per-request or process-per-request model,
but we don't want our number of threads (and thread-related contention
and overhead) going up as load increases. Greenthreads are much lower
overhead. Furthermore, it's not about parallelism, it's about
preemption. Cooperative threads are much more efficient, but sometimes
you really do need guarantees about responsiveness.

There are two built-in Threadpools with decorators worth highlighting,
io_bound and cpu_bound:

##### io_bound

Used to wrap opaque clients built without affordances for cooperative
concurrent IO. We use this to wrap cx_Oracle and other C-based clients.

##### cpu_bound

Used to wrap expensive operations that would halt the event loop for
too long. We use it to wrap long-running cryptiography and
serialization tasks, such as decrypting private SSL certificates or
loading huge blobs of XML and JSON. It's worth noting that some
deserialization tasks are not worth the overhead of dispatching to a
separate thread, if for instance, the data is shorter than 256
bytes. For these cases, we have the `cpu_bound_if` decorator, which
conditionally dispatches to the thread, yielding slightly higher
responsiveness for low-complexity requests.

#### Global state management with contexts

Python famously has no global scope: all values are namespaced in
module scope. But there are still plenty of aspects of the runtime
that are global. Some are out of our control, like the OS-assigned
process ID, or the VM-managed garbage collection counters. Others
aspects are in our control, and best practice in concurrent
programming is to keep these as well-managed as possible.

#### No monkeypatching

One of the first and sometimes only ways that people experience gevent
is through monkeypatching. At the top of your main module you issue a
call to gevent that automatically swaps out virtually all system
libraries with their cooperatively concurrent ones. This sort of magic
is relatively rare in Python programming, and rightfully so. Implicit
activities like this can have unexpected consequences. Support is a
no-monkeypatching approach to gevent. If you want to implement your
own network-level code, it is best to use gevent.socket directly. If
you want gevent-incompatible libraries to work with gevent, best to
use SuPPort's gevent-based threadpooling capabilities.

#### Connection Management

Errors happen in live environments.  DNS requests fail.  Packets are lost.
Latency spikes.  TCP handshakes are slow.  SSL hanshakes are slower.
Clients rarely handle these problems gracefully.  
This is why SuPPort includes the connection manager, which provides a robust
error handling code for all of these cases with consistent logging
and monitoring.  It also provides a central
point of configuration for timeouts, and host fallbacks.

Small teams and companies can benefit from the lessons learned taking
Python to 10s of millions of requests per day at PayPal.

### What's next
