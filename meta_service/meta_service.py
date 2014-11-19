import traceback
import sys
import datetime
import hashlib
import os
import os.path
from collections import defaultdict

import clastic
import clastic.render
from werkzeug.wrappers import Response  # for iterable json thingy

from .. import context
from .. import cal

import stats


def create_meta_app(additional_routes=None):
    render = clastic.render.render_basic
    ma = clastic.meta.MetaApplication()
    routes = [
        ('/', ma),
        ('/warnings', get_warnings, render),
        ('/warnings/<path>', get_warnings, render),
        ('/pytypes', get_pytypes, render),
        ('/py_lens', get_pytypes_len),
        ('/py_dump_id/<in_id>', dump_id, render),
        ('/context', get_context_dict, render),
        ('/threads', get_thread_stacks, render),
        ('/psutil', get_psutil_data, render),
        ('/debug_logs', get_logs, render),
        ('/wsgi_logs', get_web_logs, render),
        ('/debug_level/<level>', set_level, render),
        ('/greenlets', get_greenlets, render),
        ('/connections', get_connections, render),
        ('/reset_stats', reset_stats, render),
        ('/statvars', stats.get_stats, render),
        ('/statvars/<the_stat>', stats.get_stats, render),
        ('/cal/thread_ids', get_cal_threadid_info, render),
        ('/recent_cal', get_recent_cal, render),
        ('/recent_tcp', get_recent_tcp, render),
        ('/recent_tcp/<name>', get_recent_tcp, render),
        ('/recent/', get_recent, render),
        ('/recent/<thing1>', get_recent, render),
        ('/recent/<thing1>/<thing2>', get_recent, render),
        ('/samples', get_sampro_data, render),
        ('/topos', get_topos, render),
        ('/topos/<appname>', get_topos, render),
        ('/protected', get_protected, render),
        ('/environment', get_environment, render),
        ('/docs/<path*>', render_doc),
        ('/code/<path*>', render_code),
        ('/connection_mgr', get_connection_mgr, render),
        ('/fd_info', get_fd_info, rt_json_render_basic),
        ('/fd_info/<fd:int>', get_one_fd_info, rt_json_render_basic),
        ('/live_checks/', get_live_checks, render),
        ('/object', view_obj),
        ('/object/<obj_id:int>', view_obj),
    ] + (additional_routes or [])

    app = clastic.Application(routes)
    return app


def rt_json_render_basic(request, context, _route):
    import json
    rjd = clastic.render_json_dev
    new_context = json.loads(rjd.json_encoder.encode(context))
    return clastic.render_basic(request=request,
                                context=new_context,
                                _route=_route)


def get_config_dict():
    'returns information about the current environment in a dictionary'
    ctx = context.get_context()
    data = []
    keys_handled_directly = ['cal', 'protected', 'ssl_contexts']
    for k in ctx.__dict__:
        if not k in keys_handled_directly:
            data.append([k, getattr(ctx, k)])

    if isinstance(ctx.cal, cal.DefaultClient):
        data.append(['cal', 'local-print'])
    else:
        data.append(['cal', ctx.cal.pool, ctx.cal.host, ctx.cal.port])
    if ctx.protected:
        prot = ctx.protected
        data.append(['protected', prot.prot_dir, prot.prot_name,
                     prot.pin_name])
        ssl_contexts = dict([(n, bool(getattr(prot, n, None))) for n in
                            ("ssl_client_context", "ssl_server_context")])
        data.append(['ssl_contexts', ssl_contexts])
        data.append(['protected_pin_source', prot.pin.source])
    else:
        data.append(['protected', '(none loaded)'])

    return dict([(e[0], e[1:]) for e in data])


def get_environment():
    return dict((k, os.environ[k]) for k in os.environ if "AUTHC" not in k)


def get_context_dict():
    data = get_config_dict()
    return data


def get_thread_stacks():
    return dict([(k, traceback.format_stack(v)) for k, v in sys._current_frames().items()])


def get_topos(appname=None):
    ctx = context.get_context()
    if ctx.topos is None:
        return "WARNING: no topos loaded"
    if appname is None:
        appname = ctx.appname
    if appname not in ctx.topos.apps:
        return "appname {0} not found in topos.".format(ctx.appname)
    return ctx.topos.apps[appname].addresses


