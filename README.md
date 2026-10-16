# complaint-router

KSE MLOps course project: one ML system built over five labs.

## The problem in five sentences

A complaint intake desk receives about ten thousand free-text consumer complaints a day about financial products, and each one has to reach the team that owns that product. Misrouted complaints wait days to be reassigned while the regulatory response clock keeps running. complaint-router predicts the product queue from the complaint text on arrival, auto-routes the confident cases and gives triage agents a suggestion for the rest. It is trained on the public CFPB Consumer Complaint Database, which is re-published daily and whose product taxonomy, class mix and vocabulary drift over time. The system therefore ingests, versions, retrains, serves and monitors continuously, instead of being a one-off notebook.

The full design is in [`docs/design.md`](docs/design.md).

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
