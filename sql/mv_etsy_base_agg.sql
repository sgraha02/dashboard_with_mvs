CREATE OR REPLACE MATERIALIZED VIEW ${var.catalog}.${var.schema}.mv_etsy_days_to_delivery_base_agg
COMMENT 'Pre-aggregated base grain for Etsy days-to-delivery dashboard. One row per ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly. Filters out FC product code.'
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
FROM ${var.catalog}.${var.schema}.md_etsy_days_to_delivery_preprocessed
WHERE ProductCode NOT IN ('FC')
GROUP BY
  ProductwithCode,
  SvcStd,
  OZip3,
  DZip3,
  DaysLateEarly;
