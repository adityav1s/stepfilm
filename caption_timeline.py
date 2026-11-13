"""Build export-aligned caption tracks in the final scene order."""

from math import isfinite


def build_export_caption_tracks(steps, audio_durations, scene_durations, audio_lead=0.18):
    """Map capture-time cues to the rendered guide timeline.

    Cue positions are scaled from each step's captured interval to the actual
    audio duration measured during render. This preserves timing for recorded
    audio and gives synthetic or replacement audio a clearly defined relative
    mapping when its duration differs from the recording.
    """
    if not (len(steps) == len(audio_durations) == len(scene_durations)):
        raise ValueError("Caption timing needs one audio and scene duration per step.")
    tracks = {}
    scene_offset = 0.0
    for step, audio_duration, scene_duration in zip(steps, audio_durations, scene_durations):
        audio_duration = max(0.0, float(audio_duration))
        scene_duration = max(0.0, float(scene_duration))
        capture_start = max(0.0, float(step.get("captureStart", 0)))
        capture_end = max(capture_start, float(step.get("captureEnd", capture_start)))
        capture_span = capture_end - capture_start
        scale = audio_duration / capture_span if capture_span > 0 else 1.0
        for caption in step.get("captions") or []:
            try:
                start = float(caption.get("start", 0))
                end = float(caption.get("end", 0))
            except (TypeError, ValueError):
                continue
            if not (isfinite(start) and isfinite(end) and end > start):
                continue
            local_start = max(0.0, (start - capture_start) * scale + audio_lead)
            local_end = min(scene_duration, max(0.0, (end - capture_start) * scale + audio_lead))
            if local_end <= local_start:
                continue
            entries = {"original": str(caption.get("text", "")).strip()}
            translations = caption.get("translations")
            if isinstance(translations, dict):
                entries.update({str(language): str(text).strip() for language, text in translations.items()})
            for language, text in entries.items():
                if not text:
                    continue
                tracks.setdefault(language, []).append({
                    "start": round(scene_offset + local_start, 3),
                    "end": round(scene_offset + local_end, 3),
                    "text": text,
                    "stepId": step.get("id", ""),
                })
        scene_offset += scene_duration
    for cues in tracks.values():
        cues.sort(key=lambda cue: (cue["start"], cue["end"]))
    return tracks
