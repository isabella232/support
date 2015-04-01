HTTP Client
===========

There are two levels of HTTP client in SuPPort: One which roughly maps
to Python's builtin :mod:`httplib` module, and another that maps to
:mod:`urllib2`.

Drop-in HTTP client: ``gurllib2``
--------------------------------------

This module has all the same members as the standard :mod:`urllib2`
library, except that a few have been overridden with alternatives that
use SuPPort for logging and connection management. It can be used as a
drop-in replacement in most applications that use urllib2. It is built
on :mod:`http_client`, detailed further down.

.. automodule:: support.gurllib2
   :members:
   :undoc-members:


Low-level HTTP client: ``http_client``
--------------------------------------

``http_client`` is a simple HTTP client that builds on :mod:`httplib`
and gevent, using SuPPort for connection management and logging.

.. automodule:: support.http_client
   :members:
   :undoc-members:
