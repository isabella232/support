import itertools

class LRU(object):
    '''
    Extremely simple Leat Recently Used (LRU) eviction cache which ensures 
    that the most recently accessed _size_ items are kept.
    '''
    def __init__(self, size):
        self.size = size
        self.data = {}
        self.opnum = itertools.count(0)

    def __setitem__(self, key, value):
        self.data[key] = self.opnum.next(), value
        if len(self.data) > 2 * self.size:
            items = [(k, v) for k, v in self.data.items()]
            items.sort(key=lambda e: e[1][0], reverse=True)
            self.data = dict(items[:self.size])

    def __getitem__(self, key):
        if key in self.data:
            value = self.data[key][1]
            self.data[key] = self.opnum.next(), value
            return value
        raise KeyError("key not found or expired")

    def __contains__(self, key): 
        return key in self.data

    def __len__(self): 
        return len(self.data)


class DefaultLRU(LRU):
    '''
    An LRU which behaves like a collections.defaultdict on missing keys.
    '''
    def __init__(self, size, default):
        super(DefaultLRU, self).__init__(size)
        self.default = default

    def __getitem__(self, key):
        try:
            return super(DefaultLRU, self).__getitem__(key)
        except KeyError:
            value = self.default()
            self[key] = value
            return value


if __name__ == "__main__":
    c = LRU(3)
    for i in range(10):
        c[i] = i
        c[0]  # keep 0 in top 3 most recent
    assert 0 in c
    assert 8 in c
    assert 9 in c

    dc = DefaultLRU(3, int)
    for i in range(10):
        dc[i]
        dc[0]  # keep 0 in top 3 most recent
    assert 0 in dc
    assert 8 in dc
    assert 9 in dc
