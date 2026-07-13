-- Run after MV creation, before deploying the dashboard.
-- Both row_count values should be 0. Any rows returned means the MV does not match the source.

-- Etsy: original → MV
SELECT 'etsy_original_minus_mv' AS check_name, COUNT(*) AS row_count FROM (
  SELECT ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly,
         SUM(Total) AS Total, SUM(Ontime) AS Ontime, SUM(Late) AS Late
  FROM ${var.catalog}.${var.schema}.md_etsy_days_to_delivery_preprocessed
  WHERE ProductCode NOT IN ('FC')
  GROUP BY ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly
  EXCEPT ALL
  SELECT ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly, Total, Ontime, Late
  FROM ${var.catalog}.${var.schema}.mv_etsy_days_to_delivery_base_agg
)

UNION ALL

-- Etsy: MV → original (reverse check)
SELECT 'etsy_mv_minus_original' AS check_name, COUNT(*) AS row_count FROM (
  SELECT ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly, Total, Ontime, Late
  FROM ${var.catalog}.${var.schema}.mv_etsy_days_to_delivery_base_agg
  EXCEPT ALL
  SELECT ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly,
         SUM(Total) AS Total, SUM(Ontime) AS Ontime, SUM(Late) AS Late
  FROM ${var.catalog}.${var.schema}.md_etsy_days_to_delivery_preprocessed
  WHERE ProductCode NOT IN ('FC')
  GROUP BY ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly
);
