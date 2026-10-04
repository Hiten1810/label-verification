"""
checkers.py - compare application data against text OCR'd from a label.

Each check returns a FieldResult with one of three statuses:
    PASS   - matches
    REVIEW - probably fine, but a human should look (case-only difference,
             likely OCR misread, or something software can't verify)
    FAIL   - does not match / not found

Usage:
    results = verify_label(
        {"brand_name": "OLD TOM DISTILLERY",
         "class_type": "Kentucky Straight Bourbon Whiskey",
         "abv": 45,
         "net_contents": "750 mL"},
        ocr_text,
    )
    print(overall_status(results))
"""
import math
import re
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from enum import Enum

from rapidfuzz import fuzz

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# Statutory text, 27 CFR 16.21. Compared word for word.
GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
    "not drink alcoholic beverages during pregnancy because of the risk of "
    "birth defects. (2) Consumption of alcoholic beverages impairs your "
    "ability to drive a car or operate machinery, and may cause health problems."
)
WARNING_HEADER = "GOVERNMENT WARNING:"

FUZZY_REVIEW_THRESHOLD = 90   # text fields: fuzzy score that earns "review" instead of "fail"
WORD_OCR_SIMILARITY = 70      # warning: a changed word this similar is probably an OCR misread
ABV_TOLERANCE = 0.05          # percentage points
NET_CONTENTS_REL_TOL = 0.01   # 1% (covers 12 fl oz vs 355 mL)


class Status(str, Enum):
    PASS = "pass"
    REVIEW = "review"
    FAIL = "fail"


@dataclass
class FieldResult:
    field: str
    status: Status
    expected: str
    found: str
    message: str

    def to_dict(self):
        d = asdict(self)
        d["status"] = self.status.value
        return d


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def collapse_ws(text: str) -> str:
    """Collapse all whitespace/newlines to single spaces (OCR line breaks are noise)."""
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace. "STONE'S THROW" -> "stones throw"."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"[^a-z0-9\s]", "", text.lower())
    return collapse_ws(text)


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


# --------------------------------------------------------------------------
# Brand name / class-type (fuzzy text fields)
# --------------------------------------------------------------------------

def _label_text_for(expected_flat: str, ocr_flat: str) -> str:
    """The text on the label that best lines up with `expected`, exactly as printed
    (original capitalization and punctuation), so agents see what the label really says."""
    lowered = ocr_flat.lower()
    if expected_flat and len(lowered) == len(ocr_flat):
        a = fuzz.partial_ratio_alignment(expected_flat.lower(), lowered)
        if a:
            s, e = a.dest_start, a.dest_end
            # If the window cuts a word in half, widen it to whole words
            while 0 < s < len(ocr_flat) and ocr_flat[s].isalnum() and ocr_flat[s - 1].isalnum():
                s -= 1
            while 0 < e < len(ocr_flat) and ocr_flat[e].isalnum() and ocr_flat[e - 1].isalnum():
                e += 1
            return ocr_flat[s:e].strip()
    return ""


def _contains_whole(haystack: str, needle: str) -> bool:
    """True if `needle` appears in `haystack` and isn't just part of a longer word.
    ("Whiske" is NOT found in "Whiskey".)"""
    return bool(re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack))


def _label_line(exp_n: str, ocr_text: str):
    """Find the label line(s) holding the expected text (a wrapped value may span up to
    3 lines). Returns (line as printed, extra words on that line) or None."""
    if not exp_n:
        return None
    lines = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]
    for size in (1, 2, 3):
        for i in range(len(lines) - size + 1):
            window = " ".join(lines[i:i + size])
            wn = normalize(window)
            if _contains_whole(wn, exp_n):
                rest = re.sub(r"(?<!\w)" + re.escape(exp_n) + r"(?!\w)", " ", wn, count=1)
                return window, rest.split()
    return None


