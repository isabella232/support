# -*- coding: utf-8 -*-

import sys
import json
import time
import uuid
import socket
import getpass
import datetime
import platform
import socklusion

PPA_HOST = 'python.corp.ebay.com'
PPA_PORT = 443
PPA_URL_PATH = '/analytics/v1/on_import'
PPA_TIMEOUT = 5.0

INSTANCE_ID = uuid.uuid4()
IS_64BIT = sys.maxsize > 2 ** 32

HAVE_READLINE = True
try:
    import readline
except:
    HAVE_READLINE = False

HAVE_UCS4 = getattr(sys, 'maxunicode', 0) > 65536

# TODO: readable time
TIME_INFO = {'utc': str(datetime.datetime.utcnow()),
             'std_utc_offset': -time.timezone / 3600.0}


def get_python_info():
    ret = {}
    ret['argv'] = sys.argv
    ret['bin'] = sys.executable
    ret['is_64bit'] = IS_64BIT
    ret['version'] = sys.version
    ret['compiler'] = platform.python_compiler()
    ret['build_date'] = platform.python_build()[1]
    ret['version_info'] = list(sys.version_info)
    ret['have_ucs4'] = HAVE_UCS4
    ret['have_readline'] = HAVE_READLINE
    return ret


def get_all_info():
    ret = {}
    ret['username'] = getpass.getuser()
    ret['uuid'] = str(INSTANCE_ID)
    ret['hostname'] = socket.gethostname()
    ret['hostfqdn'] = socket.getfqdn()
    ret['uname'] = platform.uname()

    ret['python'] = get_python_info()
    ret['time'] = TIME_INFO
    try:
        import pkg_resources
        dist = pkg_resources.get_distribution('infra')
        ret['infra_wheel_version'] = dist.version
    except:
        pass

    return ret


def build_post_message(data):
    msg_lines = ['POST %s HTTP/1.0' % PPA_URL_PATH,
                 'Host: %s' % PPA_HOST,
                 'Content-Length: ' + str(len(data)),
                 '',
                 data]
    msg = '\r\n'.join(msg_lines)
    return msg


def send_import_analytics(data_dict=None, timeout=PPA_TIMEOUT):
    if data_dict is None:
        data_dict = get_all_info()
    msg = build_post_message(json.dumps(data_dict))
    socklusion.send_data(msg,
                         host=PPA_HOST,
                         port=PPA_PORT,
                         wrap_ssl=True,
                         timeout=timeout)


if __name__ == '__main__':
    send_import_analytics()
