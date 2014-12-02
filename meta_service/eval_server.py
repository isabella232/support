import code
import traceback
import cgi
import sys
import json
import gc
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
    if eval_context not in global_contexts:
        global_contexts[eval_context] = EvalContext()
    ctx = global_contexts[eval_context]
    resp = ctx.eval_line(request.values['command'])
    complete = resp is not None
    if complete:
        resp = {
            'complete': True,
            'data': cgi.escape(resp),
            'href': '/meta/object/' + str(id(ctx.last_object))
        }
    else:
        resp = {'complete': False, 'data': '', 'href': ''}
    return clastic.Response(json.dumps(resp), mimetype="application/json")


_sys_displayhook = sys.displayhook


class EvalContext(object):
    def __init__(self):
        self.last_object = None
        self.locals = {}
        self.sofar = []
        self._sys_displayhook = sys.displayhook
        self.last_cmd = 0

    def _displayhook(self, value):
        self.last_object = value
        return _sys_displayhook(value)

    def eval_line(self, line):
        '''
        evaluate a single line of code; returns None if more lines required to compile,
        a string if input is complete
        '''
        try:
            cmd = code.compile_command("\n".join(self.sofar + [line]))
            if cmd:  # complete command
                self.sofar = []
                buff = StringIO()
                sys.stdout = buff
                sys.stderr = buff
                try:
                    exec cmd in self.locals
                    return buff.getvalue()
                except:
                    return traceback.format_exc()
            else:  # incomplete command
                self.sofar.append(line)
                return
        except (OverflowError, ValueError, SyntaxError) as e:
            self.sofar = []
            return repr(e)
        finally:  # ensure sys.stdout / sys.stderr back to normal
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__





# TODO: use a two column table for better cut + paste
# <tr> <td> >>> </td> <td> OUTPUT </td> </tr>

CONSOLE_HTML = '''
<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Console</title>
    <script src="//ajax.googleapis.com/ajax/libs/jquery/2.1.1/jquery.min.js"></script>
    <link rel="stylesheet" href="//cdnjs.cloudflare.com/ajax/libs/highlight.js/8.4/styles/default.min.css">
    <script src="//cdnjs.cloudflare.com/ajax/libs/highlight.js/8.4/highlight.min.js"></script>
    <style>
        .cli_output {
            bottom: 0;
        }
        #cli_input, #console, #prompt, code {
            font-size: 15px;
            font-family: "Lucida Console", Monaco, "Bitstream Vera Sans Mono", monospace;
            white-space: pre;
        }
        .error {
            background: #FEE;
        }
        .output {
            background: #EFF;
        }
    </style>
</head>
<body>

<div style="position:absolute; bottom:0; width: 100%">
<div id="console" style="overflow:scroll; height:400px; width: 100%">
    <table id="console_out"></table>
</div>
<span id="prompt" style="width: 3em">&gt;&gt;&gt;</span>
<input type="text" id="cli_input" style="width: 50%"></input>
</div>

<script>
$('#cli_input').keyup(function(event) {
    if(event.keyCode == 13) {
        process_input();
    }
});


function console_append(prompt, val, href) {
    if(href) {
        val = '<a href="' + href +'">' + val + '</a>';
    }
    if(prompt == '') {
        if(val.indexOf("Traceback") === 0) {
            var code_class = "error";
        } else {
            var code_class = "output";
        }
        var newrow = '<td colspan=2 class="' + code_class + '"><code>' +
                     val + '</code></td>';
    } else {
        var newrow = '<td style="width: 3em">' + prompt + '</td>' +
                     '<td><code class="python">' + val + '</code></td>';
    }
    newrow = '<tr>' + newrow + '</tr>';
    $('#console').append(newrow);

    $('#console tr:last td:last code.python').each(
        function(i, block) {
            hljs.highlightBlock(block);
        });
    $('#console').scrollTop($('#console')[0].scrollHeight);
}


function process_input() {
    var val = $('#cli_input').val();
    console_append($("#prompt").text(), val.replace(/ /g, '&nbsp;'));
    $('#cli_input').val('');
    $.ajax({
            type: "POST",
            url: "CALLBACK_URL",
            data: {"command": val},
            success: function(data) {
                if(data.complete) {
                    var prompt = ">>>";
                } else {
                    var prompt = "...";
                }
                $("#prompt").text(prompt);
                if(data.data != '') {
                    console_append('', data.data, data.href);
                }
            }
        });
}
</script>
</body>
</html>
'''

