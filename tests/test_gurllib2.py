from support import gurllib2


def test_urlopen():
    # not a great test, but something
    gurllib2.urlopen('https://www.ebay.com')


def test_build_opener():
    opener = gurllib2.build_opener()
    assert any(isinstance(h, gurllib2.GHTTPHandler) for h in opener.handlers)
    assert any(isinstance(h, gurllib2.GHTTPSHandler) for h in opener.handlers)
