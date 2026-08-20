"""Generate cropped, alpha-preserving Tara PNG derivatives with Pillow/LANCZOS."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def build(source: Path, destination: Path, width: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as original:
        image = original.convert("RGBA")
        bbox = image.getchannel("A").getbbox()
        if bbox is None:
            raise ValueError("logo has no alpha-visible pixels")
        cropped = image.crop(bbox)
        height = round(cropped.height * width / cropped.width)
        cropped.resize((width, height), Image.Resampling.LANCZOS).save(
            destination, "PNG", optimize=True
        )
    with Image.open(destination) as result:
        if result.mode != "RGBA" or result.getchannel("A").getextrema()[0] != 0:
            raise ValueError(f"derivative lost alpha: {destination}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir", type=Path, default=Path("webinterface/logo-final")
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("webinterface/frontend/src/assets/brand"),
    )
    arguments = parser.parse_args()
    for variant in ("black", "white"):
        for suffix, width in (("", 132), ("@2x", 264)):
            build(
                arguments.source_dir / f"tara-logo-{variant}-transparent.png",
                arguments.output_dir / f"tara-logo-{variant}{suffix}.png",
                width,
            )


if __name__ == "__main__":
    main()
