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


if __name__ == "__main__":
    unittest.main()