def get_protected():
    ctx = context.get_context()
    if ctx.protected is None:
        return "(none)"
    prot = ctx.protected
    data = {}
    for k, v in prot.__dict__.items():
        if isinstance(v, basestring) and k not in ("pin", "passphrase"):
            data[k] = v
    data["sha1(passphrase)"] = hashlib.sha1(prot.passphrase).hexdigest()
    data["sha1(pin)"] = hashlib.sha1(prot.pin).hexdigest()
    # TODO: add cert chains and private key names
    return data


def get_connections():
    ctx = context.get_context()
    ret = {}
    for model in ctx.connection_mgr.server_models.values():
        ret[model.address] = {
            "last_error": datetime.datetime.fromtimestamp(
                int(model.last_error)).strftime('%Y-%m-%d %H:%M:%S'),
            "sockets": [repr(s) for s in model.active_connections.values()]
        }
    return ret


def get_cal_threadid_info():
    ctx = context.get_context()
    if not hasattr(ctx.cal, "aliaser"):
        return ("Error: context.cal has no aliaser attribute; "
                "(context.cal is of type " + repr(type(ctx.cal)) + ")")
    return dict(ctx.cal.aliaser.mapping)


def get_pytypes():
    answer = defaultdict(lambda: 0)
    try:
        import gc
        for ob in gc.get_objects():
            answer[repr(type(ob))] += 1
    except:
        pass
    return answer


def get_pytypes_len():
    return Response(get_pytypes_len_gen(), mimetype="application/json")


def get_pytypes_len_gen():
    try:
        import gc
        yield "{\n"

        def get_total_size(obj, n=0):
            if n > 10:
                return 0
            obj_len = 0
            try:
                if callable(getattr(obj, '__len__', False)):
                    obj_len += int(obj.__len__())
            except:
                pass
            for a in gc.get_referents(obj):
                obj_len += get_total_size(a, n + 1)
            return obj_len

        for ob in gc.get_objects():
            if getattr(ob, '__len__', False):
                yield "\"" + repr(type(ob)) + ":" + str(id(ob)) + "\": [" + str(len(ob)) \
                      + ", " + str(get_total_size(ob)) + "]\n"

    except Exception as e:
        yield "\"error\":\"" + repr(e).replace("\"", "\\\"") + "\"\n"
    yield "}\n"


def dump_id(in_id):
    answer = {}
    try:
        import gc

        def get_total_size(obj, n=0):
            if n > 10:
                return 0
            obj_len = 0
            obj_len += getattr(ob, '__len__', 0)
            for a in gc.get_referents(obj):
                obj_len += get_total_size(a, n + 1)
            return obj_len

        for ob in gc.get_objects():
            if id(ob) == int(in_id):
                answer['ob'] = ob
                answer['repr'] = repr(ob)
                answer['dir'] = dir(ob)
                answer['total_lens'] = get_total_size(ob)
    except Exception as e:
        answer['error'] = repr(e)
    return answer


def get_greenlets():
    try:
        import gc
        import traceback
        from greenlet import greenlet
        answer = []
        for ob in gc.get_objects():
            if not isinstance(ob, greenlet):
                continue
            if not ob:
                continue
            answer.append(traceback.format_stack(ob.gr_frame))
    except Exception:
        from gevent.hub import _get_hub
        answer.append([_get_hub().loop.activecnt])
    return answer


def get_psutil_data():
    answer = {}
    try:
        import psutil
        import os
        answer = psutil.Process(os.getpid()).as_dict()
    except Exception as e:
        answer = {'error': repr(e)}
    return answer


def get_one_fd_info(fd):
    import traceback

    all_fd_info = get_fd_info()
    fd_info = all_fd_info[fd]
    fd_info['frames'] = []
    for obj in fd_info['gc_objs']:
        frames = get_frames_local_to(obj)
        for name, frame in frames:
            fd_info['frames'].append(
                (obj, name, traceback.format_stack(frame)))
    return fd_info


