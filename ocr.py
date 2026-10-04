"""
ocr.py - read text from a label image, fully offline (Tesseract).

Nothing here talks to the network. Tesseract runs as a local process.
"""
import io
import os
import shutil
import sys

import pytesseract
from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError

MAX_LONG_SIDE = 3000   # downscale very large photos; keeps OCR fast
MIN_TEXT_CHARS = 15    # fewer characters than this = we effectively read nothing

WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


class UnreadableImage(Exception):
    """The upload isn't an image, or we couldn't find readable text in it."""


def configure_tesseract() -> None:
    """Find tesseract: TESSERACT_CMD env var, else PATH, else the Windows default."""
    cmd = os.environ.get("TESSERACT_CMD")
    if not cmd and sys.platform == "win32" and not shutil.which("tesseract"):
        if os.path.exists(WINDOWS_DEFAULT):
            cmd = WINDOWS_DEFAULT
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def estimate_skew(gray: Image.Image, max_angle: float = 15.0) -> float:
    """Angle (degrees, PIL convention) to rotate `gray` by so its text lines run level.

    Projection-profile method: when text lines are perfectly horizontal, the row-by-row
    ink totals alternate sharply between text rows and gaps (high variance). We try
    angles and keep the one with the highest variance. Pure Pillow, ~50-100 ms.
    Returns 0.0 when the image is already straight or has no clear text lines.
    """
    small = gray.copy()
    small.thumbnail((700, 700))
    small = ImageOps.invert(small).point(lambda v: 255 if v > 90 else 0)   # text -> white

    def score(angle: float) -> float:
        rot = small.rotate(angle, resample=Image.BILINEAR, fillcolor=0)
        profile = rot.resize((1, rot.height), Image.BOX)                  # ink per row
        return ImageStat.Stat(profile).var[0]

    coarse = max((a * 1.0 for a in range(-int(max_angle), int(max_angle) + 1)), key=score)
    best = max((coarse + d * 0.25 for d in range(-4, 5)), key=score)
    if abs(best) < 0.4 or score(best) <= 1.05 * score(0.0):   # no clear improvement -> leave alone
        return 0.0
    return best


def preprocess(img: Image.Image) -> Image.Image:
    """Cheap cleanup that helps real-world photos: rotation flag, grayscale, contrast, tilt."""
    img = ImageOps.exif_transpose(img)          # phone photos: respect rotation flag
    img = img.convert("L")                       # grayscale
    img = ImageOps.autocontrast(img, cutoff=1)   # help dim / low-contrast photos
    long_side = max(img.size)
    if long_side > MAX_LONG_SIDE:
        scale = MAX_LONG_SIDE / long_side
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    angle = estimate_skew(img)                   # straighten tilted photos
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    return img


def read_label(data: bytes) -> str:
    """Image bytes in, OCR text out. Raises UnreadableImage with a plain-English message."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise UnreadableImage(
            "That file isn't an image we can open. Please upload a PNG or JPG of the label."
        )
    text = pytesseract.image_to_string(preprocess(img))
    if len(text.strip()) < MIN_TEXT_CHARS:
        raise UnreadableImage(
            "We couldn't find readable text on this label. Please upload a clearer, "
            "well-lit, straight-on image."
        )
    return text


def warm_up() -> None:
    """Run one throwaway OCR so the first real request isn't slow (cold start)."""
    pytesseract.image_to_string(Image.new("L", (200, 60), 255))


# Locate Tesseract once, when this module is first imported, so every entry point
# (the web app, the tests, ad-hoc scripts) finds it - including the Windows default
# install folder, which is usually not on PATH.
configure_tesseract()