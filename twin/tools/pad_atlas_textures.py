"""Grow every UV island of the atlas textures into the unused gutters.

The Poly Pizza models pack their colour islands tightly on a black
background. Mipmaps average that black into the islands, so at normal viewing
distances whole buildings turn dark and island edges show black seams. Using
the UV triangles exported by tools/export_atlas_uvs.gd, pixels covered by an
island are kept exactly; uncovered pixels take the colour of the nearest
island (grown one pixel per pass), which is invisible up close and keeps the
mip levels clean.

Run: python3 tools/pad_atlas_textures.py   (after the Godot export)
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

PROJECT = Path(__file__).resolve().parents[1]
PASSES = 32  # covers mip levels up to 32 x 32 px blocks
# Below this share of painted pixels inside UV islands the texture is a
# palette sampled at points (UV orientation cannot be verified): left as is.
MIN_COVERAGE = 0.6


def island_mask(size: tuple[int, int], triangles: list, flip_v: bool) -> Image.Image:
    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for u0, v0, u1, v1, u2, v2 in triangles:
        points = []
        for u, v in ((u0, v0), (u1, v1), (u2, v2)):
            points.append((u * width, ((1.0 - v) if flip_v else v) * height))
        draw.polygon(points, fill=255)
    # Include the anti-aliased rim of each island.
    return mask.filter(ImageFilter.MaxFilter(3))


def painted(image: Image.Image) -> Image.Image:
    grey = image.convert("L")
    return grey.point(lambda value: 255 if value > 12 else 0)


def coverage(mask: Image.Image, paint: Image.Image) -> float:
    covered = ImageChops.multiply(mask, paint).histogram()[255]
    total = paint.histogram()[255]
    return covered / max(total, 1)


def pad(path: Path, triangles: list) -> None:
    image = Image.open(path)
    mode = image.mode
    image = image.convert("RGBA")
    paint = painted(image)
    mask = island_mask(image.size, triangles, False)
    flipped = island_mask(image.size, triangles, True)
    if coverage(flipped, paint) > coverage(mask, paint):
        mask = flipped
    share = coverage(mask, paint)
    print(f"{path.name}: island coverage of painted pixels {share:.3f}")
    if share < MIN_COVERAGE:
        return
    for _ in range(PASSES):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted_image = ImageChops.offset(image, dx, dy)
            shifted_mask = ImageChops.offset(mask, dx, dy)
            fill = ImageChops.subtract(shifted_mask, mask)
            image.paste(shifted_image, (0, 0), fill)
            mask = ImageChops.lighter(mask, fill)
    if mode != "RGBA":
        image = image.convert(mode)
    image.save(path)


def main() -> None:
    islands = json.loads((PROJECT / "tools" / "atlas_uv_islands.json").read_text())
    for texture, triangles in islands.items():
        pad(PROJECT / texture.replace("res://", ""), triangles)


if __name__ == "__main__":
    main()
