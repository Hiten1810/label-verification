"""Run with:  pytest -v"""
from checkers import (
    GOVERNMENT_WARNING, Status, check_abv, check_net_contents, check_text_field,
    check_warning, overall_status, verify_label,
)

# Real OCR output from the first test label (line breaks included).
OCR_GOOD = """OLD TOM DISTILLERY

Kentucky Straight Bourbon Whiskey
45% Alc./Vol. (90 Proof)
750 mL

GOVERNMENT WARNING: (1) According to the Surgeon General, women should
not drink alcoholic beverages during pregnancy because of the risk of birth defects.
(2) Consumption of alcoholic beverages impairs your ability to drive a car or
operate machinery, and may cause health problems."""

APP = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "abv": 45,
    "net_contents": "750 mL",
}


def by_field(results):
    return {r.field: r for r in results}


def test_good_label_passes_everything():
    results = verify_label(APP, OCR_GOOD)
    assert all(r.status == Status.PASS for r in results), [r.to_dict() for r in results]
    assert overall_status(results) == Status.PASS


# ---- brand / class ----------------------------------------------------------

def test_brand_case_difference_needs_review():
    r = check_text_field("Brand name", "Old Tom Distillery", OCR_GOOD)
    assert r.status == Status.REVIEW


def test_application_missing_a_word_on_the_label_line_needs_review():
    # label says OLD TOM DISTILLERY, application only says OLD TOM
    r = check_text_field("Brand name", "OLD TOM", OCR_GOOD)
    assert r.status == Status.REVIEW
    assert r.found == "OLD TOM DISTILLERY"
    assert "OLD TOM DISTILLERY" in r.message


def test_missing_word_and_case_difference_both_reported():
    r = check_text_field("Brand name", "OlD TOM", OCR_GOOD)
    assert r.status == Status.REVIEW
    assert r.found == "OLD TOM DISTILLERY"
    assert "Capitalization differs" in r.message


def test_wrapped_class_type_with_no_extra_words_still_passes():
    text = "Kentucky Straight\nBourbon Whiskey\n750 mL"
    r = check_text_field("Class/type", "Kentucky Straight Bourbon Whiskey", text)
    assert r.status == Status.PASS


def test_truncated_value_is_not_a_pass():
    # "Whiske" is only part of the word "Whiskey" - must not show green
    r = check_text_field("Class/type", "Kentucky Straight Bourbon Whiske", OCR_GOOD)
    assert r.status == Status.REVIEW
    assert r.found == "Kentucky Straight Bourbon Whiskey"


def test_extra_trailing_letters_not_a_pass():
    r = check_text_field("Brand name", "OLD TOM DISTILLERYS", OCR_GOOD)
    assert r.status != Status.PASS


def test_letter_swap_i_for_l_fails_with_closest_text():
    r = check_text_field("Brand name", "OID TOM DISTILLERY", OCR_GOOD)
    assert r.status in (Status.REVIEW, Status.FAIL)
    assert r.found == "OLD TOM DISTILLERY"


def test_review_shows_text_as_printed_on_label():
    r = check_text_field("Brand name", "Old Tom Distillery", OCR_GOOD)
    assert r.found == "OLD TOM DISTILLERY"


def test_stones_throw_case_and_apostrophe_needs_review():
    r = check_text_field("Brand name", "Stone's Throw", "STONES THROW\n750 mL")
    assert r.status == Status.REVIEW


def test_brand_ocr_typo_needs_review():
    r = check_text_field("Brand name", "OLD TOM DISTILLERY", OCR_GOOD.replace("DISTILLERY", "DISTILLEFY"))
    assert r.status == Status.REVIEW


def test_wrong_brand_fails():
    r = check_text_field("Brand name", "JACK RIVER SPIRITS", OCR_GOOD)
    assert r.status == Status.FAIL


def test_class_type_wrapped_across_lines_passes():
    text = "Kentucky Straight\nBourbon Whiskey\n45% Alc./Vol."
    r = check_text_field("Class/type", "Kentucky Straight Bourbon Whiskey", text)
    assert r.status == Status.PASS


# ---- ABV --------------------------------------------------------------------

def test_abv_wrong_fails():
    assert check_abv(40, OCR_GOOD).status == Status.FAIL


def test_abv_accepts_string_with_percent_sign():
    assert check_abv("45%", OCR_GOOD).status == Status.PASS


def test_abv_alternate_phrasings():
    for label in ["ALC. 45% BY VOL.", "45% ABV", "Alc 45% by vol", "45 % alc/vol"]:
        assert check_abv(45, label).status == Status.PASS, label


def test_proof_inconsistent_needs_review():
    assert check_abv(45, "45% Alc./Vol. (80 Proof)").status == Status.REVIEW


def test_abv_missing_fails():
    assert check_abv(45, "OLD TOM DISTILLERY 750 mL").status == Status.FAIL


# ---- net contents -----------------------------------------------------------

def test_net_contents_unit_equivalents():
    for label in ["750 mL", "75 cL", "0.75 L", "750ML", "0,75 liters"]:
        assert check_net_contents("750 mL", label).status == Status.PASS, label


def test_net_contents_fl_oz_close_enough():
    assert check_net_contents("355 mL", "12 FL. OZ.").status == Status.PASS


def test_net_contents_wrong_fails():
    assert check_net_contents("750 mL", "1 L").status == Status.FAIL


def test_net_contents_missing_fails():
    assert check_net_contents("750 mL", "OLD TOM DISTILLERY").status == Status.FAIL


# ---- government warning -----------------------------------------------------

def test_warning_title_case_fails():
    bad = OCR_GOOD.replace("GOVERNMENT WARNING:", "Government Warning:")
    r = check_warning(bad)
    assert r.status == Status.FAIL
    assert "ALL CAPS" in r.message


def test_warning_missing_colon_fails():
    r = check_warning(OCR_GOOD.replace("GOVERNMENT WARNING:", "GOVERNMENT WARNING"))
    assert r.status == Status.FAIL


def test_warning_reworded_fails():
    bad = OCR_GOOD.replace("because of the risk of birth defects", "because it may harm the baby")
    assert bad != OCR_GOOD  # guard: make sure the edit actually applied
    assert check_warning(bad).status == Status.FAIL


def test_warning_truncated_fails():
    bad = OCR_GOOD.split("(2)")[0]
    assert check_warning(bad).status == Status.FAIL


def test_warning_single_ocr_typo_needs_review():
    bad = OCR_GOOD.replace("machinery", "machlnery")
    assert check_warning(bad).status == Status.REVIEW


def test_one_character_slip_in_list_marker_needs_review_not_fail():
    # A tilted photo can turn "(1)" into "(A)"; that's an OCR slip, not rewording.
    r = check_warning(OCR_GOOD.replace("(1)", "(A)"))
    assert r.status == Status.REVIEW


def test_warning_absent_fails():
    assert check_warning("OLD TOM DISTILLERY\n750 mL").status == Status.FAIL


def test_warning_with_trailing_label_text_still_passes():
    r = check_warning(OCR_GOOD + "\nDistilled and bottled by Old Tom Distillery, Bardstown, KY")
    assert r.status == Status.PASS


def test_warning_constant_has_statutory_header():
    assert GOVERNMENT_WARNING.startswith("GOVERNMENT WARNING: (1)")


# ---- overall ----------------------------------------------------------------

def test_overall_fail_beats_review():
    results = verify_label({**APP, "brand_name": "Old Tom Distillery", "abv": 40}, OCR_GOOD)
    assert overall_status(results) == Status.FAIL