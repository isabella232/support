class Cache(object):
    'empty base class to enable isinstance(foo, cache.Cache)'


class LRUCache(Cache):
    '''
    Implements an LRU cache based on a linked list.
    Performance is about 1.1 microseconds per set/get on a core i7
    '''
    def __init__(self, maxlen=10000):
        self.map = {}
        self.root = root = []
        root[:] = [root, root]
        self.maxlen = maxlen

    def __getitem__(self, key):
        val = self.map[key][3]
        self[key] = val
        return val

    def add(self, key, val):
        # node[0] = prev; node[1] = next
        root = self.root
        if key in self.map:
            # remove from map
            link = self.map.pop(key)
            # remove from list
            link[0][1], link[1][0] = link[1], link[0]
        else:
            link = [None, None, key, val]
        discard = None
        if len(self.map) >= self.maxlen:
            # pop and discard the oldest item
            discard = root[0]
            discard[0][1], root[0] = root, discard[0]
            self.map.pop(discard[2])
        # insert into map
        self.map[key] = link
        # insert into list
        link[0], link[1] = root, root[1]
        root[1][0] = link
        root[1] = link
        if root[0] is root:
            root[0] = root[1]
        if discard:
            return discard[2], discard[3]

    __setitem__ = add

    _unset = object()
    
    def pop(self, key=_unset):
        # remove from map and list
        if key is LRUCache._unset:
            link = self.root[0]
            self.map.pop(link[2])
        else:
            link = self.map.pop(key)
        link[0][1], link[1][0] = link[1], link[0]
        return link[3]

    def __contains__(self, key):
        return key in self.map

    def __len__(self):
        return len(self.map)

    def items(self):
        return [(k, self.map[k][3]) for k in self.map]

    def keys(self):
        return self.map.keys()

    def values(self):
        return [self.map[k][3] for k in self.map]


class SegmentedCache(Cache):
    '''
    Implements a Segmented LRU cache based on an LRU cache.
    '''
    def __init__(self, maxlen=10000):
        self.probationary = LRUCache(maxlen)
        self.protected = LRUCache(maxlen / 2)
        self.maxlen = maxlen

    def __getitem__(self, key):
        if key in self.protected.map:
            # already protected, nothing to do
            return self.protected[key]
        if key in self.probationary.map:
            # promote to protected
            val = self.probationary.pop(key)
            discard = self.protected.add(key, val)
            if discard:
                self.probationary.add(discard[0], discard[1])
            return val
        raise KeyError(key)

    def add(self, key, val):
        if key in self.protected:
            self.protected[key] = val
        elif key in self.probationary:
            self.probationary.pop(key)
            discard = self.protected.add(key, val)
            if discard:
                self.probationary.add(discard[0], discard[1])
        else:  # totally brand new key being added
            self.probationary.add(key, val)
            if len(self.probationary.map) + len(self.protected.map) > self.maxlen:
                self.probationary.pop()

    __setitem__ = add

    def __contains__(self, key):
        return key in self.protected or key in self.probationary

    def __len__(self):
        return len(self.protected) + len(self.probationary)

    def items(self):
        return self.protected.items() + self.probationary.items()

    def keys(self):
        return self.protected.keys() + self.probationary.keys()

    def values(self):
        return self.protected.values() + self.probationary.values()


class DefaultLRU(SegmentedCache):
    '''
    A Segmented LRU Cache which behaves like a collections.defaultdict on missing keys.
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


class EmptyCache(Cache):
    '''
    Provides the same API as a cache, but doesn't actually store anything.
    Can be substituted for a real cache for applications where memory is critical.
    '''
    def __setitem__(self, key, value):
        pass

    def __getitem__(self, key):
        raise KeyError("EmptyCache stores no data")

    def __contains__(self, key):
        return False

    def __len__(self):
        return 0

    def items(self):
        return []

    def keys(self):
        return []

    def values(self):
        return []


class DefaultEmptyCache(EmptyCache):
    def __init__(self, default):
        self.default = default

    def __getitem__(self, key):
        return self.default()


if __name__ == "__main__":
    c = LRUCache(4)
    for i in range(10):
        c[i] = i
        c[0]  # keep 0 in top 3 most recent
    assert 0 in c
    assert 7 in c
    assert 8 in c
    assert 9 in c

    dc = DefaultLRU(4, int)
    for i in range(10):
        dc[i]
        dc[i]  # let items get into protected segment
        dc[0]  # keep 0 in top 3 most recent
    assert 0 in dc
    assert 7 in dc
    assert 8 in dc
    assert 9 in dc

    cache_size = 7
    sg = SegmentedCache(cache_size)
    r = range(10000)
    for i in r:
        sg[i] = i
    for i in r[-cache_size:]:
        assert i in sg

    import time

    r = range(int(5e5))
    s = time.time()
    for i in r:
        sg[i] = i
        sg[i] = i
    print "{0:.2f}us".format(time.time() - s)

