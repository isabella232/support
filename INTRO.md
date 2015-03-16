# Introducing SuPPort

In our last post, [Ten Myths of Enterprise Python][ten_myths], we
promised a deeper dive into how our Python Infrastructure works here
at PayPal and eBay. Thing is, there are only so many details we can
cover, and at the end of the day, it's so much better to show than to
say.

So without further ado, I'm pleased to introduce
[**SuPPort**][support_gh], an in-development distillation of our
PayPal Python Infrastructure.

[ten_myths]: https://www.paypal-engineering.com/2014/12/10/10-myths-of-enterprise-python/
[support_gh]: https://github.com/paypal/support

Started in 2010, Python Infrastructure initially powered PayPal's
internal price-setting interfaces, then payment orchestration
interfaces, and now in 2015 supports dozens of projects at PayPal and
eBay, having handled billions of production-critical requests and much
more. So what does it mean to distill this functionality into SuPPort?

<a name="#open-source"></a>

SuPPort is an [event-driven][evented] server framework designed for
building scalable and maintainable services and clients, built with
several open-source technologies:

  * [gevent][gevent_gh] - Performant networking with coroutines ([tutorial][gevent_tut])
  * [greenlet][greenlet_gh] - Python's premiere microthreading library
  * [clastic][clastic_gh] - Lightweight web framework free of global state
  * [werkzeug][werkzeug_gh] - Python's most popular WSGI toolkit
  * [faststat][faststat_gh] - Fast [streaming][streaming_wp] statistics collection
  * [hyperloglog][hyperloglog_gh] - Efficient cardinality estimators ([paper][hll_paper])
  * [ufork][ufork_gh] - Minimal, programmatic preforking servers
  * [lithoxyl][lithoxyl_gh] - Fast, transactional structured system instrumentation
  * [sampro][sampro_gh] - Super simple [sampling profiler][sampling_prof_wp]
  * [PyOpenSSL][pyopenssl_gh], [pyasn1][pyasn1_sf], [pyjks][pyjks_gh],
    [boltons][boltons_gh], and [more...][requirements_txt]

With power comes complexity, and while Python as a language strives
for technical convergence, there are many ways to approach the problem
of developing, scaling, and maintaining components. SuPPort is one way
gevent and the libraries above have been used to build functional services
and products with anywhere from 100 requests per day to 100 requests
per second and beyond. Most of all, SuPPort aims to be an educational
design entry for developers looking for practical and scalable
client-server architectures.

[evented]: https://en.wikipedia.org/wiki/Event-driven_architecture
[gevent_gh]: https://github.com/gevent/gevent
[gevent_tut]: http://sdiehl.github.io/gevent-tutorial/
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
[streaming_wp]: https://en.wikipedia.org/wiki/Streaming_algorithm
[hll_paper]: http://algo.inria.fr/flajolet/Publications/FlFuGaMe07.pdf

<a name="#enterprise"></a>
## Enterprise Ideals, Flexible Features

