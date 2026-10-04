"""
batch.py - check many labels at once.

The agent uploads a CSV (one row per label: filename + the four application fields) and a
ZIP of label images. Each row is checked with the same code as the one-label page.

A batch of a few hundred labels takes longer than one web request should, so the work runs
in a background thread and the page polls for progress. Everything lives in memory (the ZIP
sits in a temp folder that is deleted when the batch finishes); nothing is kept long-term.
"""
import csv
import io
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath

import ocr
from checkers import overall_status, verify_label

MAX_ROWS = 500                       # labels per batch
MAX_CSV_BYTES = 2 * 1024 * 1024      # 2 MB
MAX_ZIP_BYTES = 100 * 1024 * 1024    # 100 MB upload
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # per image, same as the one-label page
JOB_TTL_SECONDS = 60 * 60            # finished batches are forgotten after an hour
MAX_JOBS = 20
# Each OCR runs on one thread (see ocr.py), so several labels at once use the machine well.
WORKERS = int(os.environ.get("BATCH_WORKERS", max(1, min(3, os.cpu_count() or 1))))

REQUIRED = [("filename", "filename"), ("brand_name", "brand_name"), ("class_type", "class_type"),
            ("abv", "abv"), ("net_contents", "net_contents")]
HEADER_ALIASES = {
    "filename": {"filename", "file", "file_name", "image", "image_name", "image_file"},
    "brand_name": {"brand_name", "brand", "brandname"},
    "class_type": {"class_type", "class", "type", "classtype", "class_type_designation"},
    "abv": {"abv", "alcohol_content", "alcohol", "alc_vol", "alcohol_by_volume"},
    "net_contents": {"net_contents", "net", "netcontents", "volume", "size"},
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


class BatchError(ValueError):
    """A problem with the uploaded files, worded for a non-technical reader."""


# ---- reading the uploads ---------------------------------------------------------------

def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):   # cp1252: Excel on Windows
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise BatchError("We couldn't read that CSV file. Please save it from Excel as 'CSV' and try again.")


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def parse_applications(data: bytes) -> list[dict]:
    """CSV bytes -> list of {index, filename, brand_name, class_type, abv, net_contents}."""
    text = _decode(data)
    first_line = text.split("\n", 1)[0]
    delimiter = max([",", ";", "\t"], key=first_line.count)   # some regions export with ';'
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise BatchError("That CSV file is empty.")

    column = {}
    for position, name in enumerate(header):
        norm = _norm_header(name)
        for key, aliases in HEADER_ALIASES.items():
            if norm in aliases and key not in column:
                column[key] = position
    missing = [name for key, name in REQUIRED if key not in column]
    if missing:
        raise BatchError(
            "The CSV is missing these columns: " + ", ".join(missing)
            + ". The first row should be: filename, brand_name, class_type, abv, net_contents.")

    rows = []
    for line in reader:
        if not any(cell.strip() for cell in line):
            continue   # blank line
        row = {key: (line[pos].strip() if pos < len(line) else "") for key, pos in column.items()}
        row["index"] = len(rows)
        rows.append(row)
    if not rows:
        raise BatchError("The CSV has a header row but no labels in it.")
    if len(rows) > MAX_ROWS:
        raise BatchError(f"That CSV has {len(rows)} labels. The limit is {MAX_ROWS} per batch; "
                         "please split it into smaller files.")
    return rows


def save_zip(fileobj, folder: str) -> str:
    """Copy the uploaded ZIP into `folder` (with a size cap) and check it really is a ZIP."""
    path = os.path.join(folder, "labels.zip")
    size = 0
    with open(path, "wb") as out:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_ZIP_BYTES:
                raise BatchError(f"That ZIP is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB. "
                                 "Please split the images into smaller ZIP files.")
            out.write(chunk)
    if size == 0:
        raise BatchError("Please choose a ZIP file of label images.")
    if not zipfile.is_zipfile(path):
        raise BatchError("That file isn't a ZIP. Select your label images, right-click, "
                         "and choose 'Send to > Compressed (zipped) folder'.")
    return path


def index_zip(path: str) -> tuple[dict, int]:
    """Map lower-case image file name -> its path inside the ZIP (folders are ignored)."""
    members, duplicates = {}, 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = info.filename
            base = PurePosixPath(name.replace("\\", "/")).name
            if (info.is_dir() or "__MACOSX/" in name or base.startswith(".")
                    or os.path.splitext(base)[1].lower() not in IMAGE_EXTENSIONS):
                continue
            key = base.lower()
            if key in members:
                duplicates += 1
            else:
                members[key] = name
    if not members:
        raise BatchError("We didn't find any images (PNG or JPG) in that ZIP.")
    return members, duplicates


# ---- checking one row ------------------------------------------------------------------

