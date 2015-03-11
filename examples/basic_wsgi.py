
import platform

from support import Group
from support import meta_service
from clastic import Application
from clastic import render_basic

PORT = 8888
META_PORT = 8889


def home_handler():
    return 'Welcome to SuPPort!'


def main():
    app = Application([('/', home_handler, render_basic)])
    meta_app = meta_service.create_meta_app()
    wsgi_apps = [(app, ('0.0.0.0', PORT), False),
                 (meta_app, ('0.0.0.0', META_PORT), False)]

    if platform.system() == 'Windows':
        group = Group(wsgi_apps=wsgi_apps)
    else:
        group = Group(wsgi_apps=wsgi_apps,
                      prefork=True,
                      num_workers=2,
                      daemonize=True)
    group.serve_forever()


if __name__ == '__main__':
    main()
