# Introducing SuPPort

In our last post, [Ten Myths of Enterprise Python][ten_myths], we
promised a deeper dive into how our Python Infrastructure works here
at PayPal and eBay. Thing is, there are only so many details we can
cover, and at the end of the day, it's so much better to show than to
say.

So without further ado, I'm pleased to introduce
[**SuPPort**][support_github], an in-development distillation of our
PayPal Python Infrastructure.

[ten_myths]: https://www.paypal-engineering.com/2014/12/10/10-myths-of-enterprise-python/
[support_github]: https://github.com/paypal/support

Started in 2010, Python Infrastructure initially powered PayPal's
internal price-setting interfaces, then payment orchestration
interfaces, and now in 2015 supports dozens of projects at PayPal and
eBay, having handled billions of production-critical requests and much
more. So what does it mean to distill this functionality into SuPPort?

SuPPort is an evented server framework designed for building scalable
and maintainable services and clients, built with several open-source
technologies:

  * [gevent][gevent_gh] - Performant networking with coroutines
  * [greenlet][greenlet_gh] - Python's premiere userspace threading (greenthread) library
  * [clastic][clastic_gh] - Lightweight web framework built on top of Werkzeug
  * [werkzeug][werkzeug_gh] - The WSGI toolkit
  * [faststat][faststat_gh] - Fast online statistics collection
  * [hyperloglog][hyperlog_gh] - Efficient cardinality estimators
  * [ufork][ufork_gh] - Minimal, programmatic preforking servers
  * [lithoxyl][lithoxyl_gh] - Fast, transactional structured system instrumentation
  * [sampro][sampro_gh] - Super simple [sampling profiler][sampling_prof_wp]
  * [PyOpenSSL][pyopenssl_gh], [pyasn1][pyasn1_sf], [pyjks][pyjks_gh],
    [boltons][boltons_gh], and [more][requirements_txt]

[gevent_gh]: https://github.com/gevent/gevent
[greenlet_gh]: https://github.com/python-greenlet/greenlet
[clastic_gh]: https://github.com/mahmoud/clastic
[werkzeug_gh]: https://github.com/mitsuhiko/werkzeug
[faststat_gh]: https://github.com/doublereedkurt/faststat
[hyperloglog_gh]: https://github.com/svpcom/hyperloglog
[ufork_gh]: https://github.com/doublereedkurt/ufork
[lithoxyl_gh]: https://github.com/mahmoud/lithoxyl
[sampro_gh]: https://github.com/doublereedkurt/sampro
[pyopenssl_gh]: https://github.com/pyca/pyopenssl
[pyasn1_sf]: http://pyasn1.sourceforge.net/
[pyjks_gh]: https://github.com/doublereedkurt/pyjks
[boltons_gh]: https://github.com/mahmoud/boltons
[requirements_txt]: https://github.com/paypal/support/blob/master/requirements.txt

[sampling_prof_wp]: https://en.wikipedia.org/wiki/Profiling_%28computer_programming%29#Statistical_profilers

## Enterprise Ideals, Flexible Features

In an enterprise environment, you are looking to achieve the
following:

  * Interoperability
  * Introspectability
  * Infallibility

Of course organizations of all sizes want these features as well, but
the key difference is that large organizations like PayPal usually end
up building a higher degree of redundancy and risk mitigation into
their processes. This often results in a great cost in terms of both
hardware and developer productivity. Fortunately, this is exactly
where Python excels and can save the day.

So let's take a stroll through a selection of SuPPort's feature set in
the context of these criteria!

### Interoperability

Python usage here at PayPal has spread to virtually every imaginable
use case: admin interfaces, midtier services, operations automation,
developer tools, batch jobs; you name it, Python has filled a gap in
that area. This has resulted in a few rather interesting abstractions
exposed in SuPPort.

#### BufferedSocket

PayPal has hundreds of services across several tiers. Interoperating
between these means having to implement over half a dozen network
protocols. The `BufferedSocket` type eliminated our inevitable code
duplication, handling a lot of the nitty-gritty of making a socket
into a parser-friendly data source, while retaining timeouts for
keeping communications responsive. A must-have primitive for any
gevent protocol implementer.

#### ConnectionManager

Errors happen in live environments. DNS requests fail. Packets are
lost. Latency spikes. TCP handshakes are slow. SSL handshakes are
slower. Clients rarely handle these problems gracefully. This is why
SuPPort includes the `ConnectionManager`, which provides robust error
handling code for all of these cases with consistent logging and
monitoring. It also provides a central point of configuration for
timeouts and host fallbacks.

### Introspectability

As part of a large organization, we can afford to add more machines,
and are even required to keep a certain level of redundancy and idle
hardware. And while DevOps is catching on in many larger-scale
environments, there are many cases in enterprise environments where a
developer is *not allowed* to attend to their production code.

SuPPort currently comes with all the same general-purpose
introspection capabilities that PayPal Python developers enjoy,
meaning that we get you as much structured information about your
application as possible without actually requiring login
privileges. Of course almost every aspect of this is configurable, to
suit a wide variety of environments from development to production.

#### Context management

Python famously has no global scope: all values are namespaced in
module scope. But there are still plenty of aspects of the runtime
that are global. Some are out of our control, like the OS-assigned
process ID, or the VM-managed garbage collection counters. Others
aspects are in our control, and best practice in concurrent
programming is to keep these as well-managed as possible.

