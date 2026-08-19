# CSV → Image Generator

Turns a spreadsheet of prompts into finished PNG images using OpenAI's
image model (`gpt-image-2`), three sizes per prompt, sorted into folders
by client.

## What you need (one-time setup)

1. **Python 3.10 or newer** — check with `python3 --version`
   (on Windows: `python --version`). If it's missing, install it from
   https://www.python.org/downloads/ and tick "Add Python to PATH"
   during the Windows install.

2. **The packages this script uses** — from inside this folder, run:

   ```
   pip install -r requirements.txt
   ```

3. **Your OpenAI API key** — get one at https://platform.openai.com/api-keys
   (your account also needs billing/credit set up). Then:
   - copy the file `.env.example` and name the copy exactly `.env`
   - open `.env` in any text editor and replace the placeholder with
     your real key, so the file contains one line like:

     ```
     OPENAI_API_KEY=sk-abc123...
     ```

   The `.env` file is your secret. It is already git-ignored so it
   never gets uploaded anywhere.

## Your CSV file

Name it `prompts.csv` and put it in this folder (or point at any file
with `--csv`). The first line must be this header, exactly:

```
client,concept,angle,prompt
```

One row per prompt. If a prompt contains commas, wrap it in double
quotes — Excel and Google Sheets do this for you automatically when you
export as CSV. A starter `prompts.csv` with example rows is included;
replace its rows with your own.

## Running it

From inside this folder:

```
python3 generate.py
```

That reads `prompts.csv`. To use a file with a different name, put the
name after the command:

```
python3 generate.py batch.csv
```

Each row is generated at **1080x1080, 1080x1350, and 1080x1920** and
saved as:

```
output/<client>/<client>_<concept>_<angle>_<size>.png
```

Progress prints as it goes. Several images are generated at the same
time (5 by default), so a 20-row batch (60 images) doesn't run one at
a time.

### If something fails

The batch keeps going. At the end you get:

- `failures.log` — a readable list of what failed and why
- `failures.csv` — just the failed rows, in the original format

Retry only the failures with:

```
python3 generate.py failures.csv
```

Already-finished images are **skipped automatically**, so re-running
never wastes money regenerating what you already have. Use
`--overwrite` if you *want* to regenerate.

### Useful options

| Command | What it does |
|---|---|
| `python3 generate.py --dry-run` | Checks your CSV and lists the files it *would* create — costs nothing |
| `python3 generate.py --quality low` | Faster and cheaper drafts (`medium` is the default, `high` is slow and pricier) |
| `python3 generate.py --workers 8` | More images at once (lower this if you hit rate limits) |
| `python3 generate.py --overwrite` | Regenerate images that already exist |

## A note on sizes

The image model only accepts dimensions divisible by 16, so 1080-pixel
sizes can't be requested directly. The script asks the API for the next
size up **at the exact same aspect ratio** (e.g. 1088x1360 for the 4:5),
then scales it down to the precise pixels you asked for. No cropping,
no stretching.

## Rough cost

With the default `medium` quality, budget on the order of a few cents
per image — so a 20-row batch (60 images) is typically a couple of
dollars. `--quality low` is cheaper, `high` is several times more.
Check current pricing at https://openai.com/api/pricing/.