def get_fd_info():
    '''
    Gathers and correlates fd info from 3 sources:
    1- gc over all objects that have a fileno(); this is most 'socket-like' things
    2- infra Context object data structures
    3- psutil information
    4- /proc filesystem (if available)

    This function is a little bit open-ended, we can probably continue
    to find additional sources of information
    '''
    import gc
    import os.path
    import os
    import collections

    import psutil

    fd_info = collections.defaultdict(
        lambda: {'gc_objs': [], '/proc': {}, 'context': [], 'psutil': {}})

    # 1 - all objects with filenos
    for obj in gc.get_objects():
        try:
            fd = None
            if hasattr(obj, 'fileno') and callable(obj.fileno):
                fd = obj.fileno()
        except:  # there are a zillion boring reasons this may fail
            pass
        if isinstance(fd, (int, long)):
            fd_info[fd]['gc_objs'].append(obj)

    # 2 - inspection of data-structures in context
    ctx = context.get_context()
    socks = []
    for model in ctx.connection_mgr.server_models.values():
        socks.extend(model.active_connections)
    socks.extend(ctx.client_sockets)
    if ctx.server_group:
        socks.extend(ctx.server_group.socks.values())
    for sock in socks:
        fd_info[sock.fileno()]['context'].append(sock)

    # 3 - psutil information
    process = psutil.Process()
    for f in process.get_open_files():
        if f.fd == -1:
            continue
        fd_info[f.fd]['psutil']['path'] = f.path
    for conn in process.get_connections(kind='all'):
        if conn.fd == -1:
            continue
        fd_info[conn.fd]['psutil'].update(vars(conn))
        del fd_info[conn.fd]['psutil']['fd']

    # 4 - /proc filesystem
    proc_path = "/proc/" + str(os.getpid())
    if not os.path.exists(proc_path):
        return dict(fd_info)

    for fd in os.listdir(proc_path + "/fd"):
        try:
            inode = os.readlink(proc_path + "/fd/" + fd)
        except OSError:
            continue
        else:
            fd = int(fd)
            fd_info[fd]['/proc']['inode'] = inode

    return dict(fd_info)


def get_frames_local_to(obj):
    '''
    Find all of the frames which the object is referenced in.
    Return a list [(name, frame), (name, frame) ... ]
    '''
    import gc
    import types
    refs = gc.get_referrers(obj)
    frame_refs = []
    for f in refs:
        if not isinstance(f, types.FrameType):
            continue
        if f.f_code.co_filename == __file__:
            continue  # skip incestuous self-references
        for k, v in f.f_locals.items():
            if v is obj:
                frame_refs.append((k, f))
    return frame_refs


def get_connection_mgr():
    connection_mgr = context.get_context().connection_mgr
    server_models, sockpools = {}, {}
    for k in connection_mgr.server_models:
        server_model = connection_mgr.server_models[k]
        server_models[repr(k)] = {
            "info": repr(server_model),
            "fds": [s.fileno() for s in server_model.active_connections.keys()]
        }
    for prot in connection_mgr.sockpools:
        for sock_type in connection_mgr.sockpools[prot]:
            sockpool = connection_mgr.sockpools[prot][sock_type]
            sockpools[repr((prot, sock_type))] = {
                "info": repr(sockpool),
                "addresses": map(repr, sockpool.free_socks_by_addr.keys())
            }
    return {'server_models': server_models, 'sockpools': sockpools}


def get_logs():
    import ll
    return ll.log_msgs


def get_web_logs():
    sgrp = context.get_context().server_group
    if not sgrp:
        raise EnvironmentError("context.server_group unset (infra.serve() not called)")
    result = []
    for serv in sgrp.servers:
        if hasattr(serv, "log"):
            result.extend(serv.log.msgs)
    return result


def get_sampro_data():
    processed = defaultdict(int)
    profiler = context.get_context().profiler
    if profiler is None:
        return "(sampling disabled; enable with infra.get_context().set_sampling(True))"
    for k, v in context.get_context().profiler.live_data_copy().iteritems():
        code, lineno, parentcode = k
        if parentcode is None:
            # TODO: shorten filenames up to the .git directory
            key = (code.co_name + "(" + code.co_filename +
                   ", " + str(code.co_firstlineno) + ")")
            processed[key] += v
    processed = reversed(sorted([(v, k) for k, v in processed.items()]))
    return dict(processed)


