from collections import namedtuple

Segment = namedtuple('Segment', 'begin end')


class SegmentTree(object):

    def __init__(self, segments):
        # left to right, shortest as a tie breaker -- the way tuples
        # normally sort
        segments.sort()
        self.segments = segments

    def __repr__(self):
        cn = self.__class__.__name__
        return '%s(%r)' % (cn, self.segments)

    def find(self, value, raises=True):
        upper_idx = len(self.segments) - 1
        low, high = 0, upper_idx
        segments = self.segments

        while low <= high:
            mid = (low + high) / 2

            low_seg, high_seg = segments[low], segments[high]
            mid_seg = segments[mid]

            # either side of the middle segment
            left_seg = mid > 0 and segments[mid - 1]
            right_seg = mid < upper_idx and segments[mid + 1]

            if mid_seg[0] <= value <= mid_seg[1]:
                # found
                return mid_seg
            elif left_seg and low_seg[0] <= value <= left_seg[1]:
                high = mid - 1
            elif right_seg and right_seg[0] <= value <= high_seg[1]:
                low = mid + 1
            elif raises:
                raise ValueError('Missing value %s' % value)
            else:
                return

    def __contains__(self, value):
        return bool(self.find(value, False))
