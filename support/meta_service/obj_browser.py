import types
import gc
import sys


def render_html(obj, id2url):
    '''
    Render an HTML page displaying information about the object,
    where id2url is a callback to construct a path to another object
    from a URL.
    '''
    def tolink(obj):
        if not gc.is_tracked(obj):
            return format('{0}', tolabel(obj))
        return format('<a href="{0}">{1}</a>', id2url(id(obj)), tolabel(obj))

    header = format('<h1>{0}(@{1})</h1>', tolabel(obj), id(obj))

    from_col = '<table><tr><th>from</th><th>as</th></tr>'
    for key, ref in get_referrer_key_obj_list(obj):
        from_col += '<tr><td>{0}</td>'.format(tolink(ref))
        from_col += format('<td>{0}</td></tr>', key)
    from_col += '</table>'

    info_col = '<table>'
    info_col += '<tr><td>type</td><td>{0}</td></tr>'.format(tolink(type(obj)))
    info_col += format('<tr><td>refcount</td><td>{0}</td></tr>',
                       sys.getrefcount(obj))
    info_col += format('<tr><td>dir</td><td>{0}</td></tr>', repr(dir(obj)))
    info_col += '</table>'

    to_col = '<table><th>as</th><th>to</th></tr>'
    for key, ref in get_referree_key_obj_list(obj):
        to_col += '<tr><td>{0}</td>'.format(key)
        to_col += '<td>{0}</td></tr>'.format(tolink(ref))
    to_col += '</table>'

    return ('<!doctype html><html><head><link rel="stylesheet" type="text/css"'
            ' href="/meta/clastic_assets/common.css"></head><body>{0}<table>'
            '<tr><td valign="top">{1}</td>'
            '<td valign="top">{2}</td><td valign="top">{3}</td></tr></table>'
            '</body></html>').format(header, from_col, info_col, to_col)


def get_referrer_key_obj_list(obj):
    '''
    Return a list [ (key, ref), (key, ref), ...]
    Where key is a string representing how the object
    ref references the passed obj.
    '''
    gc.collect()
    refs = gc.get_referrers(obj)
    key_obj_list = []
    for e in refs:
        key = None
        if isinstance(e, dict):
            key = "[" + repr(keyof(e, obj)) + "]"
        elif isinstance(e, (list, tuple)):
            try:
                key = "[" + repr(e.index(obj)) + "]"
            except ValueError:
                pass
        elif isinstance(e, types.FrameType):
            key = keyof(e.f_locals, obj) or keyof(e.f_globals, obj)
        elif isinstance(e, types.MethodType):
            key = keyof({
                "im_class": e.im_class,
                "im_func": e.im_func,
                "im_self": e.im_self
            }, obj)
        elif hasattr(e, '__dict__'):
            key = keyof(e.__dict__, obj)
        # if all else has failed...
        if type(e) is obj:
            key = '__class__'
        key_obj_list.append((key, e))
    return key_obj_list

 
def keyof(map, obj):
    'find key that obj is stored at in map by exhaustive search'
    for k in map:
        if map[k] is obj:
            return k
    return None


def get_referree_key_obj_list(obj):
    '''
    Return a list [ (key, ref), (key, ref), ...]
    Where key is a string representing how the passed
    obj references the object ref.
    '''
    key_obj_map = {}
    # dict-like things
    try:
        for k in obj.keys():
            key_obj_map["[" + tolabel(k) + "]"] = obj[k]
    except:
        pass
    # list-like things
    try:
        for i in range(len(obj)):
            key_obj_map["[" + tolabel(i) + "]"] = obj[k]
    except:
        pass
    # object-like things
    try:
        key_obj_map.update(obj.__dict__)
    except:
        pass
    return sorted(key_obj_map.items())


def tolabel(obj):
    if not isinstance(obj, basestring):
        obj = repr(obj)
    return obj.encode('utf-8', 'replace')[:64]


def format(html, *args, **kwargs):
    import cgi

    def escape(e):
        if isinstance(e, basestring):
            return cgi.escape(e)
        return e

    args = [escape(e) for e in args]
    kwargs = dict([(k, escape(v)) for k, v in kwargs.items()])
    return html.format(*args, **kwargs)
