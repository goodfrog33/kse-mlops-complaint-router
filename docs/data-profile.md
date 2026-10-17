# Data profile: CFPB Consumer Complaint Database

Profiled on 2026-10-17 by [`scripts/profile_data.py`](../scripts/profile_data.py). All numbers below are in [`data/profile.json`](data/profile.json); a 200-row sample of real complaints is in [`../data/sample/complaints_sample.csv`](../data/sample/complaints_sample.csv).

## Reproduce

```bash
mkdir -p data/raw && curl -L -o data/raw/complaints.csv.zip https://files.consumerfinance.gov/ccdb/complaints.csv.zip && unzip -o data/raw/complaints.csv.zip -d data/raw
```

The text snapshot is the Parquet conversion of [Mouwiya/cfpb-consumer-complaints](https://huggingface.co/datasets/Mouwiya/cfpb-consumer-complaints) (`refs/convert/parquet`, 67 files) saved under `data/raw/hf_snapshot/`. Then:

```bash
uv run python scripts/profile_data.py
```

Lab 2 replaces both manual downloads with the `ingest_ccdb` task and a DVC remote.

## Two sources, and why

| | Text snapshot | Official export |
|---|---|---|
| Taken | 2026-07-06 (Hugging Face mirror of the CFPB export) | 2026-10-17 (files.consumerfinance.gov) |
| Size | 1.0 GB Parquet | 355 MB zip, 5.6 GB CSV |
| Complaints | 16,558,052 | 18,486,121 |
| With narrative | 3,815,251 | **none: the column is gone** |
| Date range | 2011-12-01 → 2026-07-06 | 2011-12 → 2026-10 |

CFPB stopped publishing the `Consumer complaint narrative` field in 2026. The current export has 15 columns (`Date received, Product, Sub-product, Issue, Sub-issue, Company public response, Company, State, ZIP code, Tags, Submitted via, Date sent to company, Company response to consumer, Timely response?, Complaint ID`), and CFPB's field reference no longer lists the narrative.

## Volume and text by year (snapshot)

| Year | Complaints | With narrative | Share |
|---|---:|---:|---:|
| 2015 | 168,273 | 54,723 | 33% |
| 2016 | 191,294 | 77,766 | 41% |
| 2017 | 242,745 | 115,311 | 48% |
| 2018 | 257,132 | 118,393 | 46% |
| 2019 | 277,241 | 124,843 | 45% |
| 2020 | 444,214 | 174,298 | 39% |
| 2021 | 495,939 | 203,570 | 41% |
| 2022 | 800,255 | 337,280 | 42% |
| 2023 | 1,170,765 | 442,373 | 38% |
| 2024 | 2,734,276 | 814,385 | 30% |
| 2025 | 5,443,265 | 1,222,107 | 22% |
| 2026 (to 07-06) | 3,875,331 | 85,162 | 2% |

Narratives start on 2015-03-19. Before that the column is empty.

## The narrative cut-off, month by month

| Month | Complaints | With narrative | Share |
|---|---:|---:|---:|
| 2025-07 | 473,202 | 125,545 | 26.5% |
| 2025-08 | 495,582 | 108,868 | 22.0% |
| 2025-09 | 503,854 | 81,659 | 16.2% |
| 2025-10 | 519,802 | 76,166 | 14.7% |
| 2025-11 | 496,696 | 78,130 | 15.7% |
| 2025-12 | 512,667 | 56,548 | 11.0% |
| 2026-01 | 541,941 | 22,309 | 4.1% |
| 2026-04 | 622,362 | 20,765 | 3.3% |
| 2026-05 | 655,263 | 6,665 | 1.0% |
| 2026-06 | 779,756 | 1,458 | 0.2% |
| 2026-07 (6 days) | 150,777 | 0 | 0.0% |

The official export keeps growing without text: 614,959–685,414 complaints a month from March to September 2026.

## Product taxonomy over time

20 product names, three taxonomy eras (changes in April 2017 and August 2023), mapped onto 10 classes by [`configs/taxonomy_map.csv`](../configs/taxonomy_map.csv).

| CFPB product | Class | First | Last | Complaints |
|---|---|---|---|---:|
| Credit reporting | Credit reporting | 2012-10 | 2017-04 | 140,426 |
| Credit reporting, credit repair services, or other personal consumer reports | Credit reporting | 2017-04 | 2023-08 | 2,163,773 |
| Credit reporting or other personal consumer reports | Credit reporting | 2023-08 | 2026-07 | 11,075,895 |
| Debt collection | Debt collection | 2013-07 | 2026-07 | 1,163,391 |
| Mortgage | Mortgage | 2011-12 | 2026-07 | 455,171 |
| Bank account or service | Checking or savings | 2012-03 | 2017-04 | 86,198 |
| Checking or savings account | Checking or savings | 2017-04 | 2026-07 | 381,902 |
| Credit card | Credit or prepaid card | 2011-12 | 2026-07 | 328,478 |
| Credit card or prepaid card | Credit or prepaid card | 2017-04 | 2023-08 | 206,357 |
| Prepaid card | Credit or prepaid card | 2014-07 | 2026-07 | 22,405 |
| Money transfers | Money transfer and virtual currency | 2013-04 | 2017-04 | 5,354 |
| Virtual currency | Money transfer and virtual currency | 2014-08 | 2017-04 | 18 |
| Money transfer, virtual currency, or money service | Money transfer and virtual currency | 2017-04 | 2026-07 | 185,579 |
| Student loan | Student loan | 2012-03 | 2026-07 | 130,414 |
| Vehicle loan or lease | Vehicle loan or lease | 2017-04 | 2026-07 | 99,357 |
| Consumer Loan | Payday, title and personal loan | 2012-03 | 2017-04 | 31,560 |
| Payday loan | Payday, title and personal loan | 2013-11 | 2017-04 | 5,528 |
| Payday loan, title loan, or personal loan | Payday, title and personal loan | 2017-04 | 2023-08 | 30,622 |
| Payday loan, title loan, personal loan, or advance loan | Payday, title and personal loan | 2023-08 | 2026-07 | 34,565 |
| Debt or credit management | Debt or credit management | 2023-08 | 2026-07 | 9,726 |
| Other financial service, Other, Non-financial, blank | unmapped → manual triage | | | 1,333 |

`Consumer Loan` (pre-2017) also held some vehicle loans. It sits outside the training window, so the coarse mapping does not affect the model.

## Training window (narratives, 2023-04 → 2025-03)

| Split | Months | Narratives |
|---|---|---:|
| Train | 2023-04 → 2025-03 | 1,539,100 |
| Validation | 2025-04 → 2025-06 | 324,620 |
| Test | 2025-07 → 2025-09 | 316,072 |

| Class | Narratives | Share |
|---|---:|---:|
| Credit reporting | 1,141,303 | 74.2% |
| Debt collection | 118,514 | 7.7% |
| Credit or prepaid card | 77,149 | 5.0% |
| Checking or savings | 68,365 | 4.4% |
| Money transfer and virtual currency | 64,632 | 4.2% |
| Mortgage | 24,096 | 1.6% |
| Student loan | 17,190 | 1.1% |
| Vehicle loan or lease | 15,172 | 1.0% |
| Payday, title and personal loan | 9,960 | 0.6% |
| Debt or credit management | 2,719 | 0.2% |

Credit reporting's share of narratives by year: 19% (2016), 41% (2018), 69% (2022), 77% (2024), 75% (2025).

**Narrative length:** median 644 characters / 107 words; p10 184 / 32; p90 1,990 / 342; p99 5,782 / 990. **Redaction:** 62% of narratives contain CFPB's `XXXX` mask.

**Post-routing fields (excluded as leakage):** company responses in the window are 51% "Closed with explanation", 48% "Closed with non-monetary relief", 0.9% "Closed with monetary relief". `Date sent to company` equals `Date received` for the median complaint (p99: 7 days).
