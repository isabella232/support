import sys
import collections
import cgi
import os.path

import clastic

from .. import context


def listmodules():
    filename_leaf_samples, filename_branch_samples, total = _get_samples_by_file()

    rows = []
    for name, mod in sys.modules.items():
        href = '<a href="/meta/showmodule/{0}">{0}</a><br />'.format(cgi.escape(name))
        count = '?'
        cum_count = '?'
        if filename_leaf_samples and hasattr(mod, '__file__'):
            fname = mod.__file__
            if fname.endswith('.pyc'):
                fname = fname[:-1]
            count = str(filename_leaf_samples[fname])
            cum_count = str(filename_branch_samples[fname])
        rows.append('<tr><td>{0}</td><td>{1}</td><td>{2}</td></tr>'.format(
            href, count, cum_count))
    return clastic.Response(
        _LIST_MODULES_TEMPLATE.format(total, '\n'.join(rows)), mimetype="text/html")


def showmodule(module_name):
    module = sys.modules[module_name]
    if not hasattr(module, '__file__'):
        raise ValueError(
            "cannot display module {0} (no __file__)".format(module_name))
    if module.__file__.endswith(".py"):
        fname = module.__file__
    else:
        if not module.__file__.endswith(".pyc"):
            raise ValueError("cannot display module file {0} for {1}".format(
                module.__file__, module_name))
        fname = module.__file__[:-1]
    if not os.path.exists(fname):
        raise ValueError("could not find file {0} for {1}".format(
            fname, module_name))
    leaf_count, branch_count = _get_samples_by_line(fname)
    lines = []
    with open(fname) as f:
        for i, line in enumerate(f):
            lines.append((
                i + 1, cgi.escape(line),
                leaf_count[i + 1], branch_count[i + 1]))
    rows = '\n'.join(
        ['<tr><td><code>{0}</code></td>'
         '<td><pre><code class="python">{1}</code></pre></td>'
         '<td>{2}</td><td>{3}</td></tr>'.format(*e) for e in lines])
    return clastic.Response(
        _RENDER_MODULE_TEMPLATE.format(rows), mimetype="text/html")


def _get_samples_by_file():
    ctx = context.get_context()
    filename_leaf_samples = collections.defaultdict(int)
    filename_branch_samples = collections.defaultdict(int)
    total = 0
    if ctx.sampling:
        leaf_samples = {}
        branch_samples = {}
        for key, count in ctx.profiler.live_data_copy().items():
            if key[2] is None:
                leaf_samples[key] = count
                total += count
            else:
                branch_samples[key] = count
        for key, count in leaf_samples.items():
            filename_leaf_samples[key[0].co_filename] += count
        for key, count in branch_samples.items():
            filename_branch_samples[key[0].co_filename] += count
    return filename_leaf_samples, filename_branch_samples, total


def _get_samples_by_line(filename):
    ctx = context.get_context()
    line_leaf_samples = collections.defaultdict(int)
    line_branch_samples = collections.defaultdict(int)
    if ctx.sampling:
        for key, count in ctx.profiler.live_data_copy().items():
            if key[0].co_filename != filename:
                continue
            if key[2] is None:
                line_leaf_samples[key[1]] = count
            else:
                line_branch_samples[key[1]] = count
    return line_leaf_samples, line_branch_samples


_LIST_MODULES_TEMPLATE = '''
<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Modules</title>
</head>
<body>
    total samples {0}
    <table><tr><th>Module</th><th>Count</th><th>Shared Count</th></tr>
    {1}
    </table>
</body>
</html>
'''

_RENDER_MODULE_TEMPLATE = '''
<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Modules</title>
    <script src="//ajax.googleapis.com/ajax/libs/jquery/2.1.1/jquery.min.js"></script>
    <link rel="stylesheet" href="//cdnjs.cloudflare.com/ajax/libs/highlight.js/8.4/styles/default.min.css">
    <script src="//cdnjs.cloudflare.com/ajax/libs/highlight.js/8.4/highlight.min.js"></script>
    <style>
        code {{
                font-size: 15px;
                font-family: "Lucida Console", Monaco, "Bitstream Vera Sans Mono", monospace;
                white-space: pre;
            }}
        .code-table tr td pre {{ display: inline; }}
    </style>
    <script>
        $(document).ready(function() {{
            $('pre code').each(function(i, block) {{ hljs.highlightBlock(block); }})
            $('.code-table > tbody > tr').each(function(i, v) {{
                    v = $(v);
                    var zero_row = true;
                    v.children('td').each(function(i, v) {{
                        v = $(v);
                        if(v.find('code').length == 0 && v.text() != '0') {{
                            zero_row = false;
                        }}
                        if(v.find('th').length != 0) {{
                            zero_row = false;
                        }}
                    }});
                    if(zero_row) {{
                        v.addClass("zero-row");
                    }}
                }});
            }});
    </script>
</head>
<body>
    <a href="javascript:$('.zero-row').toggle()">show/hide zero rows</a>
    <table class="code-table">
      <tr><th>Line</th><th>Code</th><th>Count</th><th>Shared Count</th></tr>
    {0}
    </table>
</body>
</html>
'''