SuPPort uses a system of contexts to explicitly manage nonlocal state,
eliminating difficult-to-track implicit global state for many core
functions. This has the added benefit of creating opportunities to
centrally manage and monitor debugging data and statistics, made
available through the MetaApplication below. (# TODO screenshot)

#### MetaApplication

While not strictly a web server framework, SuPPort leverages its
strong roots in the web to provide both a web-based user interface and
API full of useful runtime information.

(# TODO screenshot)

### Infallibility

At the end of the day, reliability over long periods of time is what
earns a stack approval and adoption. At this point, the SuPPort
architecture has a billion production requests under its belt here at
PayPal, but on the way we put it through the proverbial paces. We
confirmed at various points that the architecture:

* Gracefully sheds traffic under load (no unbounded queues here)
* Can and has run at 90%+ CPU load for days at a time
* Is free from infrastructural memory leaks
* Is robust to memory leakage

To illustrate, a live service handling millions of requests per
day had a version of openssl installed which was leaking memory
on every handshake.  Thanks to pre-emptive worker cycling on
excessive memory, no intervention was required.
The worker cycling was noted in the logs, the leak was traced
to openssl, ops was notified.  The problem was fixed with the
next regularly scheduled release rather than being handled as
a crisis.


#### Using threads with gevent

"Threads? In my gevent? I thought the whole point of greenlets and
gevent was to eliminate evil, evil threads," go the protests of
countless strawmen.

Originating in [Stackless][stackless] and ported over by [PyPy][pypy]'s own Armin Rigo,
greenlets are excellent and powerful, and we want to add that power to
the process- and thread-based world. There's no point running from
standard OS capabilities; threads still have their place. We decided
we didn't want *n* threads, we wanted *k* threads. Many architectures
adopt a thread-per-request or process-per-request model, but the last
thing we want is the number of threads going up as load
increases. Each thread adds a bit of contention to the mix, but
in many environments the memory overhead alone, typically 4-8MB per
thread. At just a few kilobytes apiece, greenthreads are three orders
of magnitude less costly.

[stackless]: http://www.stackless.com/
[pypy]: http://pypy.org/

Furthermore, thread usage in our architecture is hardly about
parallelism; we use worker processes for that. In the SuPPort world,
threads are about preemption. Cooperative greenthreads are much more
efficient, but sometimes you really do need guarantees about
responsiveness.

One excellent example of how threads provide this responsiveness is
the `ThreadQueueServer` detailed below. But first, there are two
built-in `Threadpools` with decorators worth highlighting, `io_bound` and
`cpu_bound`:

##### io_bound

Used to wrap opaque clients built without affordances for cooperative
concurrent IO. We use this to wrap `cx_Oracle` and other C-based
clients that are built for thread-based parallelization. Other major
use cases for `io_bound` is when getting input from standard input
(`stdin`) and files.

##### cpu_bound

Used to wrap expensive operations that would halt the event loop for
too long. We use it to wrap long-running cryptography and
serialization tasks, such as decrypting private SSL certificates or
loading huge blobs of XML and JSON. Because the majority of use cases'
implementations do not release [the GIL][TODO], the `cpu_bound`
ThreadPool is actually just a pool of one thread, to minimize CPU
contention from multiple unparallelizable CPU-intensive tasks.

It's worth noting that some deserialization tasks are not worth the overhead
of dispatching to a separate thread, if for instance, the data is
shorter than 256 bytes. For these cases, we have the `cpu_bound_if`
decorator, which conditionally dispatches to the thread, yielding
slightly higher responsiveness for low-complexity requests.

Also note that both of these decorators are reentrant, making dispatch
idempotent. If you decorate a function that itself eventually calls a
decorated function, performance won't pay the thread dispatch tax
twice.

##### ThreadedQueueServer

The ThreadedQueueServer exists as an enhanced approach to pulling new
connections off of a server's listening socket. It's SuPPort's way of
incorporating an industry-standard practice, commonly associated with
nginx and Apache, into the gevent WSGI server.

If you've read this far into the post, you're probably familiar with
the standard multi-worker preforking server architecture; a parent
process opens a listening socket, forks one or more children that
inherit the socket, and the kernel manages which worker gets which
incoming client connection.

The problem with this approach is that it generally results in
inefficient distribution of connections, and can lead to some workers
being overloaded while others have cycles to spare. Plus, all worker
processes are woken up by the kernel in a race to accept a single
inbound connection. The solution implemented here uses a thread that
sleeps on accept, removing them from the kernel's listen queue as soon
as possible, then pushing accepted connections to the main event
loop. The ability to inspect this user-space connection queue enables
not only even distribution but also intelligent behavior under high
load, such as closing incoming connections when the backlog gets too
long. This fail-fast approach prevents the kernel from holding open
fully-established connections that cannot be reached in a reasonable
amount of time. This backpressure takes the wait out of client failure
scenarios leading to a more responsive system overall.

## What's next for SuPPort

The features highlighted above are just a small selection of the
features already in SuPPort, and there are many more to cover in
future posts. In addition to those, we will also be distilling more
code from our internal codebase out into the open. Among these we are
particularly excited about:

* Enhanced human-readable structured logging
* Advanced network security functionality based on OpenSSL
* Distributed online statistics collection
* Additional generalizations for TCP client infrastructure

## Cellar

Python Infrastructure is handling tens of millions of
production-critical hits per day, on track to handle hundreds by the
end of the year.

Python Infrastructure has excelled in these areas for the past 5 years
and SuPPort aims to carry on that legacy as the open-source common
core for both eBay and PayPal Python stacks.

SuPPort is a living, codified set of concurrent software learnings.


## TODO

* Links
  * link to worker/thread-per-request architecture
  * link to lengthier preforked architecture description
