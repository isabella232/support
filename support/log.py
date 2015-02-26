
import sys
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


class LoggingContext(object):
    def __init__(self, level=None, enable_stderr=True):
        self.level = level
        self.enable_stderr = enable_stderr
        self.loggers = {}
        self.module_loggers = {}
        self.default_logger = support_log

    def get_module_logger(self):
        module_name = sys._getframe(1).f_globals.get('__name__', '<module>')
        log = Logger(name=module_name, module=module_name)
        if self.enable_stderr:
            log.add_sink(stderr_sink)
        self.module_loggers[module_name] = log
        return log

    def get_logger(self, name):
        try:
            ret = self.module_loggers[name]
        except KeyError:
            ret = self.module_loggers[name] = SupportLogger(name)
        return ret

    def debug(self, *a, **kw):
        return self.default_logger.debug(*a, **kw)

    def info(self, *a, **kw):
        return self.default_logger.info(*a, **kw)

    def critical(self, *a, **kw):
        return self.default_logger.critical(*a, **kw)
