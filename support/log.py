
from lithoxyl import BaseLogger
from lithoxyl.sinks import SensibleSink, Formatter, StreamEmitter

class SupportLogger(BaseLogger):
    pass


url_log = SupportLogger('url')
worker_log = SupportLogger('worker')


# TODO: create/attach this in context
stderr_fmt = Formatter('{begin_timestamp} - {record_status}')
stderr_emt = StreamEmitter('stderr')
stderr_sink = SensibleSink(formatter=stderr_fmt,
                           emitter=stderr_emt)

url_log.add_sink(stderr_sink)
worker_log.add_sink(stderr_sink)

url_log.critical('GET /').success()
