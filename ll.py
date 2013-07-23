"""
Low-Level Logging

A module to allow a ton of data (e.g. all SSL unencrypted and encrypted IO) to
be logged but not actually slow the server down unless the thing is being traced
or the whole server is logging super verbose.
"""

import inspect
from collections import defaultdict
from datetime import datetime
import time

log_msgs = defaultdict(int)


LOG_LEVELS = {'NONE':   0,
              'DEBUG':  1,
              'DEBUG2': 2,
              'DEBUG3': 3
              }


_log_level = LOG_LEVELS['NONE']


class LLogger(object):
    """Instantiate this to get the module data about the caller"""
    
    def __init__(self):
        self.caller_mod = inspect.getmodule(inspect.stack()[1][0]).__file__.split(".")[-2].upper()
        self.ld = self.log_debug
        self.ld2 = self.log_debug2
        self.ld3 = self.log_debug3

    def log_debug(self, *args, **kw):
        log_msgs[args[0]] += 1
        if _log_level >= 1:
            msg = apply(args[0].format, tuple(args[1:]))
            print "%s %s D:" % (datetime.now().strftime("%d/%H:%M:%S.%f"),
                                self.caller_mod), msg
                              
    def log_debug2(self, *args, **kw):
        log_msgs[args[0]] += 1
        if _log_level >= 2:
            msg = apply(args[0].format, tuple(args[1:]))
            print "%s %s D2:" % (datetime.now().strftime("%d/%H:%M:%S.%f"),
                                 self.caller_mod), msg

    def log_debug3(self, *args, **kw):
        log_msgs[args[0]] += 1
        if _log_level >= 3:
            msg = apply(args[0].format, tuple(args[1:]))
            print "%s %s D3:" % (datetime.now().strftime("%d/%H:%M:%S.%f"),
                                 self.caller_mod), msg
                              
if __name__ == "__main__":
    ll = LLogger()
    ll.log_debug("{0}", "some stuff")
     
