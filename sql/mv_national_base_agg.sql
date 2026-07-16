CREATE OR REFRESH MATERIALIZED VIEW mv_national_days_to_delivery_base_agg
COMMENT 'Pre-aggregated base grain for National days-to-delivery dashboard. One row per ProductwithCode, SvcStd, DaysLateEarly. Filters to GA, PM, PK, EX product codes. National table has no OZip3/DZip3.'
CLUSTER BY AUTO
AS
SELECT
  ProductwithCode,
  SvcStd,
  DaysLateEarly,
  SUM(Total)  AS Total,
  SUM(Ontime) AS Ontime,
  SUM(Late)   AS Late
FROM ${var.source_catalog}.${var.source_schema}.md_national_days_to_delivery_preprocessed
WHERE ProductCode IN ('GA', 'PM', 'PK', 'EX')
GROUP BY
  ProductwithCode,
  SvcStd,
  DaysLateEarly;
