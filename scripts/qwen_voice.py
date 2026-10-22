#!/usr/bin/env python3
"""Generate one local Qwen3-TTS Base voice-cloning clip for the episode pipeline."""

import argparse
import contextlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import wave


def existing_file(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError(f"Missing or empty input file: {resolved}")
    return resolved


def read_text(path: Path) -> str:
    value = existing_file(path).read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Input text is empty: {path}")
    return value


def validate_model(path: Path) -> Path:
    model_dir = path.expanduser().resolve(strict=True)
    if not model_dir.is_dir():
        raise ValueError(f"Model must be an existing local directory: {model_dir}")
    config = json.loads(existing_file(model_dir / "config.json").read_text("utf-8"))
    if config.get("tts_model_type") != "base":
        raise ValueError("Voice cloning requires a Qwen3-TTS Base model")
    if not any(model_dir.glob("*.safetensors")):
        raise ValueError(f"Model weights are missing: {model_dir}")
    existing_file(model_dir / "tokenizer_config.json")
    if (model_dir / "tokenizer.json").exists():
        existing_file(model_dir / "tokenizer.json")
    else:
        # The pinned Qwen2 tokenizer uses vocab + merges instead of a fast JSON.
        existing_file(model_dir / "vocab.json")
        existing_file(model_dir / "merges.txt")
    tokenizer_dir = model_dir / "speech_tokenizer"
    existing_file(tokenizer_dir / "config.json")
    if not any(tokenizer_dir.glob("*.safetensors")):
        raise ValueError(f"Speech tokenizer weights are missing: {tokenizer_dir}")
    return model_dir


def generate(args: argparse.Namespace) -> None:
    model_dir = validate_model(args.model_dir)
    reference_audio = existing_file(args.reference_audio)
    reference_text = read_text(args.reference_text_file)
    text = read_text(args.text_file)
    output = args.output.expanduser().resolve()
    if output.exists():
        raise ValueError("Output already exists; use a new clip version")
    if output in {
        reference_audio,
        args.reference_text_file.expanduser().resolve(),
        args.text_file.expanduser().resolve(),
    }:
        raise ValueError("Output must not overwrite a reference or text input")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("This local MLX provider requires an Apple Silicon Mac")

    # Local inference must never trigger a model or tokenizer download.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    # MLX Audio's model loader prints diagnostics to stdout; keep the bridge's
    # stdout empty so every model message is captured as stderr by the caller.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            import mlx.core as mx
            import numpy as np
            from mlx_audio.tts.utils import load_model
        except ImportError as error:
            raise RuntimeError(
                "Local voice dependencies are missing. Use your prepared Python 3.12 "
                "environment with compatible mlx-audio dependencies."
            ) from error

        mx.random.seed(42)
        print(f"Loading local Qwen3-TTS Base model: {model_dir}", file=sys.stderr)
        model = load_model(model_dir)
        if getattr(model, "model_type", None) != "qwen3_tts":
            raise ValueError("The local model is not Qwen3-TTS")
        if model.tokenizer is None or model.speech_tokenizer is None:
            raise ValueError("Local text or speech tokenizer failed to load")
        if not model.speech_tokenizer.has_encoder:
            raise ValueError("Base cloning requires a speech tokenizer encoder")

        audio_parts = []
        sample_rate = int(model.sample_rate)
        for result in model.generate(
            text=text,
            ref_audio=str(reference_audio),
            ref_text=reference_text,
            lang_code="English",
            max_tokens=1024,
            verbose=False,
            stream=False,
        ):
            if int(result.sample_rate) != sample_rate:
                raise ValueError("Generated segments have inconsistent sample rates")
            mx.eval(result.audio)
            samples = np.asarray(result.audio, dtype=np.float32).squeeze()
            if samples.ndim != 1 or samples.size == 0:
                raise ValueError("The model returned an empty or non-mono waveform")
            if not np.isfinite(samples).all():
                raise ValueError("The model returned invalid audio samples")
            audio_parts.append(samples)

        if not audio_parts:
            raise ValueError("The model produced no audio")
        audio = np.concatenate(audio_parts)
        if np.max(np.abs(audio)) == 0:
            raise ValueError("The model produced only silence")
        pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype("<i2")

        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
            with wave.open(str(temporary_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(pcm.tobytes())
            os.replace(temporary_path, output)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        print(
            f"Saved {audio.size / sample_rate:.3f}s mono PCM16 WAV: {output}",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reference-audio", type=Path, required=True)
    parser.add_argument("--reference-text-file", type=Path, required=True)
    parser.add_argument("--text-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(args)
    except Exception as error:
        print(f"qwen-voice: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
