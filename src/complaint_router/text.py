"""Text preprocessing shared by the pipeline, training and (Lab 4) serving.

Training reads narratives that CFPB has already redacted with ``XXXX``. Serving will see
raw text, so the same masking is applied everywhere to keep both paths identical.
"""

import re

import polars as pl

EMAIL = r"[\w.+-]+@[\w-]+\.[\w.-]+"
LONG_NUMBER = r"\d{4,}"
MASKED_DATE = r"XX/XX/(?:XXXX|\d{4}|year>?)"
SPACES = r"\s+"


def clean_text(text: str) -> str:
    """Mask e-mails and long digit runs like CFPB does, normalise whitespace, lowercase."""
    text = re.sub(EMAIL, "XXXX", text)
    text = re.sub(LONG_NUMBER, "XXXX", text)
    text = re.sub(MASKED_DATE, "XXXX", text)
    return re.sub(SPACES, " ", text).strip().lower()


def clean_text_expr(col: str) -> pl.Expr:
    """The same transformation as ``clean_text``, as a polars expression."""
    return (
        pl.col(col)
        .str.replace_all(EMAIL, "XXXX")
        .str.replace_all(LONG_NUMBER, "XXXX")
        .str.replace_all(MASKED_DATE, "XXXX")
        .str.replace_all(SPACES, " ")
        .str.strip_chars()
        .str.to_lowercase()
    )