Many motivations went into building up a Python stack at PayPal, but
as in any enterprise environment, we were looking to achieve the
following:

  * [**Interoperability**](#interoperability)
  * [**Introspectability**](#introspectability)
  * [**Infallibility**](#infallibility)

Of course organizations of all sizes want these features as well, but
the key difference is that large organizations like PayPal usually end
up building a higher degree of redundancy and risk mitigation into
their processes. This often results in a great cost in terms of both
hardware and developer productivity. Fortunately, this is exactly
where Python excels and can save the day.

So, let's take a stroll through a selection of SuPPort's feature set
in the context of these criteria! Note that if you're unfamiliar with
evented programming, nonblocking sockets, and in a couple cases gevent
in particular, some of this may seem quite foreign. [The gevent
tutorial][gevent_tut] is a good entry point for the intermediate Python programmer.

### <a href="#interoperability" name="interoperability">Interoperability</a>

Python usage here at PayPal has spread to virtually every imaginable
use case: admin interfaces, midtier services, operations automation,
developer tools, batch jobs; you name it, Python has filled a gap in
that area. This has resulted in a few rather interesting abstractions
exposed in SuPPort.

#### <a href="#buffered-socket" name="buffered-socket">`BufferedSocket`</a>

PayPal has hundreds of services across several tiers. Interoperating
between these means having to implement over half a dozen network
protocols. The `BufferedSocket` type eliminated our inevitable code
duplication, handling a lot of the nitty-gritty of making a socket
into a parser-friendly data source, while retaining timeouts for
keeping communications responsive. A must-have primitive for any
gevent protocol implementer.

#### <a href="#connection-manager" name="connection-manager">`ConnectionManager`</a>

Errors happen in live environments. DNS requests fail. Packets are
lost. Latency spikes. TCP handshakes are slow. SSL handshakes are
slower. Clients rarely handle these problems gracefully. This is why
SuPPort includes the `ConnectionManager`, which provides robust error
handling code for all of these cases with consistent logging and
monitoring. It also provides a central point of configuration for
timeouts and host fallbacks.

### <a href="#introspectability" name="introspectability">Introspectability</a>

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

#### <a href="#context-management" name="context-management">`Context` management</a>

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
available through the MetaApplication below.

(Figure 1: see the examples of charts in the the assets directory)

#### <a href="#metaapplication" name="metaapplication">`MetaApplication`</a>

While not strictly a web server framework, SuPPort leverages its
strong roots in the web to provide both a web-based user interface and
API full of useful runtime information.

(Figure 2: see the examples of the MetaApplication in the assets directory)

### <a href="#infallibility" name="infallibility">Infallibility</a>

At the end of the day, reliability over long periods of time is what
earns a stack approval and adoption. At this point, the SuPPort
architecture has a billion production requests under its belt here at
PayPal, but on the way we put it through the proverbial paces. At
various points, we have tested and confirmed these edge behaviors:

  * **Gracefully sheds traffic** under load (no unbounded queues here)
  * Can and has run at **90%+ CPU load for days** at a time
  * Is free from **framework** memory leaks
  * Is robust to memory leakage in **user code**

To illustrate, a live service handling millions of requests per day
had a version of OpenSSL installed which was leaking memory on every
handshake. Thanks to preemptive worker cycling on excessive process
memory usage, no intervention was required and no customers were
impacted. The worker cycling was noted in the logs, the leak was
traced to OpenSSL, and operations was notified. The problem was fixed
with the next regularly scheduled release rather than being handled as
a crisis.

#### <a href="#no-monkeypatching" name="no-monkeypatching">No monkeypatching</a>

One of the first and sometimes only ways that people experience gevent
is through [monkeypatching][monkeypatch_wp]. At the top of your main
module [you issue a call to gevent][gevent_monkeypatch] that
automatically swaps out virtually all system libraries with their
cooperatively concurrent ones. This sort of magic is relatively rare
in Python programming, and rightfully so. Implicit activities like
this can have unexpected consequences. SuPPort is a no-monkeypatching
approach to gevent. If you want to implement your own network-level
code, it is best to use `gevent.socket` directly. If you want
gevent-incompatible libraries to work with gevent, best to use
SuPPort's gevent-based threadpooling capabilities.

[gevent_monkeypatch]: http://www.gevent.org/intro.html#monkey-patching
[monkeypatch_wp]: https://en.wikipedia.org/wiki/Monkey_patch

#### <a href="#gevent-threads" name="gevent-threads">Using threads with gevent</a>

<blockquote>"Threads? In my gevent? I thought the whole point of
greenlets and gevent was to eliminate evil, evil threads!"
<cite> --Countless strawmen</cite></blockquote>

Originating in [Stackless][stackless] and ported over in 2004 by
[Armin Rigo][armin_rigo] (of [PyPy][pypy] fame),
[greenlets][greenlets] are mature and powerful concurrency
primitives. We wanted to add that power to the process- and
thread-based world of POSIX. There's no point running from standard OS
capabilities; threads have their place. Many architectures adopt a
[thread-per-request or process-per-request model][server_models], but
the last thing we want is the number of threads going up as load
increases. Threads are expensive; each thread adds a bit of contention
to the mix, but in many environments the memory overhead alone,
typically 4-8MB per thread, presents a problem. At just a few
kilobytes apiece, greenlet's microthreads are three orders of
magnitude less costly.

[stackless]: http://www.stackless.com/
[pypy]: http://pypy.org/
[armin_rigo]: http://pyvideo.org/speaker/334/armin-rigo
[greenlets]: https://greenlet.readthedocs.org/en/latest/
[server_models]: https://www.usenix.org/legacy/publications/library/proceedings/osdi99/full_papers/banga/banga_html/node3.html

Furthermore, thread usage in our architecture is hardly about
parallelism; we use worker processes for that. In the SuPPort world,
threads are about preemption. Cooperative greenlets are much more
efficient overall, but sometimes you really do need guarantees about
responsiveness.

One excellent example of how threads provide this responsiveness is
the [`ThreadQueueServer`](#thread-queue-server) detailed below. But
first, there are two built-in `Threadpools` with decorators worth
highlighting, `io_bound` and `cpu_bound`:

##### <a href="#io-bound" name="io-bound">`io_bound`</a>

Used to wrap opaque clients built without affordances for cooperative
concurrent IO. We use this to wrap [`cx_Oracle`][cx_oracle] and other C-based
clients that are built for thread-based parallelization. Other major
use cases for `io_bound` is when getting input from standard input
(`stdin`) and files.

[cx_oracle]: https://pypi.python.org/pypi/cx_Oracle

##### <a href="#cpu-bound" name="cpu-bound">`cpu_bound`</a>

Used to wrap expensive operations that would halt the event loop for
too long. We use it to wrap long-running cryptography and
serialization tasks, such as decrypting private SSL certificates or
loading huge blobs of XML and JSON. Because the majority of use cases'
implementations do not release the Global Interpreter Lock, the
`cpu_bound` ThreadPool is actually just a pool of one thread, to
minimize CPU contention from multiple unparallelizable CPU-intensive
tasks.

It's worth noting that some deserialization tasks are not worth the
overhead of dispatching to a separate thread. If the data to be
deserialized is very short or a result is already cached.  For these
cases, we have the `cpu_bound_if` decorator, which conditionally
dispatches to the thread, yielding slightly higher responsiveness for
low-complexity requests.

Also note that both of these decorators are reentrant, making dispatch
idempotent. If you decorate a function that itself eventually calls a
decorated function, performance won't pay the thread dispatch tax
twice.

##### <a href="#thread-queue-server" name="thread-queue-server">`ThreadQueueServer`</a>

The `ThreadQueueServer` exists as an enhanced approach to pulling
new connections off of a server's listening socket. It's SuPPort's way
of incorporating an industry-standard practice, commonly associated
with nginx and Apache, into the gevent WSGI server world.

If you've read this far into the post, you're probably familiar with
[the standard multi-worker preforking server architecture][stevens_models];
a parent process opens a listening socket, forks one or more children
that inherit the socket, and the kernel manages which worker gets
which incoming client connection.

[stevens_models]: http://linuxgazette.net/129/saha.html

The problem with this approach is that it generally results in
inefficient distribution of connections, and can lead to some workers
being overloaded while others have cycles to spare. Plus, all worker
processes are woken up by the kernel in a race to accept a single
inbound connection, in what's commonly referred to as
[*the thundering herd*][thunder_herd].

[thunder_herd]: https://en.wikipedia.org/wiki/Thundering_herd_problem

The solution implemented here uses a thread that sleeps on accept,
removing connections from the kernel's listen queue as soon as
possible, then explicitly pushing accepted connections to the main
event loop. The ability to inspect this user-space connection queue
enables not only even distribution but also intelligent behavior under
high load, such as closing incoming connections when the backlog gets
too long. This fail-fast approach prevents the kernel from holding
open fully-established connections that cannot be reached in a
reasonable amount of time. This backpressure takes the wait out of
client failure scenarios leading to a more responsive extrinsic
system, as well.

## <a href="#whats-next" name="whats-next">What's next for SuPPort</a>

The sections above highlight just a small selection of the features
already in SuPPort, and there are many more to cover in future
posts. In addition to those, we will also be distilling more code from
our internal codebase out into the open. Among these we are
particularly excited about:

  * Enhanced human-readable structured logging
  * Advanced network security functionality based on OpenSSL
  * Distributed online statistics collection
  * Additional generalizations for TCP client infrastructure

And of course, more tests! As soon as we get a couple more features
distilled out, we'll start porting out more than the skeleton tests we
have now. Suffice to say, we're really looking forward to expanding
our set of codified concurrent software learnings, and incorporating
as much community feedback as possible, so don't forget to
[subscribe to the blog][ppe_rss] and
[the SuPPort repo on GitHub][support_gh].

[ppe_rss]: https://www.paypal-engineering.com/feed/

[Mahmoud Hashemi][mahmoud], [Kurt Rose][doublereedkurt], [Mark Williams][markrwilliams], and [Chris Lane][lanstin]

[mahmoud]: https://github.com/mahmoud
[doublereedkurt]: https://github.com/doublereedkurt
[markrwilliams]: https://github.com/markrwilliams
[lanstin]: https://github.com/lanstin
