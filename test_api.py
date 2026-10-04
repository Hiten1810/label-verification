"""API tests. Run with:  pytest -v"""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

import app as app_module
import ocr
from test_checkers import OCR_GOOD

FIELDS = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "abv": "45",
    "net_contents": "750 mL",
}


def tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, "PNG")
    return buf.getvalue()


def post(client, fields=FIELDS, data=None, name="label.png"):
    files = {"image": (name, data if data is not None else tiny_png(), "image/png")}
    return client.post("/api/verify", data=fields, files=files)


@pytest.fixture
def client():
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def fake_ocr(monkeypatch):
    """Skip real OCR: pretend the label image produced OCR_GOOD."""
    monkeypatch.setattr(app_module.ocr, "read_label", lambda b: OCR_GOOD)


# ---- page + health ----------------------------------------------------------

def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Check Label" in r.text


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


# ---- verify (OCR mocked) ----------------------------------------------------

def test_verify_all_pass(client, fake_ocr):
    body = post(client).json()
    assert body["overall"] == "pass"
    assert [r["field"] for r in body["results"]] == [
        "Brand name", "Class/type", "Alcohol content", "Net contents", "Government warning"]
    assert "seconds" in body and "ocr_text" in body


def test_verify_wrong_abv_fails(client, fake_ocr):
    body = post(client, {**FIELDS, "abv": "40"}).json()
    assert body["overall"] == "fail"


def test_verify_case_difference_is_review(client, fake_ocr):
    body = post(client, {**FIELDS, "brand_name": "Old Tom Distillery"}).json()
    assert body["overall"] == "review"


# ---- error handling ---------------------------------------------------------

def test_missing_fields_named_in_error(client, fake_ocr):
    r = post(client, {**FIELDS, "brand_name": "", "net_contents": ""})
    assert r.status_code == 400
    assert "Brand name" in r.json()["detail"] and "Net contents" in r.json()["detail"]


def test_abv_must_contain_a_number(client, fake_ocr):
    r = post(client, {**FIELDS, "abv": "strong"})
    assert r.status_code == 400


def test_non_image_upload_rejected_politely(client):
    r = post(client, data=b"this is not an image", name="notes.txt")
    assert r.status_code == 400
    assert "image" in r.json()["detail"].lower()


def test_empty_upload_rejected(client):
    r = post(client, data=b"")
    assert r.status_code == 400


def test_oversized_upload_rejected(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 10)
    assert post(client).status_code == 400


def test_blank_image_reports_unreadable(client):
    if not ocr.tesseract_available():
        pytest.skip("tesseract not installed")
    r = post(client)  # 50x50 white square: no text
    assert r.status_code == 400
    assert "readable text" in r.json()["detail"]


# ---- real OCR end to end ----------------------------------------------------

def _font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _label_png(warning_header="GOVERNMENT WARNING:") -> bytes:
    img = Image.new("RGB", (1100, 720), "white")
    d = ImageDraw.Draw(img)
    d.text((50, 40), "OLD TOM DISTILLERY", fill="black", font=_font(48))
    d.text((50, 120), "Kentucky Straight Bourbon Whiskey", fill="black", font=_font(30))
    d.text((50, 170), "45% Alc./Vol. (90 Proof)", fill="black", font=_font(30))
    d.text((50, 220), "750 mL", fill="black", font=_font(30))
    lines = [
        warning_header + " (1) According to the Surgeon General, women should",
        "not drink alcoholic beverages during pregnancy because of the risk of birth defects.",
        "(2) Consumption of alcoholic beverages impairs your ability to drive a car or",
        "operate machinery, and may cause health problems.",
    ]
    for i, line in enumerate(lines):
        d.text((50, 450 + i * 34), line, fill="black", font=_font(22))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


needs_tesseract = pytest.mark.skipif(not ocr.tesseract_available(), reason="tesseract not installed")


@needs_tesseract
def test_real_ocr_good_label_passes(client):
    body = post(client, data=_label_png()).json()
    assert body["overall"] == "pass", body
    assert body["seconds"] < 5


@needs_tesseract
def test_real_ocr_title_case_warning_fails(client):
    body = post(client, data=_label_png("Government Warning:")).json()
    warning = [r for r in body["results"] if r["field"] == "Government warning"][0]
    assert warning["status"] == "fail"