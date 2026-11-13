import shutil
import subprocess
import tempfile
from pathlib import Path

from server import video_analysis_sample_times, select_video_analysis_times, map_video_analysis_events


ffmpeg = shutil.which("ffmpeg")
if not ffmpeg:
    print("video analysis sampling: SKIP (FFmpeg unavailable)")
    raise SystemExit(0)

with tempfile.TemporaryDirectory() as temp:
    folder = Path(temp)
    clip = folder / "synthetic.mp4"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=12:duration=18", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    ], check=True, capture_output=True, timeout=30)
    duration = 18.0
    timestamps = video_analysis_sample_times(duration)
    assert len(timestamps) == 6, timestamps
    assert timestamps == sorted(timestamps) and len(set(timestamps)) == len(timestamps), timestamps
    assert timestamps[0] > 0 and timestamps[-1] < duration, timestamps
    assert len(timestamps) == 6 and timestamps[0] < timestamps[-1], timestamps
    frames = []
    for index, timestamp in enumerate(timestamps):
        frame = folder / f"sample-{index}.jpg"
        subprocess.run([
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{timestamp:.3f}",
            "-i", str(clip), "-frames:v", "1", "-vf", "scale=w='min(640,iw)':h=-2", "-q:v", "7", str(frame),
        ], check=True, capture_output=True, timeout=20)
        assert frame.is_file() and frame.stat().st_size > 0, (timestamp, frame)
        frames.append(frame)
    assert len(frames) == len(timestamps) == 6

assert len(video_analysis_sample_times(600, max_frames=48)) == 48, "coarse scan count must be bounded"
assert video_analysis_sample_times(0) == []
scan = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
steady = [bytes([20]) * 8 for _ in scan]
cut = list(steady)
cut[6] = bytes([230]) * 8
selected = select_video_analysis_times(scan, cut, max_frames=4)
assert len(selected) == 4 and selected == sorted(selected)
assert 6 in selected, selected
assert len(select_video_analysis_times(scan, steady, max_frames=4)) == 4
assert select_video_analysis_times([], []) == []
samples = [1.0, 4.0, 9.0]
events = [
    {"time": -50, "action": " Before clip "},
    {"time": 2.5, "action": "Midpoint tie"},
    {"time": 3.4, "action": "Open menu"},
    {"time": 3.6, "action": "Open menu"},
    {"time": 999, "action": "Submit"},
    {"time": float("nan"), "action": "invalid"},
    {"time": 4, "action": "  "},
    None,
    {"time": "bad", "action": "invalid"},
    {"time": 4, "action": "Different action"},
]
mapped = map_video_analysis_events(events, samples, 10)
assert mapped == [
    {"time": 1.0, "action": "Before clip"},
    {"time": 1.0, "action": "Midpoint tie"},
    {"time": 4.0, "action": "Open menu"},
    {"time": 4.0, "action": "Different action"},
    {"time": 9.0, "action": "Submit"},
], mapped
assert map_video_analysis_events(events, [], 10) == []
print("video analysis sampling: PASS (bounded coarse scan, adaptive visual-change selection, timestamped extraction)")
