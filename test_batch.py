"""Batch upload tests. Run with:  pytest -v"""
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

import app as app_module
import batch
import ocr
from test_api import _label_png, tiny_png
from test_checkers import OCR_GOOD

HEADER = "filename,brand_name,class_type,abv,net_contents\n"
GOOD_ROW = "{},OLD TOM DISTILLERY,Kentucky Straight Bourbon Whiskey,45,750 mL\n"


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def start(client, csv_text, zip_bytes, csv_bytes=None):
    return client.post("/api/batch", files={
        "applications": ("apps.csv", csv_bytes if csv_bytes is not None else csv_text.encode(), "text/csv"),
        "images_zip": ("labels.zip", zip_bytes, "application/zip"),
    })


def finish(client, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/batch/{job_id}").json()
        if body["done"]:
            return body
        time.sleep(0.05)
    raise AssertionError("batch did not finish")


def by_name(body):
    return {r["filename"]: r for r in body["rows"]}


@pytest.fixture
def client():
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def fake_ocr(monkeypatch):
    monkeypatch.setattr(ocr, "read_label", lambda b: OCR_GOOD)


# ---- the happy path ---------------------------------------------------------------------

def test_batch_all_pass(client, fake_ocr):
    z = make_zip({"a.png": tiny_png(), "b.png": tiny_png()})
    r = start(client, HEADER + GOOD_ROW.format("a.png") + GOOD_ROW.format("b.png"), z)
    assert r.status_code == 200 and r.json()["total"] == 2
    body = finish(client, r.json()["job_id"])
    assert body["summary"] == {"pass": 2, "review": 0, "fail": 0, "error": 0}
    assert all(len(row["fields"]) == 5 for row in body["rows"])


def test_mixed_results_are_counted(client, fake_ocr):
    z = make_zip({"a.png": tiny_png(), "b.png": tiny_png()})
    csv_text = (HEADER + GOOD_ROW.format("a.png")
                + "b.png,Old Tom Distillery,Kentucky Straight Bourbon Whiskey,40,750 mL\n"
                + "ghost.png,X,Y,45,750 mL\n")
    body = finish(client, start(client, csv_text, z).json()["job_id"])
    rows = by_name(body)
    assert rows["a.png"]["status"] == "pass"
    assert rows["b.png"]["status"] == "fail"           # ABV 40 vs 45
    assert rows["ghost.png"]["status"] == "error"
    assert "no image named" in rows["ghost.png"]["message"]
    assert body["summary"] == {"pass": 1, "review": 0, "fail": 1, "error": 1}


def test_progress_rows_can_be_fetched_in_pieces(client, fake_ocr):
    z = make_zip({"a.png": tiny_png(), "b.png": tiny_png()})
    job = start(client, HEADER + GOOD_ROW.format("a.png") + GOOD_ROW.format("b.png"), z).json()["job_id"]
    finish(client, job)
    assert len(client.get(f"/api/batch/{job}?since=1").json()["rows"]) == 1
    assert client.get(f"/api/batch/{job}?since=2").json()["rows"] == []


# ---- forgiving about the CSV and ZIP ----------------------------------------------------

def test_header_names_are_flexible_and_bom_ok(client, fake_ocr):
    csv_text = ("Image,Brand,Class/Type,Alcohol Content,Net Contents\n"
                "a.png,OLD TOM DISTILLERY,Kentucky Straight Bourbon Whiskey,45,750 mL\n")
    r = start(client, "", make_zip({"a.png": tiny_png()}), csv_bytes=b"\xef\xbb\xbf" + csv_text.encode())
    assert r.status_code == 200
    assert finish(client, r.json()["job_id"])["summary"]["pass"] == 1


def test_semicolon_csv_and_windows_encoding(client, fake_ocr):
    csv_text = ("filename;brand_name;class_type;abv;net_contents\n"
                "a.png;OLD TOM DISTILLERY;Kentucky Straight Bourbon Whiskey;45;750 mL\n")
    r = start(client, "", make_zip({"a.png": tiny_png()}), csv_bytes=csv_text.encode("cp1252"))
    assert finish(client, r.json()["job_id"])["summary"]["pass"] == 1


def test_images_in_folders_and_uppercase_names_match(client, fake_ocr):
    z = make_zip({"labels/batch1/LABEL.PNG": tiny_png(), "__MACOSX/labels/._LABEL.PNG": b"junk"})
    r = start(client, HEADER + GOOD_ROW.format("label.png"), z)
    body = finish(client, r.json()["job_id"])
    assert body["summary"]["pass"] == 1
    assert r.json()["warnings"] == []


def test_unused_images_produce_a_warning(client, fake_ocr):
    z = make_zip({"a.png": tiny_png(), "extra.png": tiny_png()})
    r = start(client, HEADER + GOOD_ROW.format("a.png"), z)
    assert any("no row in the CSV" in w for w in r.json()["warnings"])


def test_blank_cells_give_an_error_row_not_a_crash(client, fake_ocr):
    z = make_zip({"a.png": tiny_png()})
    body = finish(client, start(client, HEADER + "a.png,,Kentucky,45,750 mL\n", z).json()["job_id"])
    row = body["rows"][0]
    assert row["status"] == "error" and "brand_name" in row["message"]


def test_one_unreadable_image_does_not_stop_the_rest(client, monkeypatch):
    def flaky(data):
        if data == b"not an image":
            raise ocr.UnreadableImage("That file isn't an image we can open.")
        return OCR_GOOD
    monkeypatch.setattr(ocr, "read_label", flaky)
    z = make_zip({"a.png": tiny_png(), "bad.png": b"not an image"})
    body = finish(client, start(client, HEADER + GOOD_ROW.format("bad.png") + GOOD_ROW.format("a.png"), z).json()["job_id"])
    rows = by_name(body)
    assert rows["bad.png"]["status"] == "error"
    assert rows["a.png"]["status"] == "pass"


def test_unexpected_exception_in_one_row_is_contained(client, monkeypatch):
    def boom(data):
        raise RuntimeError("tesseract crashed")
    monkeypatch.setattr(ocr, "read_label", boom)
    z = make_zip({"a.png": tiny_png()})
    body = finish(client, start(client, HEADER + GOOD_ROW.format("a.png"), z).json()["job_id"])
    assert body["rows"][0]["status"] == "error"


# ---- rejecting bad uploads with plain-English messages ----------------------------------

def test_csv_missing_columns(client):
    r = start(client, "filename,brand_name\na.png,X\n", make_zip({"a.png": tiny_png()}))
    assert r.status_code == 400
    assert "missing these columns" in r.json()["detail"]


def test_empty_csv_and_header_only_csv(client):
    assert start(client, "", make_zip({"a.png": tiny_png()})).status_code == 400
    r = start(client, HEADER, make_zip({"a.png": tiny_png()}))
    assert r.status_code == 400 and "no labels" in r.json()["detail"]


def test_zip_that_is_not_a_zip(client):
    r = start(client, HEADER + GOOD_ROW.format("a.png"), b"hello")
    assert r.status_code == 400 and "isn't a ZIP" in r.json()["detail"]


def test_zip_without_images(client):
    r = start(client, HEADER + GOOD_ROW.format("a.png"), make_zip({"notes.txt": b"hi"}))
    assert r.status_code == 400 and "any images" in r.json()["detail"]


def test_too_many_rows(client, monkeypatch):
    monkeypatch.setattr(batch, "MAX_ROWS", 2)
    csv_text = HEADER + "".join(GOOD_ROW.format(f"{i}.png") for i in range(3))
    r = start(client, csv_text, make_zip({"0.png": tiny_png()}))
    assert r.status_code == 400 and "limit is 2" in r.json()["detail"]


def test_zip_over_the_size_limit(client, monkeypatch):
    monkeypatch.setattr(batch, "MAX_ZIP_BYTES", 100)
    r = start(client, HEADER + GOOD_ROW.format("a.png"), make_zip({"a.png": tiny_png()}))
    assert r.status_code == 400 and "larger than" in r.json()["detail"]


def test_unknown_job_is_a_friendly_404(client):
    r = client.get("/api/batch/does-not-exist")
    assert r.status_code == 404 and "run it again" in r.json()["detail"]


def test_temp_files_are_removed_after_a_batch(client, fake_ocr):
    import glob
    import tempfile
    before = set(glob.glob(tempfile.gettempdir() + "/labels-*"))
    z = make_zip({"a.png": tiny_png()})
    finish(client, start(client, HEADER + GOOD_ROW.format("a.png"), z).json()["job_id"])
    time.sleep(0.2)
    assert set(glob.glob(tempfile.gettempdir() + "/labels-*")) == before


def test_old_finished_jobs_are_purged(client, fake_ocr, monkeypatch):
    z = make_zip({"a.png": tiny_png()})
    job_id = start(client, HEADER + GOOD_ROW.format("a.png"), z).json()["job_id"]
    finish(client, job_id)
    time.sleep(0.2)
    monkeypatch.setattr(batch, "JOB_TTL_SECONDS", -1)
    start(client, HEADER + GOOD_ROW.format("a.png"), z)    # starting a new job purges expired ones
    assert client.get(f"/api/batch/{job_id}").status_code == 404


# ---- with real OCR ----------------------------------------------------------------------

needs_tesseract = pytest.mark.skipif(not ocr.tesseract_available(), reason="tesseract not installed")


@needs_tesseract
def test_real_ocr_batch(client):
    z = make_zip({"good.png": _label_png(), "wrong_abv.png": _label_png()})
    csv_text = (HEADER + GOOD_ROW.format("good.png")
                + "wrong_abv.png,OLD TOM DISTILLERY,Kentucky Straight Bourbon Whiskey,40,750 mL\n")
    body = finish(client, start(client, csv_text, z).json()["job_id"], timeout=60)
    rows = by_name(body)
    assert rows["good.png"]["status"] == "pass", rows["good.png"]
    assert rows["wrong_abv.png"]["status"] == "fail"