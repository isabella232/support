# Python Analytics

This server-client system exists to track usage patterns of Python
applications, primarily within an enterprise development
environment. Within a large organization there are all different
manners of setups, software versions, and other complexity, so it
helps to track these factors to know what users require what sort of
support. At PayPal, we have used it to stay ahead of the curve of
changing architectures and updating compilers, as well as getting real
data on the phase out of old versions of Python.

It can also be invaluable when contacted by users who report issues,
because it's rare that they include all of their environment and
execution details. Much of that information doesn't last much longer
than their shell session, so it's best to collect it eagerly whenever
possible.

## Server

For the sake of simplicity, the server appends one JSON-serialized
object per request, each on a separate line, in what's commonly known
as the JSON Lines or JSONL format. For this reason, unless a more
advanced database backend is integrated, it is recommended that the
server be configured to run with only one worker.

Also for the sake of simplicity, it offers basic querying
functionality, despite being primarily a write-only service. This
querying system has very basic counting and group-by functionality,
all offered through the URL query string.

## Client

In congruence with the server, the client is simple and HTTP-based. It
consists of two parts: `collect.py` collects the relevant analytics
data and `sockclusion.py` takes care of the HTTP and socket
layers. Neither has any dependencies beyond the Python 2.6 standard
library. It does not depend on SuPPort. The client has been tested
across Linux, Windows, Mac, Solaris, and Cygwin.

Note that `socklusion` gives the client the added feature of spawning
a separate process to asynchronously submit the analytics data in the
most unobtrusive way possible, virtually guaranteed to leave the main
process unaffected.
