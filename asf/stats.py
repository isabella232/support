# -*- coding: utf-8 -*-

import time
from collections import defaultdict

from clastic.core import Application
from clastic.middleware import Middleware
from clastic.render import default_response

import faststat

# TODO: what are some sane-default intervals?


class StatsMiddleware(Middleware):
    def __init__(self):
        self.hits = defaultdict(lambda: defaultdict(faststat.Stats))
        self.route_hits = defaultdict(faststat.Stats)
        self.url_hits = defaultdict(faststat.Stats)

    def request(self, next, request, _route):
        start_time = time.time()
        try:
            resp = next()
            resp_status = repr(getattr(resp, 'status_code', type(resp)))
        except Exception as e:
            # see Werkzeug #388
            resp_status = repr(getattr(e, 'code', type(e)))
            raise
        finally:
            end_time = time.time()
            elapsed_time = end_time - start_time
            self.hits[_route][resp_status].add(elapsed_time)
            self.url_hits[request.path].add(elapsed_time)
        return resp


def dict_from_faststat(fs):
    stats = {}
    attrs = ("n", "mean", "variance", "percentiles", "max", "min")
    for a in attrs:
        stats[a] = getattr(fs, a)
    for p, v in stats["percentiles"].items():
        stats["percentiles"][round(p, 2)] = v  # make percentiles JSON neatly in pyth

    return stats


def get_route_stats(route_hist):
    ret = {}
    for status in route_hist:
        ret[status] = dict_from_faststat(route_hist[status])

    return ret


def _get_stats_dict(_application):
    try:
        stats_mw = [mw for mw in _application.middlewares
                    if isinstance(mw, StatsMiddleware)][0]
    except IndexError:
        return {'error': "StatsMiddleware doesn't seem to be installed"}
    rt_hits = stats_mw.hits
    return {'resp_counts': dict([(url, rh.n) for url, rh
                                 in stats_mw.url_hits.items()]),
            'route_stats': dict([(rt.rule, get_route_stats(rt_hits[rt])) for rt
                                 in rt_hits if rt_hits[rt]])}


def _create_app():
    routes = [('/', _get_stats_dict, default_response)]
    mws = [StatsMiddleware()]
    app = Application(routes, middlewares=mws)
    return app


if __name__ == '__main__':
    sapp = _create_app()
    sapp.serve()
    