def check_text_field(field: str, expected: str, ocr_text: str) -> FieldResult:
    expected_flat = collapse_ws(expected)
    ocr_flat = collapse_ws(ocr_text)

    exp_n, ocr_n = normalize(expected), normalize(ocr_text)
    on_label = _label_text_for(expected_flat, ocr_flat)
    line_hit = _label_line(exp_n, ocr_text)   # (line as printed, extra words) or None

    # 1. Exact (case- and punctuation-sensitive) whole-word match
    if expected_flat and _contains_whole(ocr_flat, expected_flat):
        if line_hit and line_hit[1]:
            return FieldResult(
                field, Status.REVIEW, expected, line_hit[0],
                f"The label line reads \"{line_hit[0]}\", which has more than the "
                f"application's \"{expected_flat}\". Please confirm the full name.",
            )
        return FieldResult(field, Status.PASS, expected, expected_flat, "Matches the label.")

    # 2. Same words, different capitalization/punctuation -> human judgment (Dave's STONE'S THROW case)
    if exp_n and _contains_whole(ocr_n, exp_n):
        msg = ("Same text but capitalization or punctuation differs from the application. "
               "Likely the same, please confirm.")
        if line_hit and line_hit[1]:
            msg = (f"Capitalization differs, and the label line reads \"{line_hit[0]}\", which has "
                   f"more than the application's \"{expected_flat}\". Please confirm.")
        return FieldResult(field, Status.REVIEW, expected,
                           (line_hit[0] if line_hit else on_label) or expected, msg)

    # 3. Fuzzy match -> probably an OCR misread
    if exp_n:
        align = fuzz.partial_ratio_alignment(exp_n, ocr_n)
        if align and align.score >= FUZZY_REVIEW_THRESHOLD:
            close = on_label or ocr_n[align.dest_start:align.dest_end]
            return FieldResult(
                field, Status.REVIEW, expected, close,
                f"Close match ({align.score:.0f}%). The image text may have been misread.",
            )
        closest = ""
        if align and align.score >= 60:
            closest = on_label or ocr_n[align.dest_start:align.dest_end]
        msg = "Not found on the label."
        if closest:
            msg += f" Closest text: \"{closest}\"."
        return FieldResult(field, Status.FAIL, expected, closest, msg)

    return FieldResult(field, Status.FAIL, expected, "", "No application value to compare.")


# --------------------------------------------------------------------------
# Alcohol content
# --------------------------------------------------------------------------

_ABV_PATTERNS = [
    r"(\d{1,3}(?:[.,]\d+)?)\s*%\s*(?:alc|abv|alcohol|by\s*vol)",
    r"(?:alc(?:ohol)?\.?|abv)\s*(?:by\s*vol(?:ume)?\.?)?\s*:?\s*(\d{1,3}(?:[.,]\d+)?)\s*%",
    r"(\d{1,3}(?:[.,]\d+)?)\s*%",  # last resort: any percentage
]
_PROOF_PATTERN = r"(\d{1,3}(?:[.,]\d+)?)\s*proof"


def extract_abv(text: str):
    for pattern in _ABV_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return _to_float(m.group(1))
    return None


def extract_proof(text: str):
    m = re.search(_PROOF_PATTERN, text, re.IGNORECASE)
    return _to_float(m.group(1)) if m else None


def check_abv(expected, ocr_text: str) -> FieldResult:
    m = re.search(r"\d+(?:[.,]\d+)?", str(expected))
    if not m:
        return FieldResult("Alcohol content", Status.REVIEW, str(expected), "",
                           "Couldn't read a number from the application value.")
    exp_val = _to_float(m.group(0))
    found = extract_abv(ocr_text)
    if found is None:
        return FieldResult("Alcohol content", Status.FAIL, f"{exp_val:g}%", "",
                           "No alcohol percentage found on the label.")
    if abs(found - exp_val) > ABV_TOLERANCE:
        return FieldResult("Alcohol content", Status.FAIL, f"{exp_val:g}%", f"{found:g}%",
                           f"Label says {found:g}% but the application says {exp_val:g}%.")
    proof = extract_proof(ocr_text)
    if proof is not None and abs(proof - 2 * found) > 0.5:
        return FieldResult("Alcohol content", Status.REVIEW, f"{exp_val:g}%", f"{found:g}%",
                           f"Percentage matches, but the label's proof ({proof:g}) "
                           f"isn't twice the percentage ({2 * found:g}).")
    return FieldResult("Alcohol content", Status.PASS, f"{exp_val:g}%", f"{found:g}%",
                       "Matches the label.")


# --------------------------------------------------------------------------
# Net contents
# --------------------------------------------------------------------------

