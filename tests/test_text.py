import polars as pl

from complaint_router.text import clean_text, clean_text_expr

SAMPLES = [
    "Call me at 5551234567 or mail john.doe@example.com",
    "On XX/XX/XXXX my   ACCOUNT was closed",
    "  Account 1234 and 123 stay?  ",
]


def test_masks_numbers_and_emails():
    assert clean_text(SAMPLES[0]) == "call me at xxxx or mail xxxx"


def test_masks_cfpb_dates_and_normalises_spaces():
    assert clean_text(SAMPLES[1]) == "on xxxx my account was closed"


def test_short_numbers_survive():
    assert clean_text(SAMPLES[2]) == "account xxxx and 123 stay?"


def test_polars_expression_matches_python_function():
    # Training uses the polars expression, serving will use the function: they must agree.
    got = pl.DataFrame({"t": SAMPLES}).select(clean_text_expr("t"))["t"].to_list()
    assert got == [clean_text(s) for s in SAMPLES]
