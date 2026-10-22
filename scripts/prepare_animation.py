#!/usr/bin/env python3
"""Extract full-canvas PNGs with FFmpeg, then remove green with OpenCV."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np


def remove_green(bgr, green_rgb, tolerance=18.0, softness=5.0, despill=0.8, edge_shrink=1, value_min=100,
                 remove_light_border=False, keep_largest_component=False, green_excess_min=20,
                 connected_background=False):
    """HSV hue key; no cropping, resizing, recentering, or temporal filtering.

    Hue distances use OpenCV's 0..179 scale (one unit = two degrees).
    Low saturation/value pixels are kept, preserving black outlines and whites.
    Despill affects only a narrow transparent boundary, never the whole shirt.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    target = cv2.cvtColor(np.uint8([[green_rgb[::-1]]]), cv2.COLOR_BGR2HSV)[0, 0, 0]
    distance = np.abs(hsv[:, :, 0] - float(target))
    distance = np.minimum(distance, 180.0 - distance)
    opacity = np.clip((distance - tolerance) / softness, 0.0, 1.0)
    colors = bgr.astype(np.float32)
    excess = np.maximum(colors[:, :, 1] - np.maximum(colors[:, :, 0], colors[:, :, 2]), 0)
    # Compressed dark teal strokes can share the screen hue; they are not
    # strongly green-dominant. Preserve them rather than punching shirt holes.
    opacity[(hsv[:, :, 1] < 40) | (hsv[:, :, 2] < value_min) | (excess < green_excess_min)] = 1.0
    if connected_background:
        # Brush-painted backgrounds can have cream holes. Flood through already
        # keyed green AND light paper from the canvas edge, stopping at the brown
        # character outline. Enclosed fur, highlights and green shirt emblems stay.
        light = (hsv[:, :, 1] < 45) & (hsv[:, :, 2] > 200) if remove_light_border else False
        candidates = ((opacity < 0.5) | light).astype(np.uint8)
        _, labels = cv2.connectedComponents(candidates, connectivity=8)
        # Seed paper only at background corners, plus already-keyed green edge
        # pixels. Cream legs/ears cropped by the source edge are NOT paper seeds.
        edge = np.zeros_like(candidates, dtype=bool)
        edge[0] = edge[-1] = True
        edge[:, 0] = edge[:, -1] = True
        edge_labels = np.unique(np.concatenate((labels[edge & (opacity < 0.5)],
                                                labels[[0, 0, -1, -1], [0, -1, 0, -1]])))
        outside = np.isin(labels, edge_labels[edge_labels != 0])
        opacity = (~outside).astype(np.float32)
    elif remove_light_border:
        # Only remove near-white regions connected to the canvas edge. The rabbit's
        # cream fur and enclosed eye highlights must not be keyed as white screen.
        light = ((hsv[:, :, 1] < 24) & (hsv[:, :, 2] > 225)).astype(np.uint8)
        _, labels = cv2.connectedComponents(light, connectivity=8)
        edge_labels = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
        edge_labels = edge_labels[edge_labels != 0]
        opacity[np.isin(labels, edge_labels)] = 0.0
    kernel = np.ones((3, 3), np.uint8)
    opacity = cv2.morphologyEx(opacity, cv2.MORPH_CLOSE, kernel)
    opacity = cv2.morphologyEx(opacity, cv2.MORPH_OPEN, kernel)
    if keep_largest_component:
        # Opt-in cleanup for isolated video labels / background debris. Retain a
        # narrow margin around the connected character for its antialiased edge.
        count, labels, stats, _ = cv2.connectedComponentsWithStats((opacity > 0.5).astype(np.uint8), connectivity=8)
        if count > 1:
            main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            keep = cv2.dilate((labels == main).astype(np.uint8), kernel)
            opacity *= keep
    # A one-pixel matte choke removes the green fringe and tiny compression flecks.
    # This changes only alpha; source coordinates and the full canvas stay fixed.
    if edge_shrink:
        opacity = cv2.erode(opacity, kernel, iterations=edge_shrink)
    # A tiny spatial feather softens compression edges without moving the canvas.
    opacity = cv2.GaussianBlur(opacity, (3, 3), 0.5)
    opacity[opacity < 0.01] = 0.0
    opacity[opacity > 0.99] = 1.0
    boundary = cv2.dilate(1.0 - opacity, np.ones((3, 3), np.uint8))
    colors[:, :, 1] -= excess * boundary * despill
    alpha = np.rint(opacity * 255).astype(np.uint8)
    bgra = np.dstack((np.rint(colors).astype(np.uint8), alpha))
    bgra[alpha == 0, :3] = 0
    return bgra