_NET_PATTERN = (
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(milliliters?|millilitres?|ml|centiliters?|centilitres?|cl|liters?|litres?|"
    r"fl\.?\s*oz\.?|ounces?|oz|l)(?![a-z])"
)


def _unit_to_ml(unit: str):
    u = re.sub(r"[\s.]", "", unit.lower())
    if u.startswith(("milli", "ml")):
        return 1.0
    if u.startswith(("centi", "cl")):
        return 10.0
    if u in ("l", "liter", "liters", "litre", "litres"):
        return 1000.0
    if u.startswith(("floz", "oz", "ounce")):
        return 29.5735
    return None


def extract_volumes_ml(text: str):
    """All volume measurements in the text, converted to mL."""
    out = []
    for m in re.finditer(_NET_PATTERN, text, re.IGNORECASE):
        factor = _unit_to_ml(m.group(2))
        if factor:
            out.append((m.group(0).strip(), _to_float(m.group(1)) * factor))
    return out


def check_net_contents(expected: str, ocr_text: str) -> FieldResult:
    exp = extract_volumes_ml(str(expected))
    if not exp:
        return FieldResult("Net contents", Status.REVIEW, str(expected), "",
                           "Couldn't read a volume from the application value.")
    exp_ml = exp[0][1]
    found = extract_volumes_ml(ocr_text)
    if not found:
        return FieldResult("Net contents", Status.FAIL, str(expected), "",
                           "No net contents found on the label.")
    for raw, ml in found:
        if math.isclose(ml, exp_ml, rel_tol=NET_CONTENTS_REL_TOL):
            return FieldResult("Net contents", Status.PASS, str(expected), raw,
                               "Matches the label.")
    raws = ", ".join(r for r, _ in found)
    return FieldResult("Net contents", Status.FAIL, str(expected), raws,
                       f"Label shows {raws}, which doesn't equal {expected}.")


# --------------------------------------------------------------------------
# Government warning (strict)
# --------------------------------------------------------------------------

def _looks_like_ocr_slip(expected: str, found: str) -> bool:
    """True if `found` is plausibly `expected` misread by OCR (one or two wrong characters).
    Short tokens like "(1)" have little to compare, so they get a lower bar."""
    a, b = expected.lower(), found.lower()
    limit = 60 if max(len(a), len(b)) <= 4 else WORD_OCR_SIMILARITY
    return fuzz.ratio(a, b) >= limit


def check_warning(ocr_text: str) -> FieldResult:
    field = "Government warning"
    flat = collapse_ws(ocr_text)

    m = re.search(r"government\s+warning\s*:?", flat, re.IGNORECASE)
    if not m:
        return FieldResult(field, Status.FAIL, WARNING_HEADER, "",
                           "Government warning not found. If the image is blurry, "
                           "ask for a clearer one.")

    header = m.group(0)
    if header != WARNING_HEADER:
        if header.upper() == header:
            why = "is missing its colon"
        else:
            why = "must be in ALL CAPS"
        return FieldResult(field, Status.FAIL, WARNING_HEADER, header,
                           f"The \"GOVERNMENT WARNING:\" heading {why}. Found: \"{header}\".")

    exp_words = GOVERNMENT_WARNING.split()
    found_words = flat[m.start():].split()[: len(exp_words) + 10]

    sm = SequenceMatcher(a=exp_words, b=found_words, autojunk=False)
    diffs, only_ocr_like = [], True
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert" and j2 == len(found_words):
            continue  # extra label text after the warning - not part of it
        exp_part, found_part = " ".join(exp_words[i1:i2]), " ".join(found_words[j1:j2])
        if tag == "replace":
            diffs.append(f"expected \"{exp_part}\" but found \"{found_part}\"")
            same_size = (i2 - i1) == (j2 - j1)
            similar = same_size and all(
                _looks_like_ocr_slip(a, b)
                for a, b in zip(exp_words[i1:i2], found_words[j1:j2])
            )
            only_ocr_like &= similar
        elif tag == "delete":
            diffs.append(f"missing \"{exp_part}\"")
            only_ocr_like = False
        else:
            diffs.append(f"extra words \"{found_part}\"")
            only_ocr_like = False

    note = " Bold type can't be checked automatically - confirm the heading is bold."
    found_text = " ".join(found_words)
    if not diffs:
        return FieldResult(field, Status.PASS, GOVERNMENT_WARNING, found_text,
                           "Wording matches the required text exactly." + note)
    detail = "; ".join(diffs[:5])
    if only_ocr_like:
        return FieldResult(field, Status.REVIEW, GOVERNMENT_WARNING, found_text,
                           "Wording is almost identical - possibly an OCR misread: "
                           + detail + ". If the photo is tilted, blurry or has glare, "
                           "a straighter, clearer image may help.")
    return FieldResult(field, Status.FAIL, GOVERNMENT_WARNING, found_text,
                       "Wording differs from the required text: " + detail + ".")


# --------------------------------------------------------------------------
# Top level
# --------------------------------------------------------------------------

def verify_label(application: dict, ocr_text: str):
    """Run every check. `application` keys: brand_name, class_type, abv, net_contents."""
    return [
        check_text_field("Brand name", application["brand_name"], ocr_text),
        check_text_field("Class/type", application["class_type"], ocr_text),
        check_abv(application["abv"], ocr_text),
        check_net_contents(application["net_contents"], ocr_text),
        check_warning(ocr_text),
    ]


def overall_status(results) -> Status:
    statuses = {r.status for r in results}
    if Status.FAIL in statuses:
        return Status.FAIL
    if Status.REVIEW in statuses:
        return Status.REVIEW
    return Status.PASS