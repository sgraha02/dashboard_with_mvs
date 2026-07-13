-- NOTE: Run DESCRIBE TABLE ${var.catalog}.${var.schema}.md_national_days_to_delivery_preprocessed
-- first to confirm column names match (Total, Ontime, Late, ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly).

CREATE OR REPLACE MATERIALIZED VIEW ${var.catalog}.${var.schema}.mv_national_days_to_delivery_base_agg
COMMENT 'Pre-aggregated base grain for National days-to-delivery dashboard. One row per ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly. Filters to GA, PM, PK, EX product codes.'
CLUSTER BY AUTO
SCHEDULE EVERY 1 DAY
AS
SELECT
  ProductwithCode,
  SvcStd,
  OZip3,
  DZip3,
  DaysLateEarly,
  SUM(Total)  AS Total,
  SUM(Ontime) AS Ontime,
  SUM(Late)   AS Late
FROM ${var.catalog}.${var.schema}.md_national_days_to_delivery_preprocessed
WHERE ProductCode IN ('GA', 'PM', 'PK', 'EX')
GROUP BY
  ProductwithCode,
  SvcStd,
  OZip3,
  DZip3,
  DaysLateEarly;