def find_ffmpeg(explicit=None):
    if explicit:
        executable = shutil.which(explicit) or str(Path(explicit).expanduser().resolve())
    else:
        executable = shutil.which("ffmpeg")
    if not executable or not Path(executable).is_file():
        raise ValueError("找不到 FFmpeg；请安装 FFmpeg，或用 --ffmpeg 指定可执行文件。")
    env = os.environ.copy()
    lib_dir = str(Path(executable).parent)
    if sys.platform == "darwin" and (Path(lib_dir) / "libavcodec.dylib").exists():
        env["DYLD_LIBRARY_PATH"] = lib_dir + os.pathsep + env.get("DYLD_LIBRARY_PATH", "")
    return executable, env


def video_duration(video, ffmpeg, env):
    ffprobe = Path(ffmpeg).with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    executable = str(ffprobe) if ffprobe.is_file() else shutil.which("ffprobe")
    if not executable:
        raise ValueError("找不到 ffprobe；请提供包含 ffmpeg 和 ffprobe 的 FFmpeg 安装目录。")
    result = subprocess.run([executable, "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=duration:format=duration", "-of", "json", str(video)],
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"无法读取视频时长 {video.name}：{result.stderr.strip()}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    value = streams[0].get("duration") if streams else None
    if value in (None, "N/A"):
        value = data.get("format", {}).get("duration")
    duration = float(value or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"视频缺少有效的时长：{video}")
    return duration


def process_video(video, output, name, settings, ffmpeg, env):
    target = output / video.stem
    if target.exists():
        raise ValueError(f"Refusing to overwrite {target}")
    source_sha256 = hashlib.sha256(video.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix=f".{video.stem}-", dir=output) as temp:
        stage = Path(temp) / "frames"
        stage.mkdir()
        duration = video_duration(video, ffmpeg, env)
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video),
                   "-map", "0:v:0", "-t", str(duration), "-r", str(settings["fps"]),
                   "-fps_mode", "cfr", "-an", "-c:v", "png",
                   "-start_number", "1", str(stage / "frame_%06d.png")]
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"FFmpeg 无法处理 {video.name}：{result.stderr.strip()}")
        files = sorted(stage.glob("frame_*.png"))
        if not files:
            raise ValueError(f"没有提取到图片帧：{video}")
        size = None
        for frame in files:
            # imdecode/encode plus pathlib work reliably with Chinese paths.
            image = cv2.imdecode(np.frombuffer(frame.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"图片无法解码：{frame}")
            height, width = image.shape[:2]
            if size and size != (width, height):
                raise ValueError(f"视频中途改变画布大小：{video}")
            size = (width, height)
            image = remove_green(image, settings["green_rgb"], settings["tolerance"],
                                 settings["softness"], settings["despill"], settings["edge_shrink"], settings["value_min"],
                                 settings["remove_light_border"], settings["keep_largest_component"], settings["green_excess_min"],
                                 settings["connected_background"])
            ok, png = cv2.imencode(".png", image)
            if not ok:
                raise ValueError(f"透明 PNG 编码失败：{frame}")
            frame.write_bytes(png.tobytes())
        spec = {"name": name, "directory": video.stem, "frame_count": len(files),
                "width": size[0], "height": size[1]}
        if hashlib.sha256(video.read_bytes()).hexdigest() != source_sha256:
            raise ValueError(f"Source changed during extraction: {video}")
        metadata = {**spec, "generator": "edu-animation-skill/prepare_animation", "source": str(video), "source_sha256": source_sha256, **settings}
        (stage / "animation.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        stage.rename(target)
    return spec


def validate_settings(settings):
    for key in ("fps", "tolerance", "softness", "despill"):
        if type(settings[key]) not in (int, float) or not math.isfinite(settings[key]):
            raise ValueError(key + " must be finite numeric data")
    if not 1 <= settings["fps"] <= 120:
        raise ValueError("fps 必须在 1–120 之间")
    if not 0 <= settings["tolerance"] <= 60 or not 0 < settings["softness"] <= 30:
        raise ValueError("tolerance 必须在 0–60 之间，softness 必须大于 0 且不超过 30")
    if not 0 <= settings["despill"] <= 1:
        raise ValueError("despill 必须在 0–1 之间")
    if type(settings["edge_shrink"]) is not int or not 0 <= settings["edge_shrink"] <= 3:
        raise ValueError("edge_shrink 必须是 0–3 的整数；0 表示不收缩透明边缘")
    if type(settings["value_min"]) is not int or not 0 <= settings["value_min"] <= 255:
        raise ValueError("value_min 必须是 0–255 的整数")
    if type(settings["green_excess_min"]) is not int or not 0 <= settings["green_excess_min"] <= 255:
        raise ValueError("green_excess_min 必须是 0–255 的整数")
    for key in ("remove_light_border", "keep_largest_component", "connected_background"):
        if type(settings[key]) is not bool:
            raise ValueError(f"{key} 必须是 true 或 false")
    rgb = settings["green_rgb"]
    if not isinstance(rgb, list) or len(rgb) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in rgb):
        raise ValueError("green_rgb 必须是三个 0–255 的整数")


DEFAULTS = {"fps": 16, "green_rgb": [0, 255, 0], "tolerance": 18, "softness": 5,
            "despill": 0.8, "edge_shrink": 1, "value_min": 100, "green_excess_min": 20,
            "connected_background": False, "remove_light_border": False, "keep_largest_component": False}
MODES = {"loop", "return_idle", "hold"}


def prepare(args):
    source, output = args.input.expanduser().resolve(), args.output.expanduser().resolve()
    if not source.is_dir() or output.exists():
        raise ValueError("Input must be a directory; output must be a NEW directory")
    config = json.loads(args.config.read_text()) if args.config else {}
    if not isinstance(config, dict) or set(config) - (set(DEFAULTS) | {"actions", "overrides", "playback", "layouts", "closed_mouth"}):
        raise ValueError("Unknown configuration fields")
    for key in ("actions", "overrides", "playback", "layouts", "closed_mouth"):
        if not isinstance(config.get(key, {}), dict):
            raise ValueError(key + " must be an object")
    settings = {**DEFAULTS, **{k: config[k] for k in DEFAULTS if k in config}}
    if settings["fps"] != 16:
        raise ValueError("This pipeline samples at 16 FPS")
    validate_settings(settings)
    videos = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() == ".mp4")
    if not videos or len({v.stem.casefold() for v in videos}) != len(videos):
        raise ValueError("Need uniquely named MP4 files")
    planned, names = [], set()
    for video in videos:
        name = config.get("actions", {}).get(video.stem, video.stem)
        if not isinstance(name, str) or not re.fullmatch(r"[\w-]+", name) or name in names:
            raise ValueError("Action names must be unique letters/digits/underscore/hyphen")
        names.add(name)
        overrides = config.get("overrides", {}).get(video.stem, {})
        if not isinstance(overrides, dict) or set(overrides) - (set(DEFAULTS) - {"fps"}):
            raise ValueError("Overrides may contain only keying parameters")
        current = {**settings, **overrides}
        validate_settings(current)
        mode = config.get("playback", {}).get(name, "hold")
        if mode not in MODES:
            raise ValueError("Unknown playback mode: " + str(mode))
        layout = config.get("layouts", {}).get(name)
        if layout is not None:
            if not isinstance(layout, dict) or set(layout) != {"scale", "anchor"}:
                raise ValueError("layout requires scale and anchor")
            values = [layout["scale"], *layout["anchor"]] if isinstance(layout["anchor"], list) else []
            if len(values) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in values) or values[0] <= 0:
                raise ValueError("Invalid fixed scale/anchor")
        closed = config.get("closed_mouth", {}).get(name, False)
        if type(closed) is not bool:
            raise ValueError("closed_mouth must be explicitly true/false")
        planned.append((video, name, current, mode, layout, closed))
    ffmpeg, env = find_ffmpeg(args.ffmpeg)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".animation-stage-", dir=output.parent) as temp:
        stage = Path(temp) / "batch"
        stage.mkdir()
        animations = []
        for video, name, current, mode, layout, closed in planned:
            spec = process_video(video, stage, name, current, ffmpeg, env)
            marker = stage / video.stem / "animation.json"
            metadata = json.loads(marker.read_text())
            spec.update(playback=mode, layout=layout or {"scale": 1.0, "anchor": [spec["width"] / 2, spec["height"]]},
                        closed_mouth=closed, source_sha256=metadata["source_sha256"], review="pending")
            metadata.update(spec)
            metadata["frames_sha256"] = [hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((stage/video.stem).glob("frame_*.png"))]
            marker.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
            animations.append(spec)
            print(f"{name}: {spec['frame_count']} frames, {spec['width']}x{spec['height']}", flush=True)
        (stage / "animations.json").write_text(json.dumps({"fps": 16, "animations": animations}, ensure_ascii=False, indent=2) + "\n")
        if output.exists():
            raise ValueError("Output appeared during extraction; refusing to replace it")
        stage.rename(output)
    return output / "animations.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--ffmpeg")
    args = parser.parse_args()
    try:
        print(prepare(args))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
