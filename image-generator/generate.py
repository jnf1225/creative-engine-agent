#!/usr/bin/env python3
"""
generate.py - Batch image generation from a CSV of prompts.

Reads a CSV with the columns: client, concept, angle, prompt
For every row it generates three images (1080x1080, 1080x1350, 1080x1920) with
OpenAI's Images API and saves them to:

    output/<client>/<client>_<concept>_<angle>_<size>.png

Requests run in parallel. Anything that fails is written to failures.log and
failures.csv instead of stopping the batch.

Usage:
    python generate.py                     # uses prompts.csv
    python generate.py --csv my_file.csv   # uses a different CSV
    python generate.py --dry-run           # check the CSV, generate nothing
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import os
import random
import re
import sys
import threading
import time
import typing
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# --- Friendly import errors -------------------------------------------------
# If a package is missing, say which command fixes it instead of showing a
# Python traceback.

def _missing(package: str) -> "typing.NoReturn":
    sys.exit(
        f"\nERROR: the Python package '{package}' is not installed.\n"
        f"Fix it by running this in your terminal, from this folder:\n\n"
        f"    pip install -r requirements.txt\n"
    )


try:
    from dotenv import load_dotenv
except ImportError:
    _missing("python-dotenv")

try:
    from PIL import Image
except ImportError:
    _missing("Pillow")

try:
    import openai
    from openai import OpenAI
except ImportError:
    _missing("openai")


# --- Settings ---------------------------------------------------------------

# OpenAI's current image generation model (verified against OpenAI's docs).
MODEL = "gpt-image-2"

REQUIRED_COLUMNS = ("client", "concept", "angle", "prompt")

SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Size:
    """A size we want on disk, plus the size we ask the API for.

    gpt-image-2 only accepts widths and heights that divide evenly by 16, so
    1080x1080, 1080x1350 and 1080x1920 cannot be requested directly. For each
    one we request the next size up that has the *exact same* aspect ratio and
    is a legal API size, then scale it down to the exact pixel size you asked
    for. Same shape, no cropping, no stretching.
    """

    width: int
    height: int
    api_size: str

    @property
    def label(self) -> str:
        return f"{self.width}x{self.height}"


SIZES = (
    Size(1080, 1080, "1088x1088"),   # 1:1   square
    Size(1080, 1350, "1088x1360"),   # 4:5   portrait
    Size(1080, 1920, "1152x2048"),   # 9:16  story / reel
)


@dataclass
class Job:
    """One image to make: one CSV row at one size."""

    row_number: int
    client: str
    concept: str
    angle: str
    prompt: str
    size: Size
    path: Path

    @property
    def name(self) -> str:
        return self.path.name


# --- Helpers ----------------------------------------------------------------

def slug(value: str) -> str:
    """Turn free text into something safe for a file or folder name."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip())
    return cleaned.strip("-") or "untitled"


class Reporter:
    """Prints progress and records failures. Safe to call from many threads."""

    def __init__(self, total: int, log_path: Path, csv_path: Path):
        self.total = total
        self.log_path = log_path
        self.csv_path = csv_path
        self._lock = threading.Lock()
        self._done = 0
        self.succeeded = 0
        self.skipped = 0
        self.failures: list[dict] = []

    def _tick(self) -> str:
        self._done += 1
        return f"[{self._done}/{self.total}]"

    def success(self, job: Job, seconds: float) -> None:
        with self._lock:
            self.succeeded += 1
            print(f"{self._tick()} OK      {job.name}  ({seconds:.1f}s)", flush=True)

    def skip(self, job: Job, why: str) -> None:
        with self._lock:
            self.skipped += 1
            print(f"{self._tick()} SKIP    {job.name}  ({why})", flush=True)

    def failure(self, job: Job, error: str) -> None:
        with self._lock:
            self.failures.append(
                {
                    "row": job.row_number,
                    "client": job.client,
                    "concept": job.concept,
                    "angle": job.angle,
                    "size": job.size.label,
                    "file": job.name,
                    "error": error,
                    "prompt": job.prompt,
                }
            )
            print(f"{self._tick()} FAILED  {job.name}  -> {error}", flush=True)

    def write_failure_files(self) -> None:
        """Write failures.log (readable) and failures.csv (re-runnable)."""
        if not self.failures:
            return

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n===== Run finished {stamp} - {len(self.failures)} failure(s) =====\n")
            for item in self.failures:
                handle.write(
                    f"[CSV row {item['row']}] {item['file']}\n"
                    f"    client : {item['client']}\n"
                    f"    concept: {item['concept']}\n"
                    f"    angle  : {item['angle']}\n"
                    f"    size   : {item['size']}\n"
                    f"    error  : {item['error']}\n"
                    f"    prompt : {item['prompt']}\n\n"
                )

        # A CSV of just the failed rows, in the original format, so a retry is
        # simply: python generate.py --csv failures.csv
        failed_rows: dict[int, dict] = {}
        for item in self.failures:
            failed_rows.setdefault(
                item["row"],
                {
                    "client": item["client"],
                    "concept": item["concept"],
                    "angle": item["angle"],
                    "prompt": item["prompt"],
                },
            )
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(REQUIRED_COLUMNS))
            writer.writeheader()
            for _, row in sorted(failed_rows.items()):
                writer.writerow(row)


