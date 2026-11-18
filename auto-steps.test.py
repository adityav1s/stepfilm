import base64
import os
import shutil
import subprocess
import tempfile
import unittest

from auto_steps import detect_scene_steps, select_scene_times


class SelectSceneTimesTests(unittest.TestCase):
    def test_always_keeps_start_and_sorts(self):
        self.assertEqual(select_scene_times([(4, 1), (2, 2)], 6, 5, 1.5), [0.3, 2.0, 4.0])

    def test_drops_close_changes(self):
        self.assertEqual(select_scene_times([(0.4, 9), (1.0, 8), (2.6, 1)], 4, 5, 1.5), [0.3, 2.6])

    def test_keeps_largest_scores_when_capped(self):
        result = select_scene_times([(1, 1), (3, 9), (5, 4), (7, 8)], 8, 3, 1.5)
        self.assertEqual(result, [0.3, 3.0, 7.0])

    def test_start_is_zero_for_short_video(self):
        self.assertEqual(select_scene_times([(0.1, 1)], 0.2, 3, 0.1), [0.0, 0.1])

    def test_max_steps_and_invalid_limits(self):
        self.assertEqual(select_scene_times([(1, 1), (3, 2)], 4, 1, 1), [0.3])
        self.assertEqual(select_scene_times([(1, 1)], 4, 0, 1), [])


class FfmpegIntegrationTests(unittest.TestCase):
    def test_three_colour_webm(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            print("SKIP: ffmpeg missing")
            return
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "colours.webm")
            command = [
                ffmpeg, "-y", "-f", "lavfi", "-i",
                "color=c=black:s=320x180:d=2[r];"
                "color=c=white:s=320x180:d=2[g];"
                "color=c=red:s=320x180:d=2[b];"
                "[r][g][b]concat=n=3:v=1:a=0,format=yuv420p",
                "-c:v", "libvpx-vp9", "-lossless", "1", "-g", "1", path,
            ]
            created = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if created.returncode != 0:
                self.fail(created.stderr.decode("utf-8", errors="replace"))
            steps = detect_scene_steps(ffmpeg, path, max_steps=3, min_gap=1.0, threshold=0.06)
            self.assertEqual(len(steps), 3)
            self.assertEqual([round(step["at"]) for step in steps], [0, 2, 4])
            for step in steps:
                self.assertTrue(step["image"].startswith("data:image/jpeg;base64,"))
                self.assertTrue(base64.b64decode(step["image"].split(",", 1)[1]).startswith(b"\xff\xd8\xff"))





def _pre_action_frames_test():
    import base64 as b64
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    from auto_steps import apply_pre_action_frames
    ffmpeg = shutil.which("ffmpeg")
    # Without video: the re-measured box is used and the point follows it.
    steps = [{"at": 1.0, "image": "orig", "action": {"type": "click", "rect": {"x": .1, "y": .1, "width": .1, "height": .1}, "point": {"x": .15, "y": .15},
                                                    "shotRect": {"x": .1, "y": .5, "width": .1, "height": .1}}}]
    assert apply_pre_action_frames(steps, None, None) == 0
    assert steps[0]["image"] == "orig" and steps[0]["action"]["rect"]["y"] == .5 and abs(steps[0]["action"]["point"]["y"] - .55) < 1e-9 and "shotRect" not in steps[0]["action"]
    if not ffmpeg:
        print("pre-action frames: SKIP video part (FFmpeg unavailable)")
        return
    with tempfile.TemporaryDirectory() as temp:
        video = Path(temp) / "rec.webm"
        # White page until 2.0 s (the click), then the page turns black.
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=white:s=320x240:r=30:d=2", "-f", "lavfi", "-i", "color=black:s=320x240:r=30:d=2",
                        "-filter_complex", "[0][1]concat=n=2", "-c:v", "libvpx-vp9", str(video)], check=True, timeout=60)
        steps = [{"at": 2.05, "image": "after-click", "action": {"type": "click", "stale": True, "rect": {"x": .1, "y": .1, "width": .1, "height": .1}, "shotRect": {"x": 0, "y": 0, "width": .1, "height": .1}}},
                 {"at": 2.5, "image": "nav", "action": {"type": "navigate"}}]
        assert apply_pre_action_frames(steps, ffmpeg, str(video)) == 1
        assert steps[0]["image"].startswith("data:image/jpeg;base64,") and "stale" not in steps[0]["action"]
        assert steps[0]["action"]["rect"]["x"] == .1, "the pointer-down box matches the pre-click frame"
        assert steps[1]["image"] == "nav", "navigation steps keep their own screenshot"
        frame = Path(temp) / "f.jpg"
        frame.write_bytes(b64.b64decode(steps[0]["image"].split(",", 1)[1]))
        mean = subprocess.run([ffmpeg, "-i", str(frame), "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"], capture_output=True, text=True).stderr
        yavg = float(mean.split("lavfi.signalstats.YAVG=")[1].split()[0])
        assert yavg > 200, f"frame should show the page before the click (white), got luma {yavg}"
    print("pre-action frames: PASS (pre-click frame used, fallbacks correct)")


if __name__ == "__main__":
    _pre_action_frames_test()
    unittest.main()
