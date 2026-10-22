"""Rule baselines (design.md 5.3): the majority class and a keyword rule per product."""

import re
from collections import Counter

# Ordered: the first product whose pattern matches wins.
KEYWORDS: list[tuple[str, str]] = [
    ("Mortgage", r"mortgage|escrow|foreclos|refinanc|heloc|loan servicer"),
    ("Student loan", r"student loan|navient|nelnet|mohela|fafsa|pslf|loan forgiveness"),
    ("Vehicle loan or lease", r"auto loan|car loan|vehicle|repossess|\blease\b"),
    ("Payday title and personal loan", r"payday|title loan|personal loan|installment loan"),
    (
        "Money transfer and virtual currency",
        r"zelle|venmo|paypal|cash app|wire transfer|western union|money transfer|crypto|bitcoin",
    ),
    ("Debt or credit management", r"credit repair|debt relief|debt settlement|credit counsel"),
    ("Debt collection", r"debt collect|collection agency|collector|collections"),
    ("Credit or prepaid card", r"credit card|annual fee|card issuer|\bapr\b|prepaid card"),
    ("Checking or savings", r"checking|savings account|overdraft|\batm\b|bank account"),
    ("Credit reporting", r"credit report|equifax|experian|transunion|credit score|inquir"),
]
_COMPILED = [(label, re.compile(pattern)) for label, pattern in KEYWORDS]


class MajorityClass:
    """Predicts the most frequent training label for every input."""

    def fit(self, labels: list[str]) -> "MajorityClass":
        self.label_ = Counter(labels).most_common(1)[0][0]
        return self

    def predict(self, texts: list[str]) -> list[str]:
        return [self.label_] * len(texts)


class KeywordRule:
    """First keyword match wins; no match falls back to the given default label."""

    def __init__(self, default: str):
        self.default = default

    def predict_one(self, text: str) -> str:
        for label, pattern in _COMPILED:
            if pattern.search(text):
                return label
        return self.default

    def predict(self, texts: list[str]) -> list[str]:
        return [self.predict_one(t) for t in texts]
