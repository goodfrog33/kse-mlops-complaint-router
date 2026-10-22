"""Feast definitions: monthly complaint statistics per company.

A month's statistics only exist once the month is over, so each row's event_timestamp is
the first day of the next month. A point-in-time join for a complaint on 2024-07-15 must
return the June row (stamped 2024-07-01), never the July row (stamped 2024-08-01).
"""

from datetime import timedelta

from feast import Entity, FeatureView, Field, FileSource
from feast.types import Float32, Int64

company = Entity(name="company", join_keys=["company"], description="CFPB company name")

company_monthly_source = FileSource(
    name="company_monthly_source",
    path="data/company_monthly.parquet",
    timestamp_field="event_timestamp",
)

company_monthly_stats = FeatureView(
    name="company_monthly_stats",
    entities=[company],
    # A row stays valid for two months; older statistics are treated as missing.
    ttl=timedelta(days=62),
    schema=[
        Field(name="complaints_last_month", dtype=Int64),
        Field(name="credit_reporting_share_last_month", dtype=Float32),
        Field(name="text_share_last_month", dtype=Float32),
    ],
    source=company_monthly_source,
    online=True,
)
