# Alcohol Label Verification (prototype)

A small web app that checks an alcohol label image against the data in an application. An agent enters the application values (brand name, class/type, ABV, net contents), uploads a label image, and gets a per-field result in a few seconds:

- **Pass** (green): the label matches.
- **Needs review** (yellow): probably fine, but a human should look (for example a capitalization difference or a one-character OCR slip).
- **Fail** (red): the label does not match, or a required item is missing.

The label is read **entirely offline**. Tesseract OCR runs as a local process and the app makes no outbound network calls.

**Live demo:** https://label-verification.onrender.com (hosted on Render's free tier, which gives the app only 0.1 of a CPU and 512 MB of memory: expect about 3–5 seconds per label there, under 1 second on a normal machine, and up to a minute for the first load after the site has been idle)

**Batch note:** because the free host has only 0.1 CPU, it is slow (about 4 seconds per label), so the 10-label demo batch takes about 30–40 seconds and a 300-label batch takes 15–20 minutes there. A normal machine is roughly 15–20 times faster; to see the real speed, run it locally or try a smaller batch.

For the approach, tools used and assumptions in one page, see [APPROACH.md](APPROACH.md).

## Run it locally

Requires Python 3.12 and [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) (Windows) / `apt install tesseract-ocr` (Linux) / `brew install tesseract` (macOS).

```
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://127.0.0.1:8000.

On Windows the app finds Tesseract at `C:\Program Files\Tesseract-OCR\tesseract.exe` automatically. If it is installed elsewhere, set the `TESSERACT_CMD` environment variable to the full path.

### Try it with sample labels

```
python generate_samples.py
```

This writes `samples/` with a good label, labels with deliberate errors (title-case warning, missing colon, reworded warning, wrong ABV, proof mismatch, missing warning), and poor-quality "photos" (tilted, blurred, low contrast, heavy JPEG compression, 4000 px, glare), plus `blank.png` and `notes.txt` for error handling. The good label matches these application values: `OLD TOM DISTILLERY`, `Kentucky Straight Bourbon Whiskey`, `45`, `750 mL`. The page has a "Fill in sample values" link.

### Check many labels at once

Open the **Multiple labels (Batch)** tab. Upload a CSV (columns `filename, brand_name, class_type, abv, net_contents`, one row per label) and a ZIP of the label images. Progress shows as labels finish, results list problems first, and **Download results (CSV)** saves a spreadsheet. A ready-made example is written to `samples/batch_demo/` (`applications.csv` and `labels.zip`; it includes a mix of passes, reviews, failures, an unreadable image and a missing file).

### Run the tests

```
pytest -v
```

75 tests. The few that run real OCR are skipped automatically if Tesseract is not installed.

### Docker

```
docker build -t label-check .
docker run -p 8000:8000 label-check
```

## Deployment

The app is deployed on [Render](https://render.com) from the `Dockerfile` in this repo (Docker runtime, free instance). The image installs Tesseract with `apt` and starts `uvicorn`; the port comes from the `PORT` environment variable. The same image can run on any container host (for example Azure App Service for Containers) with no code changes. Nothing is stored: uploaded images are processed in memory and discarded.

## How it works

1. **Preprocess** (`ocr.py`, Pillow only): respect the phone-rotation flag, grayscale, auto-contrast, downscale very large photos, and straighten tilted photos (a projection-profile search over about ±15°).
2. **OCR**: Tesseract via `pytesseract`. A throwaway OCR runs at startup so the first real request is not slow. Tesseract is limited to one thread (`OMP_THREAD_LIMIT=1`), which is much faster on small or shared hosts and gives identical text. Typical time is 0.2–1 s per label on a normal machine.
3. **Check each field** (`checkers.py`, pure functions, easy to test):
   - **Brand and class/type**: whole-word match. An exact match passes. A match that differs only in capitalization or punctuation, a near match (fuzzy, 90+), or a label line that has extra words (for example `OLD TOM` vs `OLD TOM DISTILLERY`) goes to review. Anything else fails and shows the closest text found.
   - **ABV**: parses `45% Alc./Vol.`, `ALC. 45% BY VOL.`, `45% ABV` and similar. If a proof is printed, it must equal 2 × ABV, otherwise review.
   - **Net contents**: normalizes mL, cL, L and fl oz before comparing (1% tolerance).
   - **Government warning**: compared word for word against the statutory text (27 CFR 16.21). `GOVERNMENT WARNING:` must be in capitals with the colon. A change in wording fails. A single-character difference that looks like an OCR slip is a review, not a fail.
4. **Overall** status is the worst field status.
5. **Batch** (`batch.py`): the CSV and ZIP are validated, then labels are checked a few at a time in a background thread (each OCR is single-threaded, so several run side by side). The page polls for progress. File names in the CSV are matched to images in the ZIP ignoring case and folders. One bad row (missing image, unreadable image, blank cell) is reported on its own and never stops the rest. Limits: 500 labels, 100 MB ZIP, 10 MB per image. Results are held in server memory for an hour and the uploaded images are deleted as soon as the batch finishes.

## How it was tested

- **75 automated tests** (`pytest -v`): the matching rules for every field, the API and its error messages, image straightening, and batch upload. A few run real OCR on generated labels.
- **Sample labels** (`python generate_samples.py`): a good label, labels with one deliberate mistake each, and poor "photos" (tilted, blurred, low contrast, heavy compression, very large, glare). Results were checked by hand in the browser.
- **Offline check**: with Wi-Fi turned off, both the one-label page and the batch page work normally, because nothing leaves the machine.
- **Fresh clone**: the repository was cloned into an empty folder, set up from `requirements.txt`, and passed all tests.

## Assumptions

- The application data is typed in by the agent (there is no integration with COLA or any other system).
- One label image per check, English text, standard units.
- A "Needs review" result is a prompt for a human decision, not a rejection.

## Limitations and trade-offs

- **Bold type** in the warning header cannot be verified from OCR text. The app checks wording and capitalization only, and a person has to confirm bold.
- **Glare or heavy reflections** over text can defeat OCR. In that case the app fails or asks for review rather than guessing.
- **Tesseract's built-in dictionary** can silently correct a misspelling on a label (for example `machlnery` read as `machinery`), so a real typo on a label can pass.
- **Extra text on the same line** as a field may produce a review where a human would say pass.
- **Only four fields are checked** (brand, class/type, ABV, net contents) plus the government warning. Bottler name and address and country of origin are not verified.
- **Wine and beer** rules (such as ABV exemptions and different statements) are not modeled.
- **Not done**: authentication and a persistent history of results. Batch results live in memory on a single server process, so they are lost if the server restarts (the page says so and the batch can be re-run). A production version would use a job queue and storage.
- Tesseract was chosen over a cloud OCR or a vision model because the brief forbids outbound connections. The trade-off is lower accuracy on hard photos.

## Tools

Python, FastAPI, uvicorn, Tesseract OCR (`pytesseract`), Pillow, rapidfuzz, pytest, plain HTML/JS front end (no frameworks, no CDN calls).

## AI assistance

This prototype was built with the help of an AI coding assistant (Claude) for design, code and tests. I ran, tested and reviewed the result myself.