"""Automatic scene-step detection for screen recordings."""

import base64
import re
import subprocess
from typing import Dict, List, Sequence, Tuple


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=([-+]?\d+(?:\.\d+)?)")
_PTS_RE = re.compile(r"pts_time:\s*([-+]?\d+(?:\.\d+)?)")


def select_scene_times(
    candidates: Sequence[Tuple[float, float]],
    duration: float,
    max_steps: int,
    min_gap: float,
) -> List[float]:
    """Select scene times, retaining the start frame and returning time order."""
    if max_steps <= 0 or duration < 0:
        return []

    start = 0.3 if duration >= 0.3 else 0.0
    if max_steps == 1:
        return [start]

    # Apply the gap chronologically first, so scene cuts clustered together do
    # not crowd out useful later cuts.  The start frame is always reserved.
    usable = []
    last = start
    for raw_time, raw_score in sorted(candidates, key=lambda item: item[0]):
        time = float(raw_time)
        if time < 0 or time > duration or time - last < min_gap:
            continue
        usable.append((time, float(raw_score)))
        last = time

    if len(usable) > max_steps - 1:
        usable = sorted(usable, key=lambda item: item[1], reverse=True)[: max_steps - 1]

    return sorted([start] + [time for time, _score in usable])


def _run(command: Sequence[str], *, capture_output: bool, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("ffmpeg could not process the video (it may be missing or timed out).") from exc


def _jpeg_at(ffmpeg: str, video_path: str, time: float) -> bytes:
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        "%.3f" % max(0.0, time),
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-vf",
        "scale='min(1600,iw)':-2",
        "-q:v",
        "3",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    result = _run(command, capture_output=True)
    data = result.stdout or b""
    if result.returncode != 0 or not data.startswith(b"\xff\xd8\xff"):
        return b""
    return data


def detect_scene_steps(
    ffmpeg: str,
    video_path: str,
    max_steps: int = 15,
    min_gap: float = 1.5,
    threshold: float = 0.012,
) -> List[Dict[str, object]]:
    """Detect visual changes and return timestamped JPEG data URLs."""
    if max_steps <= 0:
        return []

    # Scoring a small copy is much faster and still catches screen changes.
    filter_graph = "scale=320:-2,select='gt(scene,%s)',metadata=print" % float(threshold)
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-i",
        video_path,
        "-vf",
        filter_graph,
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = _run(command, capture_output=False, timeout=240)
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed while detecting scene changes in the video.")

    duration_match = _DURATION_RE.search(stderr)
    duration = 0.0
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    # metadata=print logs each selected frame's pts_time followed by its
    # lavfi.scene_score, so the biggest visual changes win when trimming.
    candidates = []
    for line in stderr.splitlines():
        pts = _PTS_RE.search(line)
        if pts:
            candidates.append([float(pts.group(1)), 1.0])
            continue
        score = _SCORE_RE.search(line)
        if score and candidates:
            candidates[-1][1] = float(score.group(1))
    if not candidates and duration <= 0:
        return []
    if duration <= 0:
        duration = max(time for time, _score in candidates)

    times = select_scene_times([tuple(item) for item in candidates], duration, max_steps, min_gap)
    results = []
    for index, time in enumerate(times):
        # The first changed frame is often a half-rendered page; grab the
        # settled screen a moment later, before the next change.
        settle_limit = times[index + 1] - 0.1 if index + 1 < len(times) else duration - 0.05
        grab = max(time, min(time + 0.7, settle_limit))
        jpeg = _jpeg_at(ffmpeg, video_path, grab) or _jpeg_at(ffmpeg, video_path, time)
        if jpeg:
            results.append({
                "at": float(time),
                "image": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
            })
    return results


def frame_at(ffmpeg: str, video_path: str, time: float) -> str:
    """Return one frame of the recording as a JPEG data URL, or "" on failure."""
    try:
        jpeg = _jpeg_at(ffmpeg, video_path, max(0.0, float(time)))
    except RuntimeError:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii") if jpeg else ""


def frame_before(ffmpeg: str, video_path: str, time: float, aspect=None) -> str:
    """The last frame shown strictly before `time`, as a JPEG data URL.

    Tab capture only emits a frame when the page changes, so seeking to a
    time returns the NEXT frame (often the page after the click). Decoding a
    short window ending at `time` and keeping the last frame gives the screen
    the user actually saw. `aspect` (viewport width/height) crops away the
    bars Chrome adds when the tab's shape differs from the recording's.
    """
    import os
    import tempfile
    end = max(0.0, float(time))
    start = max(0.0, end - 4.0)
    if end - start < 0.02:
        return frame_at(ffmpeg, video_path, 0)
    filters = []
    if isinstance(aspect, (int, float)) and 0.3 < aspect < 4:
        a = "%.6f" % aspect
        filters.append(f"crop='if(gt(iw/ih,{a}),trunc(ih*{a}/2)*2,iw)':'if(gt(iw/ih,{a}),ih,trunc(iw/{a}/2)*2)'")
    filters.append("scale='min(1600,iw)':-2")
    with tempfile.TemporaryDirectory() as temp:
        out = os.path.join(temp, "last.jpg")
        result = _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % start, "-i", video_path,
                       "-t", "%.3f" % (end - start), "-vf", ",".join(filters), "-q:v", "3", "-update", "1", "-y", out], capture_output=False)
        if result.returncode != 0 or not os.path.isfile(out):
            return ""
        with open(out, "rb") as handle:
            jpeg = handle.read()
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii") if jpeg.startswith(b"\xff\xd8\xff") else ""


PRE_ACTION_TYPES = {"click", "submit", "select", "upload"}


def apply_pre_action_frames(steps, ffmpeg, video_path, lead=0.03):
    """Use the tab recording to show each action's page just BEFORE it happened.

    A screenshot taken after a click can already show the next page or a
    closed dialog. The recording has the moment of the pointer press, which
    matches the element box measured then. Without a usable frame, fall back
    to the box re-measured on the screenshot (shotRect), or mark it stale.
    """
    replaced = 0
    for step in steps:
        action = step.get("action")
        if not isinstance(action, dict):
            continue
        shot_rect = action.pop("shotRect", None)
        if not (ffmpeg and video_path):
            action.pop("viewport", None)
        frame = ""
        if ffmpeg and video_path and action.get("type") in PRE_ACTION_TYPES and isinstance(step.get("at"), (int, float)):
            viewport = action.pop("viewport", None)
            aspect = viewport["w"] / viewport["h"] if isinstance(viewport, dict) and viewport.get("h") else None
            frame = frame_before(ffmpeg, video_path, step["at"] - lead, aspect)
        if frame:
            step["image"] = frame
            action.pop("stale", None)
            replaced += 1
        elif isinstance(shot_rect, dict) and isinstance(action.get("rect"), dict):
            rect = action["rect"]
            point = action.get("point")
            if isinstance(point, dict):
                action["point"] = {"x": min(1.0, max(0.0, point["x"] + shot_rect["x"] - rect["x"])),
                                   "y": min(1.0, max(0.0, point["y"] + shot_rect["y"] - rect["y"]))}
            action["rect"] = shot_rect
    return replaced
