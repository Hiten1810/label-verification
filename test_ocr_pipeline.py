"""Tests for image preprocessing (tilt correction). Run with:  pytest -v"""
import io

import pytest
from PIL import Image, ImageOps

import ocr
from checkers import Status, overall_status, verify_label
from test_api import _label_png

APP = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "abv": "45",
    "net_contents": "750 mL",
}


def _gray_label() -> Image.Image:
    img = Image.open(io.BytesIO(_label_png())).convert("L")
    return ImageOps.autocontrast(img, cutoff=1)


def _tilted_png(degrees: float) -> bytes:
    img = Image.open(io.BytesIO(_label_png())).rotate(
        degrees, expand=True, resample=Image.BICUBIC, fillcolor="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("degrees", [3, 7, 12, -5, -10])
def test_estimate_skew_finds_the_tilt(degrees):
    tilted = _gray_label().rotate(degrees, expand=True, resample=Image.BICUBIC, fillcolor=255)
    assert abs(ocr.estimate_skew(tilted) + degrees) <= 1.0


def test_straight_image_is_left_alone():
    assert ocr.estimate_skew(_gray_label()) == 0.0


def test_blank_image_is_left_alone():
    assert ocr.estimate_skew(Image.new("L", (600, 400), 255)) == 0.0


needs_tesseract = pytest.mark.skipif(not ocr.tesseract_available(), reason="tesseract not installed")


@needs_tesseract
@pytest.mark.parametrize("degrees", [5, 12, -8])
def test_tilted_label_still_verifies(degrees):
    text = ocr.read_label(_tilted_png(degrees))
    results = verify_label(APP, text)
    assert overall_status(results) == Status.PASS, [r.to_dict() for r in results]