# Introducing SuPPort

In our last post, [Ten Myths of Enterprise Python][ten_myths], we promised a deeper
dive into our Python Infrastructure works here at PayPal and
eBay. Thing is, there are only so many details we can cover, and at
the end of the day, it's so much better to show than to say.

So without further ado, I'm pleased to introduce
[SuPPort][support_github], an in-development distillation of our
PayPal Python Infrastructure. Started in 2010, Python Infrastructure
initially powered PayPal's internal price-setting interfaces, then
payment orchestration interfaces, and now in 2015 supports dozens of
projects at PayPal and eBay, having handled billions of
production-critical requests and much more.

[ten_myths]: https://www.paypal-engineering.com/2014/12/10/10-myths-of-enterprise-python/
[support_github]: https://github.com/paypal/support

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

So let's take a stroll through a selection of SuPPort's featureset in
the context of these criteria!

### Interoperability

Python usage here at PayPal has spread to virtually every imaginable
use case: admin interfaces, midtier services, operations automation,
developer tools, batch jobs; you name it, Python has filled a gap in
that area. This has resulted in a few rather interesting abstractions
exposed in SuPPort.

#### BufferedSocket

Having had to implement over half a dozen network protocols for the
PayPal environment can lead to a lot of duplicated code. The
BufferedSocket class handles a lot of the nitty-gritty of making a
socket into a parser-friendly data source, while retaining timeouts
for keeping communications responsive. A must-have primitive for any
gevent protocol implementer.

#### ConnectionManager

Errors happen in live environments. DNS requests fail. Packets are
lost. Latency spikes. TCP handshakes are slow. SSL hanshakes are
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

At the end of the day, reliability over long periods of time are what
actually earns a stack adoption and approval. At this point, the
SuPPort architecture has a billion production requests under its belt
here at PayPal, but on the way we put it through the proverbial
paces. We confirmed at various points that the architecture:

* Gracefully sheds traffic under load (no unbounded queues here)
* Can and has run at 90%+ CPU load for days at a time
* Is free from infrastructural memory leaks
* Is robust to memory leakage within code written on top of the library

In fact, to illustrate that last point, there was a particular case
when a user unexpectedly ended up out of the office with a major
memory leak in their component. Despite being gone for weeks, thanks
to worker cycling and general robustness, there was no major impact to
their product.

#### Using threads with gevent

"Threads? In my gevent? I thought the whole point of greenlets and
gevent was to eliminate evil, evil threads," go the protests of
countless strawmen.

Greenlets are excellent and powerful, and we want to add that power to
our process and thread-based world. There's no point running from
standard OS capabilities; threads still have their place. We decided
we didn't want *n* threads, we wanted *k* threads. Many architectures
adopt a thread-per-request or process-per-request model, but the last
thing we want is the number of threads going up as load
increases. Each thread brings adds a bit of contention to the mix, but
in many environments the memory overhead alone, 4MB for 32-bit and 8MB
for 64-bit, is significant. At just a few kilobytes apiece,
greenthreads are three orders of magnitude less costly.

Furthermore, thread usage in our architecture is hardly about
parallelism; we use worker processes for that. In the SuPPort world,
threads are about preemption. Cooperative greenthreads are much more
efficient, but sometimes you really do need guarantees about
responsiveness.

One excellent example of how threads provide this responsiveness is
the `ThreadQueueServer` detailed below. But first, there are two
built-in Threadpools with decorators worth highlighting, `io_bound` and
`cpu_bound`:

##### io_bound

Used to wrap opaque clients built without affordances for cooperative
concurrent IO. We use this to wrap `cx_Oracle` and other C-based
clients that are built for thread-based parallelization.

##### cpu_bound

Used to wrap expensive operations that would halt the event loop for
too long. We use it to wrap long-running cryptiography and
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
