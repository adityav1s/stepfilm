from caption_chunking import build_caption_chunk_ranges


def assert_partition(duration, ranges, maximum=8.0):
    assert ranges and ranges[0][0] == 0.0
    assert abs(ranges[-1][1] - duration) < 1e-9
    for index, (start, end) in enumerate(ranges):
        assert end > start
        assert end - start <= maximum + 1e-9
        if index:
            assert abs(start - ranges[index - 1][1]) < 1e-9


# Long unbroken narration gets bounded chunks without gaps or overlaps.
ranges = build_caption_chunk_ranges(20.0, [])
assert_partition(20.0, ranges)
assert ranges == [(0.0, 6.0), (6.0, 12.0), (12.0, 20.0)]

# Artificial splits move to a nearby pause instead of bisecting speech at 6s.
ranges = build_caption_chunk_ranges(15.0, [(6.6, 7.0), (12.6, 13.0)])
assert_partition(15.0, ranges)
assert ranges[0] == (0.0, 6.8)

# Natural pauses remain the preferred phrase boundaries; short recordings stay whole.
assert build_caption_chunk_ranges(3.0, [(1.2, 1.7)]) == [(0.0, 1.45), (1.45, 3.0)]
assert build_caption_chunk_ranges(0.0, []) == []
print("caption chunking: PASS (contiguous bounded ranges, pause-aware long speech, short clips)")
