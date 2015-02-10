
import support
from support import Group
from clastic import Application
from clastic import render_basic


def home_handler():
    return 'Hello!'


def main():
    app = Application([('/', home_handler, render_basic)])

    support.init()
    apps = [(app, ('0.0.0.0', 8888), False)]

    group = Group(wsgi_apps=apps,
                  prefork=True,
                  num_workers=2,
                  daemonize=True)
    group.serve_forever()


if __name__ == '__main__':
    main()
