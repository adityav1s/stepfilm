from caption_timeline import build_export_caption_tracks


# Step B was originally after step A. This test uses the edited/reordered B, A
# order and verifies translated cue text retains the same final-scene timing.
steps = [
    {"id": "step-b", "captureStart": 10, "captureEnd": 14, "captions": [
        {"start": 11, "end": 12, "text": "Open settings", "translations": {"fr": "Ouvrez les paramètres"}},
    ]},
    {"id": "step-a", "captureStart": 0, "captureEnd": 4, "captions": [
        {"start": 1, "end": 2, "text": "Choose billing", "translations": {"fr": "Choisissez la facturation"}},
    ]},
]
tracks = build_export_caption_tracks(steps, [2.4, 2.0], [3.0, 2.5])
assert tracks["original"] == [
    {"start": 0.78, "end": 1.38, "text": "Open settings", "stepId": "step-b"},
    {"start": 3.68, "end": 4.18, "text": "Choose billing", "stepId": "step-a"},
], tracks
assert tracks["fr"] == [
    {"start": 0.78, "end": 1.38, "text": "Ouvrez les paramètres", "stepId": "step-b"},
    {"start": 3.68, "end": 4.18, "text": "Choisissez la facturation", "stepId": "step-a"},
], tracks
print("caption timeline: PASS (reordered scene sequence, translated text, scene-local audio scaling)")