# --- CSV reading ------------------------------------------------------------

def read_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        sys.exit(
            f"\nERROR: could not find the CSV file '{csv_path}'.\n"
            f"Put your CSV in this folder, or point at it with:\n\n"
            f"    python generate.py --csv path/to/your-file.csv\n"
        )

    # utf-8-sig strips the invisible marker Excel adds to the first column name.
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in headers]
        if missing:
            sys.exit(
                f"\nERROR: '{csv_path.name}' is missing the column(s): {', '.join(missing)}\n"
                f"The first line of the file must be exactly:\n\n"
                f"    client,concept,angle,prompt\n"
            )

        # Map our column names to however they were actually spelled/spaced
        # in the file, so " Client" and "client" both work.
        actual = {
            name.strip().lower(): name
            for name in reader.fieldnames
            if name is not None
        }

        rows = []
        # Line 1 is the header, so data starts at line 2.
        for number, raw in enumerate(reader, start=2):
            row = {key: (raw.get(actual[key]) or "").strip() for key in REQUIRED_COLUMNS}
            row["row_number"] = number
            rows.append(row)
        return rows


def build_jobs(rows: list[dict], output_root: Path, overwrite: bool) -> tuple[list[Job], list[str]]:
    jobs: list[Job] = []
    problems: list[str] = []

    for row in rows:
        blank = [c for c in REQUIRED_COLUMNS if not row[c]]
        if blank:
            problems.append(f"CSV row {row['row_number']}: empty {', '.join(blank)} - row skipped")
            continue

        folder = output_root / slug(row["client"])
        stem = f"{slug(row['client'])}_{slug(row['concept'])}_{slug(row['angle'])}"
        for size in SIZES:
            jobs.append(
                Job(
                    row_number=row["row_number"],
                    client=row["client"],
                    concept=row["concept"],
                    angle=row["angle"],
                    prompt=row["prompt"],
                    size=size,
                    path=folder / f"{stem}_{size.label}.png",
                )
            )

    if not overwrite:
        # Warn about names that would collide (two CSV rows producing the same
        # file), because the second one would silently overwrite the first.
        seen: dict[Path, int] = {}
        for job in jobs:
            if job.path in seen and seen[job.path] != job.row_number:
                problems.append(
                    f"CSV rows {seen[job.path]} and {job.row_number} both produce "
                    f"'{job.name}' - the later row will win"
                )
            seen[job.path] = job.row_number

    return jobs, problems


# --- Image generation -------------------------------------------------------

# These are worth retrying: the API was busy, slow, or briefly unreachable.
RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


def generate_one(api: OpenAI, job: Job, quality: str, attempts: int) -> None:
    """Generate a single image and write it to disk. Raises on failure."""
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = api.images.generate(
                model=MODEL,
                prompt=job.prompt,
                size=job.size.api_size,
                quality=quality,
                output_format="png",
                n=1,
            )
            break
        except RETRYABLE as error:
            last_error = error
            if attempt == attempts:
                raise
            # Back off a bit longer each time, with a little jitter so parallel
            # workers do not all retry at the same instant.
            time.sleep(min(2 ** attempt, 30) + random.uniform(0, 1))
    else:  # pragma: no cover - loop always breaks or raises
        raise last_error if last_error else RuntimeError("image generation failed")

    payload = response.data[0].b64_json if response.data else None
    if not payload:
        raise RuntimeError("the API returned no image data")

    image = Image.open(io.BytesIO(base64.b64decode(payload)))
    # The API size is a same-shape step up from the requested size, so this is
    # a clean downscale to the exact pixels asked for.
    if image.size != (job.size.width, job.size.height):
        image = image.resize((job.size.width, job.size.height), Image.LANCZOS)

    job.path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary name first so an interrupted run never leaves a
    # half-written PNG that a later run would treat as already done.
    temp_path = job.path.with_suffix(".png.part")
    image.save(temp_path, format="PNG")
    temp_path.replace(job.path)


