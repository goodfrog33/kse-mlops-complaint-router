from datetime import date

import polars as pl
import pytest

from complaint_router.pipeline import inject_fault
from complaint_router.rules import KeywordRule, MajorityClass
from complaint_router.train import months


def test_majority_class():
    m = MajorityClass().fit(["a", "b", "a"])
    assert m.predict(["x", "y"]) == ["a", "a"]


def test_keyword_rule_first_match_and_default():
    rule = KeywordRule(default="Credit reporting")
    assert rule.predict_one("my escrow account was overcharged") == "Mortgage"
    assert rule.predict_one("nothing relevant here") == "Credit reporting"


def test_months_inclusive_and_cross_year():
    got = months(("2024-11", "2025-02"))
    assert got == [date(2024, 11, 1), date(2024, 12, 1), date(2025, 1, 1), date(2025, 2, 1)]


@pytest.mark.parametrize(
    "fault,column,value",
    [
        ("null_narratives", "consumer_complaint_narrative", None),
        ("unknown_product", "product", "Buy now pay later"),
        ("wrong_month", "date_received", "2019-01-15"),
    ],
)
def test_faults_break_the_batch(fault, column, value):
    df = pl.DataFrame(
        {
            "consumer_complaint_narrative": ["text"],
            "product": ["Mortgage"],
            "date_received": ["2024-06-03"],
        }
    )
    assert inject_fault(df, fault)[column].to_list() == [value]


def test_no_fault_leaves_batch_untouched():
    df = pl.DataFrame({"product": ["Mortgage"]})
    assert inject_fault(df, None).equals(df)
