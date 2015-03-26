Architecture
============

Many motivations went into building up a Python stack at PayPal, but
as in any enterprise environment, we continuously aim to achieve the
following:

- Interoperability_
- Introspectability_
- Infallibility_

Of course organizations of all sizes want these features as well, but
the key difference is that large organizations like PayPal usually end
up building ***more***. All while demanding a higher degree of
redundancy and risk mitigation from their processes. This often results
in great cost in terms of both hardware and developer productivity.
Fortunately for us, Python can be very efficient in both respects.

This document takes us through an overview of SuPPort's architecture,
occasionally deep-diving into key features.  Note that if you're
unfamiliar with evented programming, nonblocking sockets, and gevent
in particular, some of this may seem quite foreign. `The gevent
tutorial`_ is a good entry point for the intermediate Python
programmer, which can be supplemented with `this well-illustrated
introduction to server architectures`_.

.. _The gevent tutorial: http://sdiehl.github.io/gevent-tutorial/
.. _this well-illustrated introduction to server architectures: http://berb.github.io/diploma-thesis/original/042_serverarch.html


Open-source foundations
-----------------------

SuPPort is an `event-driven`_ server framework designed for building
scalable and maintainable services and clients. It's built on top of
several technologies, all open-source, so before we dig into the
workings of SuPPort, we ought to showcase its underpinnings:

-  `gevent`_ - Performant networking with coroutines (`tutorial`_)
-  `greenlet`_ - Python's premiere microthreading library
-  `clastic`_ - Lightweight web framework free of global state
-  `werkzeug`_ - Python's most popular WSGI toolkit
-  `faststat`_ - Fast `streaming`_ statistics collection
-  `hyperloglog`_ - Efficient cardinality estimators (`paper`_)
-  `ufork`_ - Minimal, programmatic preforking servers
-  `lithoxyl`_ - Fast, transactional structured system instrumentation
-  `sampro`_ - Super simple `sampling profiler`_
-  `PyOpenSSL`_, `pyasn1`_, `pyjks`_, `boltons`_, and `more...`_

Some or all of these may be new to many developers, but all-in-all they
comprise a powerful set of functionality. With power comes complexity,
and while Python as a language strives for technical convergence, there
are many ways to approach the problem of developing, scaling, and
maintaining components. SuPPort is one way gevent and the libraries
above have been used to build functional services and products with
anywhere from 100 requests per day to 100 requests per second and
beyond.

.. _event-driven: https://en.wikipedia.org/wiki/Event-driven_architecture
.. _gevent: https://github.com/gevent/gevent
.. _tutorial: http://sdiehl.github.io/gevent-tutorial/
.. _greenlet: https://github.com/python-greenlet/greenlet
.. _clastic: https://github.com/mahmoud/clastic
.. _werkzeug: https://github.com/mitsuhiko/werkzeug
.. _faststat: https://github.com/doublereedkurt/faststat
.. _streaming: https://en.wikipedia.org/wiki/Streaming_algorithm
.. _hyperloglog: https://github.com/svpcom/hyperloglog
.. _paper: http://algo.inria.fr/flajolet/Publications/FlFuGaMe07.pdf
.. _ufork: https://github.com/doublereedkurt/ufork
.. _lithoxyl: https://github.com/mahmoud/lithoxyl
.. _sampro: https://github.com/doublereedkurt/sampro
.. _sampling profiler: https://en.wikipedia.org/wiki/Profiling_%28computer_programming%29#Statistical_profilers
.. _PyOpenSSL: https://github.com/pyca/pyopenssl
.. _pyasn1: http://pyasn1.sourceforge.net/
.. _pyjks: https://github.com/doublereedkurt/pyjks
.. _boltons: https://github.com/mahmoud/boltons
.. _more...: https://github.com/paypal/support/blob/master/requirements.txt


.. _interoperability:

Interoperability
----------------

Python usage here at PayPal has spread to virtually every imaginable use
case: administrative interfaces, midtier services, operations
automation, developer tools, batch jobs; you name it, Python has filled
a gap in that area. This legacy has resulted in a few rather interesting
abstractions exposed in SuPPort.

``BufferedSocket``
~~~~~~~~~~~~~~~~~~