def get_recent(thing1=None, thing2=None):
    from .. import cache

    if thing1 is None:
        return [k for k in context.get_context().recent.keys()]
    if thing2 is None:
        if isinstance(context.get_context().recent[thing1], (dict, cache.Cache)):
            return dict([(repr(k), list(v)) for k, v in context.get_context().recent[thing1].items()])
        else:
            return list(context.get_context().recent[thing1])
    else:
        if isinstance(context.get_context().recent[thing1], (dict, cache.Cache)):
            return dict([(repr(k), list(v)) for k, v in
                        context.get_context().recent[thing1].items() if thing2 in str(k)])
        else:
            return '{"error":"' + thing1 + ' recent data is not dict."}'


def get_recent_tcp(thing2=None):
    return get_recent(thing1='network', thing2=thing2)


def get_recent_cal():
    return get_recent(thing1='cal')


def get_warnings(path=None):
    from .. import context
    warns = context.get_context().get_warnings()
    if path:
        path_segs = path.split('.')
        for seg in path_segs:
            if seg in warns:
                warns = warns[seg]
            else:
                return "(none)"
    warns = _dict_map(warns, _transform)
    return warns


def _transform(v):
    try:
        return dict(v)
    except:
        pass
    try:
        return list(v)
    except:
        pass
    return repr(v)


def _dict_map(data, transform, recurse=lambda k, v: isinstance(v, dict)):
    transformed = {}
    stack = []
    stack.append((data, transformed))
    while stack:
        src, dst = stack.pop()
        for k, v in src.items():
            if recurse(k, v):
                child_val = {}
                stack.append((v, child_val))
            else:
                child_val = transform(v)
            dst[k] = child_val
    return transformed


def set_level(level):
    from .. import ll
    ll.set_log_level(int(level))
    return ll.get_log_level()


def reset_stats():
    import faststat
    from .. import context
    context.get_context().stats = defaultdict(faststat.Stats)
    return "OK"


def _normalize_path(path):
    if not isinstance(path, basestring):
        path = '/'.join(path)
    return path


def render_doc(path):
    path = _normalize_path(path)
    root = os.path.abspath(__file__)
    for i in range(3):
        root = os.path.dirname(root)
    pass


def _render_dir(path):
    elements = os.listdir(path)
    html = _RENDER_DIR_TEMPLATE.format('</td></tr><tr><td>'.join(elements))
    return clastic.Response(html, mimetype='text/html')


_RENDER_DIR_TEMPLATE = (
    '<html><head><title>{0}</title></head><body>'
    '<table>{1}</table></body></html>')


def render_code(path):
    path = _normalize_path(path)

    with open(path, 'rb') as f:
        code = f.read()

    try:
        from pygments import highlight
        from pygments.lexers import PythonLexer
        from pygments.formatters import HtmlFormatter
    except ImportError:
        return clastic.Response(code, mimetype="text/plain")

    formatter = HtmlFormatter()

    html = _RENDER_CODE_TEMPLATE.format(
        path, formatter.get_style_defs(), highlight(code, PythonLexer(), formatter))
    return clastic.Response(html, mimetype='text/html')


_RENDER_CODE_TEMPLATE = (
    '<html><head><title>{0}</title><style type="text/css">{1}</style>'
    '</head><body>{2}</body></html>')


def get_live_checks():
    checks = {}

    imported_modules = []
    import sys
    import os.path

    for mod in sys.modules.values():
        if not mod or not hasattr(mod, "__file__"):
            continue
        if 'site-packages' in os.path.abspath(mod.__file__):
            imported_modules.append(repr(mod))
    checks['imported_modules'] = imported_modules

    return checks


def view_obj(request, obj_id=None):
    import gc
    import obj_browser

    if obj_id is None:
        return clastic.redirect(
            request.path + '/{0}'.format(id(context.get_context())))

    for obj in gc.get_objects():
        if id(obj) == obj_id:
            break
    else:
        raise ValueError("no Python object with id {0}".format(obj_id))

    path, _, _ = request.path.rpartition('/')
    return clastic.Response(
        obj_browser.render_html(
            obj, lambda id: path + '/{0}'.format(id)),
        mimetype="text/html")

