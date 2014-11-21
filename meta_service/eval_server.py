import code
import traceback
import cgi
import sys
from StringIO import StringIO

import clastic


def make_eval_app():
    resources = {"global_contexts": {}}
    routes = [
        ('/', get_console_html),
        ('/console/<eval_context>', get_console_html),
        ('/eval/<eval_context>', eval_command),
    ]

    return clastic.Application(routes, resources=resources)


def get_console_html(request, global_contexts, eval_context=None):
    if eval_context is None:
        return clastic.redirect(
            request.path + 'console/{0}'.format(len(global_contexts)))
    path, _, _ = request.path.rsplit('/', 2)
    callback = path + '/eval/{0}'.format(eval_context)
    return clastic.Response(
        CONSOLE_HTML.replace("CALLBACK_URL", callback), mimetype="text/html")


def eval_command(request, eval_context, global_contexts):
    try:
        ctx = global_contexts.setdefault(
            eval_context, {'locals': {}, 'sofar': []})
        line = request.values['command']
        try:
            cmd = code.compile_command("\n".join(ctx['sofar'] + [line]))
            if cmd:  # complete command
                ctx['sofar'] = []
                buff = StringIO()
                sys.stdout = buff
                try:
                    exec cmd in ctx['locals']
                except Exception:
                    resp = traceback.format_exc()
                sys.stdout = sys.__stdout__
                resp = buff.getvalue()
            else:  # incomplete command
                ctx['sofar'].append(line)
                resp = "..."
        except SyntaxError as e:
            resp = repr(e)
            ctx['sofar'] = []
        except (OverflowError, ValueError) as e:
            resp = repr(e)
            ctx['sofar'] = []
        return clastic.Response(cgi.escape(resp))
    finally:
        # try to really ensure stdout isn't left broken
        sys.stdout = sys.__stdout__


# TODO: use a two column table for better cut + paste
# <tr> <td> >>> </td> <td> OUTPUT </td> </tr>

CONSOLE_HTML = '''
<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Console</title>
    <script src="//ajax.googleapis.com/ajax/libs/jquery/2.1.1/jquery.min.js"></script>
    <style>
        .cli_output {
            bottom: 0;
        }
        #cli_input, #console {
            font-size: 15px;
            font-family: "Lucida Console", Monaco, "Bitstream Vera Sans Mono", monospace;
        }
    </style>
</head>
<body>

<div style="position:absolute; bottom:0; width: 100%">
<div id="console" style="overflow:scroll; height:400px; width: 100%"></div>
&gt;&gt;&gt;<input type="text" id="cli_input" style="width: 50%"></input>
</div>

<script>
$('#cli_input').keyup(function(event) {
    if(event.keyCode == 13) {
        process_input();
    }
});

function console_append(val) {
    $('#console').append('<div class="cli_output">' + val + '</div>');
    $('#console').scrollTop($('#console')[0].scrollHeight);
}

function process_input() {
    val = $('#cli_input').val();
    console_append('&gt;&gt;&gt; ' + val);
    $('#cli_input').val('');
    $.ajax({
            type: "POST",
            url: "CALLBACK_URL",
            data: {"command": val},
            success: console_append
        })
}
</script>
</body>
</html>
'''