PayPal has hundreds of services across several tiers. Interoperating
between these means having to implement over half a dozen network
protocols. The ``BufferedSocket`` type eliminated our inevitable code
duplication, handling a lot of the nitty-gritty of making a socket into
a parser-friendly data source, while retaining timeouts for keeping
communications responsive. A must-have primitive for any gevent protocol
implementer.

``ConnectionManager``
~~~~~~~~~~~~~~~~~~~~~

Errors happen in live environments. DNS requests fail. Packets are lost.
Latency spikes. TCP handshakes are slow. SSL handshakes are slower.
Clients rarely handle these problems gracefully. This is why SuPPort
includes the ``ConnectionManager``, which provides robust error handling
code for all of these cases with consistent logging and monitoring. It
also provides a central point of configuration for timeouts and host
fallbacks.

.. _introspectability:

Introspectability
-----------------

As part of a large organization, we can afford to add more machines, and
are even required to keep a certain level of redundancy and idle
hardware. And while DevOps is catching on in many larger-scale
environments, there are many cases in enterprise environments where
developers are *not allowed* to attend to their production code.

SuPPort currently comes with all the same general-purpose introspection
capabilities that PayPal Python developers enjoy, meaning that we get
you as much structured information about your application as possible
without actually requiring login privileges. Of course almost every
aspect of this is configurable, to suit a wide variety of environments
from development to production.

\ ``Context`` management
^^^^^^^^^^^^^^^^^^^^^^^^

Python famously has no global scope: all values are namespaced in module
scope. But there are still plenty of aspects of the runtime that are
global. Some are out of our control, like the OS-assigned process ID, or
the VM-managed garbage collection counters. Others aspects are in our
control, and best practice in concurrent programming is to keep these as
well-managed as possible.

SuPPort uses a system of Contexts to explicitly manage nonlocal state,
eliminating difficult-to-track implicit global state for many core
functions. This has the added benefit of creating opportunities to
centrally manage and monitor debugging data and statistics, made
available through the MetaApplication below.

(Figure 1: see the examples of charts in the the static directory)

\ ``MetaApplication``\
^^^^^^^^^^^^^^^^^^^^^^^

While not exclusively a web server framework, SuPPort leverages its
strong roots in the web to provide both a web-based user interface and
API full of useful runtime information.

(Figure 2: see the examples of the MetaApplication in the static
directory)

As you can see above, there is a lot of information exposed through this
default interface. This is partly because of restricted environments not
allowing local login on machines, and another part is the relative
convenience of a browser for most developers. Not pictured is the
feature that the same information is available in JSON format for easy
programmatic consumption. Because this application is such a rich source
of information, we recommend using SuPPort to run it on a separate port
which can be firewalled accordingly, as seen `in this example`_.

.. _infallibility:

Infallibility
-------------

At the end of the day, reliability over long periods of time is what
earns a stack approval and adoption. At this point, the SuPPort
architecture has a billion production requests under its belt here at
PayPal, but on the way we put it through the proverbial paces. At
various points, we have tested and confirmed these edge behaviors. Here
are just a few key characteristics of a well-behaved application:

-  **Gracefully sheds traffic** under load (no unbounded queues here)
-  Can and has run at **90%+ CPU load for days** at a time
-  Is **free** from framework memory leaks
-  Is **robust to** memory leakage in user code

To illustrate, a live service handling millions of requests per day had
a version of OpenSSL installed which was leaking memory on every
handshake. Thanks to preemptive worker cycling on excessive process
memory usage, no intervention was required and no customers were
impacted. The worker cycling was noted in the logs, the leak was traced
to OpenSSL, and operations was notified. The problem was fixed with the
next regularly scheduled release rather than being handled as a crisis.

No monkeypatching
^^^^^^^^^^^^^^^^^

One of the first and sometimes only ways that people experience gevent
is through `monkeypatching`_. At the top of your main module `you issue
a call to gevent`_ that automatically swaps out virtually all system
libraries with their cooperatively concurrent ones. This sort of magic
is relatively rare in Python programming, and rightfully so. Implicit
activities like this can have unexpected consequences. SuPPort is a
no-monkeypatching approach to gevent. If you want to implement your own
network-level code, it is best to use ``gevent.socket`` directly. If you
want gevent-incompatible libraries to work with gevent, best to use
SuPPort's gevent-based threadpooling capabilities, detailed below:

