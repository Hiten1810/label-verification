"""
generate_samples.py - make test labels: one good, several deliberately wrong,
and several poor-quality "photos".

Run:   python generate_samples.py
Output: ./samples/*.png and *.jpg  (plus blank.png and notes.txt for error tests)
"""
import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

OUT = "samples"
W, H, MARGIN = 1100, 720, 50

BRAND = "OLD TOM DISTILLERY"
CLASS_TYPE = "Kentucky Straight Bourbon Whiskey"
ABV_LINE = "45% Alc./Vol. (90 Proof)"
NET = "750 mL"
HEADER = "GOVERNMENT WARNING:"
BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car "
    "or operate machinery, and may cause health problems."
)

REGULAR = ["arial.ttf", "DejaVuSans.ttf"]
BOLD = ["arialbd.ttf", "DejaVuSans-Bold.ttf"]


def load_font(candidates, size):
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    print(f"WARNING: none of {candidates} found; using default font")
    return ImageFont.load_default(size=size)


def wrap(draw, text, font, max_width, first_line_offset=0):
    lines, current, width = [], "", max_width - first_line_offset
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            lines.append(current)
            current, width = word, max_width
    lines.append(current)
    return lines


def make_label(abv_line=ABV_LINE, header=HEADER, body=BODY, with_warning=True) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((MARGIN, 40), BRAND, fill="black", font=load_font(REGULAR, 48))
    body_font = load_font(REGULAR, 30)
    d.text((MARGIN, 120), CLASS_TYPE, fill="black", font=body_font)
    d.text((MARGIN, 170), abv_line, fill="black", font=body_font)
    d.text((MARGIN, 220), NET, fill="black", font=body_font)

    if with_warning:
        bold, reg = load_font(BOLD, 22), load_font(REGULAR, 22)
        y = 450
        d.text((MARGIN, y), header, fill="black", font=bold)
        offset = d.textlength(header + " ", font=bold)
        lines = wrap(d, body, reg, W - 2 * MARGIN, first_line_offset=offset)
        d.text((MARGIN + offset, y), lines[0], fill="black", font=reg)
        for line in lines[1:]:
            y += 34
            d.text((MARGIN, y), line, fill="black", font=reg)
    return img


def add_glare(img: Image.Image) -> Image.Image:
    """A bright blurred blob over the warning, like a flash reflecting off glass."""
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse((450, 380, 1000, 640), fill=235)
    mask = mask.filter(ImageFilter.GaussianBlur(60))
    return Image.composite(Image.new("RGB", img.size, "white"), img, mask)


def main():
    os.makedirs(OUT, exist_ok=True)
    save = lambda im, name, **kw: im.save(os.path.join(OUT, name), **kw)

    good = make_label()
    save(good, "good.png")

    # ---- deliberately wrong labels (use with the GOOD application values) ----
    save(make_label(header="Government Warning:"), "warning_titlecase.png")
    save(make_label(header="GOVERNMENT WARNING"), "warning_no_colon.png")
    save(make_label(body=BODY.replace("because of the risk of birth defects",
                                      "because it may harm the baby")), "warning_reworded.png")
    save(make_label(body=BODY.replace("machinery", "machlnery")), "warning_one_typo.png")
    save(make_label(with_warning=False), "warning_missing.png")
    save(make_label(abv_line="45% Alc./Vol. (80 Proof)"), "proof_mismatch.png")
    save(make_label(abv_line="40% Alc./Vol. (80 Proof)"), "label_says_40.png")

    # ---- poor-quality "photos" of the good label ----
    save(good.rotate(5, expand=True, resample=Image.BICUBIC, fillcolor="white"), "photo_rotated_5deg.png")
    save(good.rotate(12, expand=True, resample=Image.BICUBIC, fillcolor="white"), "photo_rotated_12deg.png")
    save(good.filter(ImageFilter.GaussianBlur(1.5)), "photo_blurred.png")
    save(ImageEnhance.Brightness(ImageEnhance.Contrast(good).enhance(0.35)).enhance(0.8),
         "photo_low_contrast.png")
    save(good, "photo_jpeg_low_quality.jpg", quality=12)
    save(add_glare(good), "photo_glare.png")
    big = good.resize((4000, int(H * 4000 / W)), Image.LANCZOS)
    save(big, "photo_large_4000px.jpg", quality=90)

    # ---- files for error-handling tests ----
    save(Image.new("RGB", (600, 400), "white"), "blank.png")
    with open(os.path.join(OUT, "notes.txt"), "w") as f:
        f.write("This is not an image.\n")

    print(f"Wrote {len(os.listdir(OUT))} files to ./{OUT}/")


if __name__ == "__main__":
    main()