def _error_row(row: dict, message: str, seconds: float = 0.0) -> dict:
    return {"index": row["index"], "filename": row["filename"], "brand_name": row["brand_name"],
            "status": "error", "message": message, "fields": [], "seconds": round(seconds, 2)}


def check_row(row: dict, zip_path: str, members: dict) -> dict:
    start = time.perf_counter()
    try:
        missing = [key for key, _ in REQUIRED if not row[key]]
        if missing:
            return _error_row(row, "This row is missing: " + ", ".join(missing) + ".")
        if not re.search(r"\d", row["abv"]):
            return _error_row(row, "Alcohol content should be a number, like 45.")

        key = PurePosixPath(row["filename"].replace("\\", "/")).name.lower()
        member = members.get(key)
        if member is None:
            return _error_row(row, f"There is no image named \"{row['filename']}\" in the ZIP.")
        with zipfile.ZipFile(zip_path) as zf:
            if zf.getinfo(member).file_size > MAX_IMAGE_BYTES:
                return _error_row(row, "That image is larger than 10 MB. Please use a smaller file.")
            data = zf.read(member)

        application = {k: row[k] for k in ("brand_name", "class_type", "abv", "net_contents")}
        try:
            text = ocr.read_label(data)
        except ocr.UnreadableImage as e:
            return _error_row(row, str(e), time.perf_counter() - start)
        results = verify_label(application, text)
        return {"index": row["index"], "filename": row["filename"], "brand_name": row["brand_name"],
                "status": overall_status(results).value, "message": "",
                "fields": [r.to_dict() for r in results],
                "seconds": round(time.perf_counter() - start, 2)}
    except Exception:   # one bad label must never stop the other 299
        return _error_row(row, "Something went wrong reading this label.", time.perf_counter() - start)


# ---- background jobs -------------------------------------------------------------------

class Job:
    def __init__(self, total: int, warnings: list[str]):
        self.id = secrets.token_urlsafe(16)    # unguessable
        self.total = total
        self.warnings = warnings
        self.rows: list[dict] = []             # in the order they finish
        self.done = False
        self.created = time.time()
        self.finished = None
        self.started = time.perf_counter()
        self.seconds = 0.0
        self.lock = threading.Lock()

    def add(self, result: dict) -> None:
        with self.lock:
            self.rows.append(result)

    def finish(self) -> None:
        with self.lock:
            self.done = True
            self.finished = time.time()
            self.seconds = round(time.perf_counter() - self.started, 1)

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            counts = {"pass": 0, "review": 0, "fail": 0, "error": 0}
            for r in self.rows:
                counts[r["status"]] += 1
            return {"done": self.done, "total": self.total, "completed": len(self.rows),
                    "summary": counts, "rows": self.rows[since:], "next": len(self.rows),
                    "warnings": self.warnings, "seconds": self.seconds}


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _purge_old_jobs() -> None:
    now = time.time()
    for job_id, job in list(_jobs.items()):
        if job.done and now - job.finished > JOB_TTL_SECONDS:
            del _jobs[job_id]
    finished = sorted((j for j in _jobs.values() if j.done), key=lambda j: j.finished)
    while len(_jobs) >= MAX_JOBS and finished:
        del _jobs[finished.pop(0).id]


def get_job(job_id: str) -> Job | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def start_job(rows: list[dict], folder: str, zip_path: str, members: dict, warnings: list[str]) -> Job:
    job = Job(len(rows), warnings)
    with _jobs_lock:
        _purge_old_jobs()
        _jobs[job.id] = job

    def work():
        try:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for result in pool.map(lambda r: check_row(r, zip_path, members), rows):
                    job.add(result)
        finally:
            shutil.rmtree(folder, ignore_errors=True)   # the uploaded images are not kept
            job.finish()

    threading.Thread(target=work, daemon=True).start()
    return job


def prepare(applications_csv: bytes, zip_file) -> tuple[list[dict], str, str, dict, list[str]]:
    """Validate both uploads. Returns (rows, folder, zip_path, members, warnings)."""
    if len(applications_csv) > MAX_CSV_BYTES:
        raise BatchError("That CSV file is too large.")
    rows = parse_applications(applications_csv)
    folder = tempfile.mkdtemp(prefix="labels-")
    try:
        zip_path = save_zip(zip_file, folder)
        try:
            members, duplicates = index_zip(zip_path)
        except zipfile.BadZipFile:
            raise BatchError("That ZIP file appears to be damaged. Please create it again.")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    warnings = []
    named = {PurePosixPath(r["filename"].replace("\\", "/")).name.lower() for r in rows}
    unused = len(set(members) - named)
    if unused:
        warnings.append(f"{unused} image(s) in the ZIP have no row in the CSV and were skipped.")
    if duplicates:
        warnings.append(f"{duplicates} image(s) in the ZIP share a file name with another; "
                        "only the first was used.")
    return rows, folder, zip_path, members, warnings