Using threads with gevent
^^^^^^^^^^^^^^^^^^^^^^^^^

.. raw:: html

   <blockquote>

"Threads? In my gevent? I thought the whole point of greenlets and
gevent was to eliminate evil, evil threads!" --Countless strawmen

.. raw:: html

   </blockquote>

Originating in `Stackless`_ and ported over in 2004 by `Armin Rigo`_ (of
`PyPy`_ fame), `greenlets`_ are mature and powerful concurrency
primitives. We wanted to add that power to the process- and thread-based
world of POSIX. There's no point running from standard OS capabilities;
threads have their place. Many architectures adopt a `thread-per-request
or process-per-request model`_, but the last thing we want is the number
of threads going up as load increases. Threads are expensive; each
thread adds a bit of contention to the mix, and in many environments the
memory overhead alone, typically 4-8MB per thread, presents a problem.
At just a few kilobytes apiece, greenlet's microthreads are three orders
of magnitude less costly.

Furthermore, thread usage in our architecture is hardly about
parallelism; we use worker processes for that. In the SuPPort world,
threads are about preemption. Cooperative greenlets are much more
efficient overall, but sometimes you really do need guarantees about
responsiveness.

One excellent example of how threads provide this responsiveness is the
```ThreadQueueServer```_ detailed below. But first, there are two
built-in ``Threadpools`` with decorators worth highlighting,
``io_bound`` and ``cpu_bound``:

\ ``io_bound``\
''''''''''''''''

This decorator is primarily used to wrap opaque clients built without
affordances for cooperative concurrent IO. We use this to wrap
```cx_Oracle```_ and other C-based clients that are built for
thread-based parallelization. Other major use cases for ``io_bound`` is
when getting input from standard input (``stdin``) and files.

(Figure 3: see ``worker_closeup.png`` in the static directory)

\ ``cpu_bound``\
'''''''''''''''''

The ``cpu_bound`` decorator is used to wrap expensive operations that
would halt the event loop for too long. We use it to wrap long-running
cryptography and serialization tasks, such as decrypting private SSL
certificates or loading huge blobs of XML and JSON. Because the majority
of use cases' implementations do not release the Global Interpreter
Lock, the ``cpu_bound`` ThreadPool is actually just a pool of one
thread, to minimize CPU contention from multiple unparallelizable
CPU-intensive tasks.

It's worth noting that some deserialization tasks are not worth the
overhead of dispatching to a separate thread. If the data to be
deserialized is very short or a result is already cached. For these
cases, we have the ``cpu_bound_if`` decorator, which conditionally
dispatches to the thread, yielding slightly higher responsiveness for
low-complexity requests.

Also note that both of these decorators are reentrant, making dispatch
idempotent. If you decorate a function that itself eventually calls a
decorated function, performance won't pay the thread dispatch tax twice.

\ ``ThreadQueueServer``\
'''''''''''''''''''''''''

The ``ThreadQueueServer`` exists as an enhanced approach to pulling new
connections off of a server's listening socket. It's SuPPort's way of
incorporating an industry-standard practice, commonly associated with
nginx and Apache, into the gevent WSGI server world.

If you've read this far into the post, you're probably familiar with
`the standard multi-worker preforking server architecture`_; a parent
process opens a listening socket, forks one or more children that
inherit the socket, and the kernel manages which worker gets which
incoming client connection.

(Figure 4: see ``basic_prefork_workers.png`` in the static directory)

The problem with this approach is that it generally results in
inefficient distribution of connections, and can lead to some workers
being overloaded while others have cycles to spare. Plus, all worker
processes are woken up by the kernel in a race to accept a single
inbound connection, in what's commonly referred to as `*the thundering
herd*`_.

The solution implemented here uses a thread that sleeps on accept,
removing connections from the kernel's listen queue as soon as possible,
then explicitly pushing accepted connections to the main event loop. The
ability to inspect this user-space connection queue enables not only
even distribution but also intelligent behavior under high load, such as
closing incoming connections when the backlog gets too long. This
fail-fast approach prevents the kernel from holding open
fully-established connections that cannot be reached in a reasonable
amount of time. This backpressure takes the wait out of client failure
scenarios leading to a more responsive extrinsic system, as well.
