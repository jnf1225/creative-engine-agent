#!/usr/bin/env python3
"""
overlay.py - Stamp text ideas from a CSV onto your own photos.

Reads the same kind of CSV as generate.py (columns: client, concept, angle,
prompt), but instead of generating AI images it takes photos from a folder,
pairs each CSV row with a RANDOM photo, and writes the text onto it at three
sizes (1080x1080, 1080x1350, 1080x1920).

    photos/  <- put your photos here (jpg or png)
    output-photos/<client>/<client>_<concept>_<angle>_<size>.png

Usage:
    python3 overlay.py batch.csv
    python3 overlay.py batch.csv --photos my-shoot-folder
    python3 overlay.py batch.csv --text-column angle

Free and instant - nothing here talks to the internet or costs money.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    sys.exit(
        "\nERROR: the Python package 'Pillow' is not installed.\n"
        "Run this in your terminal:\n\n    pip3 install pillow\n"
    )

SCRIPT_DIR = Path(__file__).resolve().parent
SIZES = ((1080, 1080), (1080, 1350), (1080, 1920))
PHOTO_TYPES = (".jpg", ".jpeg", ".png", ".webp")
REQUIRED_COLUMNS = ("client", "concept", "angle")

# Bold fonts to try, in order. Mac paths first, then Linux, then Pillow's
# built-in as a last resort.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip())
    return cleaned.strip("-") or "untitled"


def find_font_path() -> str | None:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


FONT_PATH = find_font_path()


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if FONT_PATH:
        return ImageFont.truetype(FONT_PATH, size=size)
    return ImageFont.load_default(size=size)


def read_rows(csv_path: Path, text_column: str) -> list[dict]:
    if not csv_path.exists():
        sys.exit(f"\nERROR: could not find the CSV file '{csv_path}'.")

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        actual = {
            name.strip().lower(): name
            for name in (reader.fieldnames or [])
            if name is not None
        }
        needed = list(REQUIRED_COLUMNS) + [text_column]
        missing = [c for c in needed if c not in actual]
        if missing:
            sys.exit(
                f"\nERROR: '{csv_path.name}' is missing the column(s): "
                f"{', '.join(missing)}\nColumns found: {', '.join(actual) or 'none'}"
            )

        rows = []
        for number, raw in enumerate(reader, start=2):
            row = {key: (raw.get(actual[key]) or "").strip() for key in needed}
            row["row_number"] = number
            rows.append(row)
        return rows


def list_photos(folder: Path) -> list[Path]:
    if not folder.exists():
        sys.exit(
            f"\nERROR: could not find the photo folder '{folder}'.\n"
            f"Create a folder named '{folder.name}' next to this script and "
            f"put your photos in it (jpg or png)."
        )
    photos = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in PHOTO_TYPES
    )
    heic = [p.name for p in folder.iterdir() if p.suffix.lower() in (".heic", ".heif")]
    if heic:
        print(
            f"NOTE: skipping {len(heic)} .heic file(s) (iPhone format). "
            f"Export them as JPEG to use them.",
        )
    if not photos:
        sys.exit(f"\nERROR: no usable photos (jpg/png) found in '{folder}'.")
    return photos


class PhotoPicker:
    """Hands out photos randomly, but spreads them evenly: every photo gets
    used once (in shuffled order) before any photo repeats."""

    def __init__(self, photos: list[Path], seed: int | None):
        self.photos = list(photos)
        self.random = random.Random(seed)
        self.deck: list[Path] = []

    def next(self) -> Path:
        if not self.deck:
            self.deck = self.photos[:]
            self.random.shuffle(self.deck)
        return self.deck.pop()


def crop_to_size(photo: Image.Image, width: int, height: int) -> Image.Image:
    """Center-crop to the target shape, then scale to exact pixels."""
    return ImageOps.fit(photo, (width, height), Image.LANCZOS, centering=(0.5, 0.45))


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_caption(image: Image.Image, text: str) -> Image.Image:
    """Dark gradient at the bottom + bold white wrapped text over it."""
    image = image.convert("RGB")
    w, h = image.size
    draw = ImageDraw.Draw(image)

    # Find the biggest font size whose wrapped text fits the text box.
    box_width = int(w * 0.84)
    box_height = int(h * 0.30)
    font_size = max(int(w / 9), 28)
    while font_size > 22:
        font = load_font(font_size)
        lines = wrap_text(draw, text, font, box_width)
        line_height = int(font_size * 1.18)
        if len(lines) * line_height <= box_height and all(
            draw.textlength(ln, font=font) <= box_width for ln in lines
        ):
            break
        font_size -= 4
    else:
        font = load_font(font_size)
        lines = wrap_text(draw, text, font, box_width)
        line_height = int(font_size * 1.18)

    text_block = len(lines) * line_height
    bottom_margin = int(h * 0.07)
    top_of_text = h - bottom_margin - text_block

    # Gradient scrim: transparent at its top, dark at the bottom edge.
    scrim = Image.new("L", (1, h), 0)
    scrim_start = max(0, top_of_text - int(h * 0.12))
    for y in range(scrim_start, h):
        reach = (y - scrim_start) / max(1, h - scrim_start)
        scrim.putpixel((0, y), int(185 * reach))
    scrim = scrim.resize((w, h))
    black = Image.new("RGB", (w, h), (8, 8, 10))
    image = Image.composite(black, image, scrim)

    draw = ImageDraw.Draw(image)
    y = top_of_text
    for ln in lines:
        x = (w - draw.textlength(ln, font=font)) / 2
        draw.text((x + 3, y + 3), ln, font=font, fill=(0, 0, 0, 140))  # soft shadow
        draw.text((x, y), ln, font=font, fill=(255, 255, 255))
        y += line_height
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description="Stamp CSV text ideas onto your own photos.")
    parser.add_argument("csv_file", nargs="?", default="batch.csv", help="CSV to read (default: batch.csv)")
    parser.add_argument("--photos", default="photos", help="folder with your photos (default: photos)")
    parser.add_argument("--output", default="output-photos", help="folder to save results (default: output-photos)")
    parser.add_argument("--text-column", default="prompt", help="which CSV column holds the text to stamp (default: prompt)")
    parser.add_argument("--seed", type=int, default=None, help="number that makes the random photo pairing repeatable")
    parser.add_argument("--overwrite", action="store_true", help="redo images that already exist")
    args = parser.parse_args()

    def resolve(p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else (SCRIPT_DIR / path)

    text_column = args.text_column.strip().lower()
    rows = read_rows(resolve(args.csv_file), text_column)
    photos = list_photos(resolve(args.photos))
    output_root = resolve(args.output)
    picker = PhotoPicker(photos, args.seed)

    usable = [r for r in rows if r[text_column] and r["client"] and r["concept"] and r["angle"]]
    for r in rows:
        if r not in usable:
            print(f"WARNING: CSV row {r['row_number']} has empty fields - skipped")
    if not usable:
        sys.exit("\nNothing to do - no usable rows in the CSV.")
    if not FONT_PATH:
        print("WARNING: no system font found; using a basic built-in font.")

    total = len(usable) * len(SIZES)
    print(f"\nRows   : {len(usable)}   Photos: {len(photos)}   Images to make: {total}\n")

    done = created = skipped = 0
    failures: list[str] = []
    photo_cache: dict[Path, Image.Image] = {}

    for row in usable:
        photo_path = picker.next()
        stem = f"{slug(row['client'])}_{slug(row['concept'])}_{slug(row['angle'])}"
        folder = output_root / slug(row["client"])
        for (w, h) in SIZES:
            done += 1
            out_path = folder / f"{stem}_{w}x{h}.png"
            if out_path.exists() and not args.overwrite:
                skipped += 1
                print(f"[{done}/{total}] SKIP  {out_path.name}  (already exists)")
                continue
            try:
                if photo_path not in photo_cache:
                    source = Image.open(photo_path)
                    photo_cache[photo_path] = ImageOps.exif_transpose(source).convert("RGB")
                framed = crop_to_size(photo_cache[photo_path], w, h)
                finished = draw_caption(framed, row[text_column])
                folder.mkdir(parents=True, exist_ok=True)
                finished.save(out_path, format="PNG")
                created += 1
                print(f"[{done}/{total}] OK    {out_path.name}  (photo: {photo_path.name})")
            except Exception as error:
                failures.append(f"{out_path.name}: {type(error).__name__}: {error}")
                print(f"[{done}/{total}] FAILED  {out_path.name}  -> {error}")

    print(f"\n{'-' * 60}")
    print(f"created: {created}   skipped: {skipped}   failed: {len(failures)}")
    if failures:
        log = output_root / "overlay-failures.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("\n".join(failures) + "\n", encoding="utf-8")
        print(f"Failure details: {log}")
        return 1
    print(f"Images are in: {output_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
