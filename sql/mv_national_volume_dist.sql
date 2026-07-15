-- Reads from the base MV (same pipeline, unqualified reference).
-- Window functions run at refresh time so the pivot table query becomes a
-- plain SELECT with no GROUPING SETS or correlated subqueries.
CREATE OR REFRESH MATERIALIZED VIEW mv_national_volume_distribution
COMMENT 'Pre-computed volume distribution for the National Days Late pivot table. VolumeShare and CumulativeVolumeShare are baked in at refresh time via window functions partitioned by (ProductwithCode, SvcStd), eliminating the AGGREGATE OVER correlated subquery pattern at dashboard query time.'
CLUSTER BY AUTO
AS
WITH with_share AS (
  SELECT
    ProductwithCode,
    SvcStd,
    DaysLateEarly,
    Total,
    Ontime,
    Late,
    TRY_DIVIDE(
      Total,
      SUM(Total) OVER (PARTITION BY ProductwithCode, SvcStd)
    ) AS VolumeShare
  FROM mv_national_days_to_delivery_base_agg
)
SELECT
  ProductwithCode,
  SvcStd,
  DaysLateEarly,
  Total,
  Ontime,
  Late,
  VolumeShare,
  SUM(VolumeShare) OVER (
    PARTITION BY ProductwithCode, SvcStd
    ORDER BY DaysLateEarly
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  ) AS CumulativeVolumeShare
FROM with_share;
