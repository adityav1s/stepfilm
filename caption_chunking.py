"""Bounded, pause-aware chunk ranges for transcription without timestamps.

These intervals are estimates derived from detected pauses and chunk boundaries;
they are not word-level forced alignment.
"""


def build_caption_chunk_ranges(duration, silence_intervals, target=6.0, maximum=8.0):
    """Split audio into contiguous chunks, preferring detected pauses near target.

    `silence_intervals` contains (start, end) pairs in the local audio timeline.
    When a long speech run exceeds `maximum`, use a nearby pause (4.5–7.5s
    after the cursor) where possible; otherwise use a bounded fixed split.
    """
    duration = max(0.0, float(duration))
    if duration <= 0:
        return []
    target = max(1.0, float(target))
    maximum = max(target, float(maximum))
    pauses = []
    for interval in silence_intervals:
        try:
            left, right = map(float, interval)
        except (TypeError, ValueError):
            continue
        midpoint = (left + right) / 2
        if 0 <= left <= right <= duration and right - left >= 0.35:
            pauses.append(midpoint)
    pauses.sort()

    natural_cuts = []
    for cut in pauses:
        if 0.6 < cut < duration - 0.6 and (not natural_cuts or cut - natural_cuts[-1] >= 1.0):
            natural_cuts.append(cut)
    coarse = [0.0, *natural_cuts, duration]
    ranges = []
    for left, right in zip(coarse, coarse[1:]):
        cursor = left
        while right - cursor > maximum:
            low, high = cursor + target - 1.5, cursor + target + 1.5
            candidates = [cut for cut in pauses if low <= cut <= high and cut < right - 0.6]
            cut = min(candidates, key=lambda value: abs(value - (cursor + target))) if candidates else cursor + target
            ranges.append((cursor, cut))
            cursor = cut
        if right - cursor >= 0.6:
            ranges.append((cursor, right))
    return ranges or [(0.0, duration)]
