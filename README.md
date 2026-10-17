# complaint-router

KSE MLOps course project: one ML system built over five labs.

## The problem in five sentences

A complaint intake desk receives about ten thousand free-text consumer complaints a day about financial products, and each one has to reach the team that owns that product. Misrouted complaints wait days to be reassigned while the regulatory response clock keeps running. complaint-router predicts the product queue from the complaint text on arrival, auto-routes the confident cases and gives triage agents a suggestion for the rest. It is trained on the public CFPB Consumer Complaint Database, which is re-published daily and whose product taxonomy, class mix and vocabulary drift over time. The system therefore ingests, versions, retrains, serves and monitors continuously, instead of being a one-off notebook.

| | |
|---|---|
| Design document | [`docs/design.md`](docs/design.md) |
| Presentation | [`docs/presentation/complaint-router-lab1.pptx`](docs/presentation/complaint-router-lab1.pptx) ([PDF](docs/presentation/complaint-router-lab1.pdf)) |
| Data profile | [`docs/data-profile.md`](docs/data-profile.md), produced by [`scripts/profile_data.py`](scripts/profile_data.py) |
| Data sample | [`data/sample/complaints_sample.csv`](data/sample/complaints_sample.csv): 200 real complaints, 20 per class |
| Label map | [`configs/taxonomy_map.csv`](configs/taxonomy_map.csv): 20 CFPB product names → 10 classes |

**Data note.** CFPB stopped publishing complaint text in 2026. Text comes from a July 2026 snapshot (3.8 M complaints with narratives, 2015–2025) that is replayed month by month; the daily official export (no text) drives freshness and volume checks. Details in design.md §5.2.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and `make`. uv downloads Python 3.12 if needed.

```bash
make setup   # installs the pinned environment from uv.lock and the pre-commit hook
make lint    # runs ruff, the same check CI runs on every push
```

## Maturity scorecard

| Property | Evidence a reviewer checks | Added in |
|---|---|---|
| Repository reproducible from a clean clone | CI is green; `make setup` and `make lint` succeed on a fresh clone | Lab 1 |
| Design document exists and is current | `docs/design.md` follows the template, every section filled or marked as an assumption | Lab 1 |
