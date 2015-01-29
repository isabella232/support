
import support
from support import Group
from clastic import Application


def home_handler():
    return 'Hello!'


app = Application([('/', home_handler)])

support.init()
group = Group(wsgi_apps=[(app, ('0.0.0.0', 8888), False)])
group.run()
