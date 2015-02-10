
from lithoxyl import Logger
from lithoxyl.sinks import SensibleSink, Formatter, StreamEmitter
from lithoxyl.fields import FormatField

import gevent

def get_current_gthreadid(record):
    return id(gevent.getcurrent())


class SupportLogger(Logger):
    pass


url_log = SupportLogger('url')
worker_log = SupportLogger('worker')
support_log = SupportLogger('support')


extra_fields = [FormatField('current_gthread_id' , 'd', get_current_gthreadid, quote=False)]


# TODO: create/attach this in context

stderr_fmt = Formatter('{end_local_iso8601_notz} {module_path} ({current_gthread_id}) - {message}',
                       extra_fields=extra_fields)
stderr_emt = StreamEmitter('stderr')
stderr_sink = SensibleSink(formatter=stderr_fmt,
                           emitter=stderr_emt)

url_log.add_sink(stderr_sink)
worker_log.add_sink(stderr_sink)
support_log.add_sink(stderr_sink)

#url_log.critical('serve_http_request').success('{method} {url}', method='GET', url='/')
