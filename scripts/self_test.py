#!/usr/bin/env python3
"""Offline regressions; temporary synthetic media only, no paid API or models.

Set FFMPEG to an executable (with sibling ffprobe), optionally GODOT to test
the actual AnimatedSprite2D player. Run with the NumPy/OpenCV environment.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ark, frames, atlas = load("ark_video"), load("prepare_animation"), load("pack_atlas")
voice = load("qwen_voice")


class ArkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _, png = cv2.imencode(".png", np.zeros((12, 12, 3), np.uint8))
        (self.root / "design.png").write_bytes(png.tobytes())
        self.recipe = {"job_id": "bear-test-v1", "character": "bear", "action": "idle",
                       "label": "闭嘴待机", "purpose": "offline test", "playback": "loop",
                       "reference_image": "design.png", "reference_sha256": ark.digest(png.tobytes()),
                       "prompt": "【角色一致性】保留设计。【动作要求】4秒闭嘴。【画面与背景】1:1纯绿#00FF00。【禁止事项】不缩放。",
                       "request": {"model": "offline-test", "resolution": "480p", "duration": 4,
                                   "ratio": "1:1", "generate_audio": False, "watermark": False}}
        self.path = self.root / "job.json"
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.recipe, ensure_ascii=False))

    def args(self, command, plan=None):
        return argparse.Namespace(command=command, job=Path("job.json"), workspace=self.root,
                                  plan_sha256=plan, task_id="task-test")

    def test_plan_never_reads_key_or_network(self):
        with patch.object(ark, "read_key", side_effect=AssertionError("key read")), patch.object(ark, "api_json", side_effect=AssertionError("network")):
            plan = ark.execute(self.args("plan"))
        self.assertFalse(plan["network_called"])
        self.assertFalse((self.root / "runtime").exists())
        self.assertEqual(plan["references"][0]["sha256"], self.recipe["reference_sha256"])

    def test_cost_guardrails_and_reference_drift(self):
        for field, bad in (("resolution", "720p"), ("duration", 5), ("duration", True), ("frames", 97), ("ratio", "9:16"), ("generate_audio", True)):
            original = dict(self.recipe["request"])
            self.recipe["request"][field] = bad
            self.save()
            with self.assertRaises(ValueError):
                ark.execute(self.args("plan"))
            self.recipe["request"] = original
        self.save()
        (self.root / "design.png").write_bytes(b"different design")
        with self.assertRaises(ValueError):
            ark.execute(self.args("plan"))

    def test_missing_or_stale_plan_prevents_network(self):
        plan = ark.execute(self.args("plan"))["plan_sha256"]
        self.recipe["prompt"] += "增加轻微眨眼。"
        self.save()
        with patch.object(ark, "api_json", side_effect=AssertionError("network")):
            for value in (None, plan):
                with self.assertRaises(ValueError):
                    ark.execute(self.args("generate", value))

    def test_submit_once_and_never_store_key_or_base64(self):
        plan = ark.execute(self.args("plan"))["plan_sha256"]
        with patch.dict(os.environ, {"ARK_API_KEY": "offline-synthetic-secret"}), patch.object(ark, "api_json", return_value={"id": "task-test"}) as api:
            ark.execute(self.args("generate", plan))
            ark.execute(self.args("generate", plan))
            self.assertEqual(api.call_count, 1)
        state = (self.root / "runtime/ark/bear-test-v1/task.json").read_text()
        self.assertNotIn("offline-synthetic-secret", state)
        self.assertNotIn("base64,", state)

    def test_ambiguous_submit_stops_resubmission(self):
        plan = ark.execute(self.args("plan"))["plan_sha256"]
        with patch.dict(os.environ, {"ARK_API_KEY": "offline-test"}), patch.object(ark, "api_json", side_effect=ValueError("timeout")) as api:
            for _ in range(2):
                with self.assertRaises(ValueError):
                    ark.execute(self.args("generate", plan))
            self.assertEqual(api.call_count, 1)
        state = json.loads((self.root / "runtime/ark/bear-test-v1/task.json").read_text())
        self.assertEqual(state["status"], "submission_unknown")

    def test_download_has_identity_and_is_reusable(self):
        plan = ark.execute(self.args("plan"))["plan_sha256"]
        with patch.dict(os.environ, {"ARK_API_KEY": "offline-test"}), patch.object(ark, "api_json", return_value={"id": "task-test"}):
            ark.execute(self.args("generate", plan))
        response = {"id": "task-test", "status": "succeeded", "content": {"video_url": "https://example.invalid/test.mp4"}}
        def fake_download(url, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic test original")
        with patch.dict(os.environ, {"ARK_API_KEY": "offline-test"}), patch.object(ark, "api_json", return_value=response), patch.object(ark, "download_video", side_effect=fake_download) as download:
            result = ark.execute(self.args("download"))
            ark.execute(self.args("download"))
            self.assertEqual(download.call_count, 1)
        original = self.root / result["source"]
        sidecar = json.loads(original.with_suffix(".source.json").read_text())
        self.assertEqual(sidecar["source_sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
        self.assertEqual(sidecar["review"], "pending")


class FrameTests(unittest.TestCase):
    def test_alpha_preserves_dark_teal_and_displacement(self):
        image = np.full((80, 120, 3), (0, 255, 0), np.uint8)
        image[15:60, 25:65] = (190, 185, 20)
        image[20:30, 30:40] = (15, 20, 30)
        output = frames.remove_green(image, [0, 255, 0])
        self.assertEqual(output.shape, (80, 120, 4))
        self.assertEqual(output[0, 0, 3], 0)
        self.assertEqual(output[35, 40, 3], 255)
        np.testing.assert_array_equal(output[25, 35, :3], image[25, 35])

    def test_connected_paper_gaps_preserve_cream_fur_and_emblem(self):
        image = np.full((140, 180, 3), (85, 175, 145), np.uint8)
        cv2.rectangle(image, (55, 15), (125, 139), (30, 50, 80), -1)
        cv2.rectangle(image, (59, 19), (121, 139), (215, 240, 248), -1)
        image[45:65, 35:55] = (225, 240, 245)
        image[70:85, 80:95] = (85, 175, 145)
        output = frames.remove_green(image, [145, 175, 85], tolerance=10, softness=4,
                                     value_min=60, green_excess_min=0, connected_background=True,
                                     remove_light_border=True, keep_largest_component=True)
        self.assertEqual(output[55, 45, 3], 0)
        self.assertEqual(output[77, 87, 3], 255)
        self.assertEqual(output[139, 90, 3], 255)

    def test_real_ffmpeg_rgba_motion_atlas_and_safe_output(self):
        ffmpeg, env = frames.find_ffmpeg(os.environ.get("FFMPEG"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, pngs, output = root / "源视频", root / "fixture", root / "animations"
            source.mkdir()
            pngs.mkdir()
            for i in range(24):
                image = np.full((64, 96, 3), (0, 255, 0), np.uint8)
                image[18:38, 8 + i:28 + i] = (15, 20, 230)
                _, png = cv2.imencode(".png", image)
                (pngs / f"{i:03d}.png").write_bytes(png.tobytes())
            subprocess.run([ffmpeg, "-v", "error", "-framerate", "24", "-i", str(pngs / "%03d.png"),
                            "-frames:v", "24", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            str(source / "动作.MP4")], env=env, check=True)
            args = argparse.Namespace(input=source, output=output, config=None, ffmpeg=ffmpeg)
            manifest_path = frames.prepare(args)
            entry = json.loads(manifest_path.read_text())["animations"][0]
            self.assertEqual((entry["width"], entry["height"], entry["frame_count"]), (96, 64, 16))
            first, last = [cv2.imread(str(output / "动作" / f"frame_{i:06d}.png"), cv2.IMREAD_UNCHANGED) for i in (1, 16)]
            self.assertEqual(first.shape, (64, 96, 4))
            self.assertGreater(np.nonzero(last[:, :, 3] > 200)[1].mean() - np.nonzero(first[:, :, 3] > 200)[1].mean(), 18)
            with self.assertRaises(ValueError):
                frames.prepare(args)
            packed = atlas.pack(manifest_path, root / "atlases", 96, 4)
            spec = json.loads(packed.read_text())["actions"][0]
            self.assertEqual(spec["canvas_transform"], {"scale": 1.0, "offset": [0.0, 16.0]})
            self.assertEqual(len(spec["frames_sha256"]), 16)
            grid = cv2.imread(str(packed.parent / spec["atlas"]), cv2.IMREAD_UNCHANGED)
            self.assertEqual(grid.shape, (384, 384, 4))
            self.assertEqual(grid[0, 0, 3], 0)
            self.assertEqual(grid[45, 20, 3], 255)
            with self.assertRaises(ValueError):
                atlas.pack(manifest_path, root / "atlases", 96, 4)

    def test_batch_failure_publishes_no_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "broken.mp4").write_bytes(b"not a video")
            args = argparse.Namespace(input=source, output=root / "out", config=None, ffmpeg=os.environ.get("FFMPEG"))
            with self.assertRaises(ValueError):
                frames.prepare(args)
            self.assertFalse(args.output.exists())


class GodotTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("GODOT"), "Set GODOT to run real engine playback checks")
    def test_player_modes_and_fixed_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Skill offline test"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
            shutil.copyfile(HERE / "sprite_character.gd", root / "sprite_character.gd")
            entries = []
            for name, mode in (("idle", "loop"), ("wave", "return_idle"), ("point", "hold")):
                folder = root / name
                folder.mkdir()
                for index in (1, 2):
                    _, png = cv2.imencode(".png", np.full((16, 16, 4), 255, np.uint8))
                    (folder / f"frame_{index:06d}.png").write_bytes(png.tobytes())
                entries.append({"name": name, "directory": name, "frame_count": 2, "width": 16, "height": 16,
                                "playback": mode, "layout": {"scale": 2, "anchor": [8, 15]}})
            (root / "animations.json").write_text(json.dumps({"fps": 16, "animations": entries}))
            (root / "test.gd").write_text('''extends SceneTree
func _initialize():
    call_deferred("check")
func require(value, message):
    if not value:
        push_error(message)
        quit(1)
        return false
    return true
func check():
    var player = load("res://sprite_character.gd").new()
    player.manifest_path = "res://animations.json"
    root.add_child(player)
    if not require(player.sprite.is_playing(), "Autoplay must start"): return
    if not require(player.sprite.position == Vector2(-16, -30), "Fixed anchor"): return
    if not require(not player.play_action("unknown"), "Reject unknown action"): return
    player.play_action("wave")
    await create_timer(0.35).timeout
    if not require(player.sprite.animation == &"idle" and player.sprite.is_playing(), "Return to idle"): return
    player.play_action("point")
    await create_timer(0.35).timeout
    if not require(player.sprite.frame == 1 and not player.sprite.is_playing(), "Hold final frame"): return
    player.play_action("point")
    if not require(player.sprite.frame == 1, "Same held action must not reset"): return
    var other = load("res://sprite_character.gd").new()
    other.manifest_path = "res://animations.json"
    root.add_child(other)
    if not require(other.sprite.is_playing() and not player.sprite.is_playing(), "Independent instances"): return
    print("GODOT_SKILL_TEST_OK")
    quit(0)
''')
            result = subprocess.run([os.environ["GODOT"], "--headless", "--path", str(root), "--script", "res://test.gd", "--log-file", str(root / "godot.log")], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("GODOT_SKILL_TEST_OK", result.stdout)


class VoiceTests(unittest.TestCase):
    def test_missing_local_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                voice.validate_model(Path(temp) / "absent")

    def test_existing_voice_output_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for filename in ("reference.wav", "reference.txt", "text.txt", "existing.wav"):
                (root / filename).write_text("existing input")
            args = argparse.Namespace(model_dir=root, reference_audio=root / "reference.wav",
                                      reference_text_file=root / "reference.txt", text_file=root / "text.txt",
                                      output=root / "existing.wav")
            with patch.object(voice, "validate_model", return_value=root), self.assertRaises(ValueError):
                voice.generate(args)
            self.assertEqual(args.output.read_text(), "existing input")


if __name__ == "__main__":
    unittest.main(verbosity=2)
