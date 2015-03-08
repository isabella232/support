from support import gurllib2

def test_urlopen():
    # not a great test, but something
    resp = gurllib2.urlopen('https://www.paypal.com')
    assert resp.getcode() == 200
    lines = resp.readlines()
    assert lines


def test_build_opener():
    opener = gurllib2.build_opener()
    assert any(isinstance(h, gurllib2.GHTTPHandler) for h in opener.handlers)
    assert any(isinstance(h, gurllib2.GHTTPSHandler) for h in opener.handlers)
