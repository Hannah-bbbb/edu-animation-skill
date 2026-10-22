#!/usr/bin/env python3
"""Pack full-canvas RGBA frames into atlases without character recentering."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

import cv2
import numpy as np


def pack(manifest_path, output, cell=512, columns=8):
    manifest_path, output = manifest_path.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Output must be a new directory")
    if not 16 <= cell <= 2048 or not 1 <= columns <= 16:
        raise ValueError("Invalid cell size or columns")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("fps") != 16 or not isinstance(manifest.get("animations"), list) or not manifest["animations"]:
        raise ValueError("Need a 16 FPS animations.json manifest")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".atlas-stage-", dir=output.parent) as tmp:
        stage = Path(tmp) / "atlases"
        stage.mkdir()
        entries, names = [], set()
        for entry in manifest["animations"]:
            name = entry["name"]
            if not isinstance(name, str) or not name or name in names or any(c in name for c in "/\\") or name in (".", ".."):
                raise ValueError("Invalid or duplicate action name")
            names.add(name)
            count, width, height = (entry[k] for k in ("frame_count", "width", "height"))
            if any(type(x) is not int or x <= 0 for x in (count, width, height)):
                raise ValueError("Frame dimensions/count must be positive integers")
            rows = math.ceil(count / columns)
            if columns * cell > 8192 or rows * cell > 8192:
                raise ValueError("Atlas exceeds 8192 pixels; use smaller cells or split the action")
            folder = (manifest_path.parent / entry["directory"]).resolve()
            if not folder.is_relative_to(manifest_path.parent):
                raise ValueError("Frame directory escapes manifest directory")
            factor = min(cell / width, cell / height)
            # Use a single isotropic affine scale; no independently rounded x/y scale.
            dx, dy = (cell - width * factor) / 2, (cell - height * factor) / 2
            transform = np.array([[factor, 0, dx], [0, factor, dy]], np.float32)
            atlas = np.zeros((rows * cell, columns * cell, 4), np.uint8)
            hashes = []
            for index in range(count):
                path = folder / f"frame_{index + 1:06d}.png"
                data = path.read_bytes()
                image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
                if image is None or image.shape != (height, width, 4):
                    raise ValueError(f"Unexpected frame shape: {path}")
                hashes.append(hashlib.sha256(data).hexdigest())
                image = image.astype(np.float32) / 255
                image[:, :, :3] *= image[:, :, 3:]
                tile = cv2.warpAffine(image, transform, (cell, cell), flags=cv2.INTER_LINEAR,
                                      borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
                tile[:, :, :3] /= np.maximum(tile[:, :, 3:], 1 / 65536)
                tile = np.clip(np.rint(tile * 255), 0, 255).astype(np.uint8)
                tile[tile[:, :, 3] == 0, :3] = 0
                row, col = divmod(index, columns)
                atlas[row * cell:(row + 1) * cell, col * cell:(col + 1) * cell] = tile
            ok, encoded = cv2.imencode(".png", atlas)
            if not ok:
                raise ValueError("PNG encoding failed")
            filename = name + ".png"
            (stage / filename).write_bytes(encoded.tobytes())
            entries.append({**entry, "atlas": filename, "cell_size": cell, "columns": columns,
                            "canvas_transform": {"scale": factor, "offset": [dx, dy]},
                            "frames_sha256": hashes, "atlas_sha256": hashlib.sha256(encoded.tobytes()).hexdigest()})
        (stage / "atlases.json").write_text(json.dumps({"fps": manifest["fps"], "actions": entries}, ensure_ascii=False, indent=2) + "\n")
        if output.exists():
            raise ValueError("Output appeared during packing")
        stage.rename(output)
    return output / "atlases.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cell-size", type=int, default=512)
    parser.add_argument("--columns", type=int, default=8)
    args = parser.parse_args()
    try:
        print(pack(args.manifest, args.output, args.cell_size, args.columns))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
