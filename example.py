
import support
from support import Group
from clastic import Application
from clastic import render_basic

from support.server_group import SimpleSupportApplication

def home_handler():
    return 'Hello!'


app = Application([('/', home_handler, render_basic)])

support.init()
group = Group(wsgi_apps=[(app, ('0.0.0.0', 8888), False)])
group.serve_forever()
