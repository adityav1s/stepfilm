from caption_timeline import build_export_caption_tracks
from server import format_caption_track, group_caption_words, offset_caption_words


steps = [
    {"id": "step-b", "captureStart": 10, "captureEnd": 14, "captions": [
        {"start": 11, "end": 12, "text": "Open settings", "translations": {"fr": "Ouvrez les paramètres"}},
    ]},
    {"id": "step-a", "captureStart": 0, "captureEnd": 4, "captions": [
        {"start": 1, "end": 2, "text": "Choose billing", "translations": {"fr": "Choisissez la facturation"}},
    ]},
]
tracks = build_export_caption_tracks(steps, [2.4, 2.0], [3.0, 2.5])
translated_vtt = format_caption_track(tracks, "fr", "vtt")
assert "00:00:00.780 --> 00:00:01.380\nOuvrez les paramètres" in translated_vtt, translated_vtt
assert "00:00:03.680 --> 00:00:04.180\nChoisissez la facturation" in translated_vtt, translated_vtt
original_srt = format_caption_track(tracks, "original", "srt")
assert "00:00:00,780 --> 00:00:01,380\nOpen settings" in original_srt, original_srt
assert "00:00:03,680 --> 00:00:04,180\nChoose billing" in original_srt, original_srt
print("caption track formatting: PASS (reordered scene sequence, translated/original cue text, VTT/SRT timestamps)")

# Whisper word timestamps are preserved when converted into readable cues.
word_cues = group_caption_words([
    {"start": 0.12, "end": 0.28, "text": "Open"},
    {"start": 0.31, "end": 0.61, "text": "the"},
    {"start": 0.65, "end": 1.02, "text": "billing"},
    {"start": 1.05, "end": 1.31, "text": "page."},
    {"start": 3.0, "end": 3.3, "text": "Save"},
])
assert word_cues == [
    {"start": 0.12, "end": 1.31, "text": "Open the billing page."},
    {"start": 3.0, "end": 3.3, "text": "Save"},
], word_cues
assert group_caption_words([{"start": 1, "end": 1, "text": "invalid"}, {"start": 2, "end": 3, "text": "valid"}]) == [
    {"start": 2.0, "end": 3.0, "text": "valid"}
]
assert offset_caption_words(
    [{"start": 0.1, "end": 0.4, "word": "Save"}, {"start": 2.1, "end": 2.2, "word": "late"}],
    8.0, 2.0, 4.0,
) == [{"start": 10.1, "end": 10.4, "text": "Save"}], "phrase-relative times must be offset and clipped"
print("caption word timing: PASS (preserved Whisper timing, readable grouping, invalid word filtering)")
