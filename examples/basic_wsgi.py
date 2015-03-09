
import platform

from support import Group
from support import meta_service
from clastic import Application
from clastic import render_basic


def home_handler():
    return 'Hello!'


def main():
    app = Application([
        ('/', home_handler, render_basic),
        ('/meta', meta_service.create_meta_app())])
    apps = [(app, ('0.0.0.0', 8888), False)]

    if platform.system() == 'Windows':
        group = Group(apps)
    else:
        group = Group(wsgi_apps=apps,
                      prefork=True,
                      num_workers=2,
                      daemonize=True)
    group.serve_forever()


if __name__ == '__main__':
    main()
