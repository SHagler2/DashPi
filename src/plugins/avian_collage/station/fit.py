#!/usr/bin/env python3
"""Crop the empty paper out of a shoot.py capture and scale the collage to fill the screen.

The collage page leaves its bird cluster small in the middle of the viewport. This
splits the capture into bands of non-background rows, treats the tallest band as
the bird cluster and the bands above it as the title, keeps the title at its
rendered size, and scales the cluster up to fill the rest. Render at --dsf 2 so
the zoom stays sharp.

    fit.py <in.png> <out.png> <width> <height>
"""
import sys
from PIL import Image, ImageChops

THRESHOLD = 10        # per-channel difference from the paper that counts as content
MIN_GAP = 0.02        # empty band (fraction of height) that separates title from birds
MARGIN = 0.025        # outer margin, fraction of the output height


def content_rows(mask):
    """Return [(top, bottom)] bands of rows that contain any content pixel."""
    width, height = mask.size
    data = mask.load()
    rows = [any(data[x, y] for x in range(0, width, 2)) for y in range(height)]
    bands, start = [], None
    for y, filled in enumerate(rows + [False]):
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            bands.append([start, y])
            start = None
    return bands


def merge(bands, gap):
    merged = []
    for band in bands:
        if merged and band[0] - merged[-1][1] < gap:
            merged[-1][1] = band[1]
        else:
            merged.append(band)
    return merged


def fit(src_path, out_path, out_w, out_h):
    src = Image.open(src_path).convert("RGB")
    paper = src.getpixel((2, 2))
    diff = ImageChops.difference(src, Image.new("RGB", src.size, paper)).convert("L")
    mask = diff.point(lambda v: 255 if v > THRESHOLD else 0)

    bands = merge(content_rows(mask), int(src.height * MIN_GAP))
    out = Image.new("RGB", (out_w, out_h), paper)
    margin = int(out_h * MARGIN)
    if not bands:
        src.resize((out_w, out_h), Image.LANCZOS).save(out_path)
        return

    # The bird cluster is the tallest band; everything above it (eyebrow + headline) is the title.
    birds_at = max(range(len(bands)), key=lambda i: bands[i][1] - bands[i][0])
    title_band = [bands[0][0], bands[birds_at - 1][1]] if birds_at else None
    bird_bands = bands[birds_at:]
    scale_back = out_w / src.width  # undo --dsf for the title

    y = margin
    if title_band:
        box = mask.crop((0, title_band[0], src.width, title_band[1])).getbbox()
        title = src.crop((box[0], title_band[0], box[2], title_band[1]))
        title = title.resize((max(1, int(title.width * scale_back)), max(1, int(title.height * scale_back))), Image.LANCZOS)
        out.paste(title, ((out_w - title.width) // 2, y))
        y += title.height + margin

    top, bottom = bird_bands[0][0], bird_bands[-1][1]
    box = mask.crop((0, top, src.width, bottom)).getbbox()
    birds = src.crop((box[0], top + box[1], box[2], top + box[3]))
    avail_w, avail_h = out_w - 2 * margin, out_h - y - margin
    scale = min(avail_w / birds.width, avail_h / birds.height)
    birds = birds.resize((max(1, int(birds.width * scale)), max(1, int(birds.height * scale))), Image.LANCZOS)
    out.paste(birds, ((out_w - birds.width) // 2, y + (avail_h - birds.height) // 2))
    out.save(out_path)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    fit(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