def run_job(api: OpenAI, job: Job, quality: str, attempts: int, overwrite: bool, reporter: Reporter) -> None:
    if job.path.exists() and not overwrite:
        reporter.skip(job, "already exists")
        return

    started = time.time()
    try:
        generate_one(api, job, quality, attempts)
    except Exception as error:  # never let one image stop the batch
        reporter.failure(job, describe_error(error))
    else:
        reporter.success(job, time.time() - started)


def describe_error(error: Exception) -> str:
    """Turn an exception into one readable line."""
    if isinstance(error, openai.BadRequestError):
        # Usually a prompt the safety system rejected, or a bad parameter.
        detail = getattr(error, "message", "") or str(error) or "no reason given"
        return f"rejected by the API: {detail}"
    if isinstance(error, openai.AuthenticationError):
        return "the API key was rejected - check OPENAI_API_KEY in your .env file"
    if isinstance(error, openai.RateLimitError):
        return "rate limited or out of credit, after all retries"
    if isinstance(error, (openai.APIConnectionError, openai.APITimeoutError)):
        return "could not reach the API (network problem), after all retries"
    return f"{type(error).__name__}: {error}"


# --- Main -------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate images from a CSV of prompts using OpenAI's Images API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv", default="prompts.csv", help="CSV file to read (default: prompts.csv)")
    parser.add_argument("--output", default="output", help="folder to save images into (default: output)")
    parser.add_argument("--workers", type=int, default=5, help="how many images to make at once (default: 5)")
    parser.add_argument(
        "--quality",
        default="medium",
        choices=("low", "medium", "high"),
        help="image quality (default: medium; 'high' is much slower and costs more)",
    )
    parser.add_argument("--retries", type=int, default=3, help="attempts per image before giving up (default: 3)")
    parser.add_argument("--overwrite", action="store_true", help="regenerate images that already exist")
    parser.add_argument("--dry-run", action="store_true", help="check the CSV and list the work, generate nothing")
    return parser.parse_args()


def resolve(path_text: str) -> Path:
    """Allow paths relative to the script's folder, not just the terminal's."""
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else (SCRIPT_DIR / path)


def main() -> int:
    args = parse_args()

    csv_path = resolve(args.csv)
    output_root = resolve(args.output)

    rows = read_rows(csv_path)
    jobs, problems = build_jobs(rows, output_root, args.overwrite)

    for problem in problems:
        print(f"WARNING: {problem}", flush=True)

    if not jobs:
        print(f"\nNothing to do - no usable rows found in {csv_path.name}.")
        return 1

    print(f"\nCSV file : {csv_path}")
    print(f"Rows     : {len(rows)}")
    print(f"Images   : {len(jobs)}  ({len(SIZES)} sizes per usable row)")
    print(f"Model    : {MODEL} (quality: {args.quality})")
    print(f"Output   : {output_root}")

    if args.dry_run:
        print("\nDry run - these files would be created:\n")
        for job in jobs:
            marker = "exists" if job.path.exists() else "new"
            print(f"  [{marker:>6}] {job.path}")
        print("\nNo images were generated. Remove --dry-run to generate them for real.")
        return 0

    load_dotenv(SCRIPT_DIR / ".env")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        sys.exit(
            "\nERROR: no API key found.\n"
            f"Create a file called .env in this folder ({SCRIPT_DIR})\n"
            "containing one line:\n\n"
            "    OPENAI_API_KEY=sk-your-key-here\n"
        )

    api = OpenAI(api_key=api_key, timeout=300.0, max_retries=0)  # retries handled here
    reporter = Reporter(len(jobs), resolve("failures.log"), resolve("failures.csv"))

    print(f"Workers  : {args.workers} at a time\n")
    started = time.time()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [
            pool.submit(run_job, api, job, args.quality, args.retries, args.overwrite, reporter)
            for job in jobs
        ]
        try:
            for future in as_completed(futures):
                future.result()  # run_job swallows errors; this re-raises only bugs
        except KeyboardInterrupt:
            print("\nStopping... (finishing images already in flight)", flush=True)
            for future in futures:
                future.cancel()
            raise

    elapsed = time.time() - started
    print(f"\n{'-' * 60}")
    print(f"Done in {elapsed / 60:.1f} min")
    print(f"  created : {reporter.succeeded}")
    print(f"  skipped : {reporter.skipped}")
    print(f"  failed  : {len(reporter.failures)}")

    if reporter.failures:
        reporter.write_failure_files()
        print(f"\nDetails of what failed : {reporter.log_path}")
        print(f"Just the failed rows   : {reporter.csv_path}")
        print(f"Retry only those with  : python generate.py --csv failures.csv")
        return 1

    print(f"\nImages are in: {output_root}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
