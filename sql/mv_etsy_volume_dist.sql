-- Solution 3: precomputes all GROUPING SETS levels with correct per-level
-- denominators for VolumeShare and CumulativeVolumeShare. Reads from the
-- base MV (same pipeline, unqualified reference).
--
-- GROUPING_ID bit assignment (5 dimensions):
--   bit 4 = ProductwithCode, bit 3 = SvcStd, bit 2 = OZip3,
--   bit 1 = DZip3, bit 0 = DaysLateEarly
--
-- denominator_grouping_id = grouping_id | 1 sets the DaysLateEarly bit,
-- pointing each row to its matching denominator row (same dimensions,
-- DaysLateEarly rolled up).
CREATE OR REFRESH MATERIALIZED VIEW mv_etsy_volume_distribution
COMMENT 'Solution 3: precomputed GROUPING SETS cube with correct VolumeShare and CumulativeVolumeShare at every grouping level for the Etsy Days Late pivot table. Restores pivot subtotals with mathematically correct denominators.'
CLUSTER BY AUTO
AS
WITH r AS (
  SELECT
    ProductwithCode,
    SvcStd,
    OZip3,
    DZip3,
    DaysLateEarly,
    SUM(Total)  AS Total,
    SUM(Ontime) AS Ontime,
    SUM(Late)   AS Late,
    TRY_DIVIDE(SUM(Ontime), SUM(Total)) AS OnTimeRate,
    TRY_DIVIDE(SUM(Late),   SUM(Total)) AS LateRate,
    GROUPING_ID(
      ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly
    ) AS grouping_id
  FROM mv_etsy_days_to_delivery_base_agg
  GROUP BY GROUPING SETS (
    (ProductwithCode, SvcStd, OZip3, DZip3, DaysLateEarly),
    (ProductwithCode, SvcStd, OZip3, DZip3),
    (ProductwithCode, SvcStd, OZip3, DaysLateEarly),
    (ProductwithCode, SvcStd, OZip3),
    (ProductwithCode, SvcStd, DaysLateEarly),
    (ProductwithCode, SvcStd),
    (ProductwithCode, DaysLateEarly),
    (ProductwithCode),
    (DaysLateEarly),
    ()
  )
),
r_with_cume AS (
  SELECT
    *,
    grouping_id | 1 AS denominator_grouping_id,
    CASE
      WHEN (grouping_id & 1) = 0 THEN
        SUM(Total) OVER (
          PARTITION BY grouping_id, ProductwithCode, SvcStd, OZip3, DZip3
          ORDER BY DaysLateEarly
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE Total
    END AS cumulative_total
  FROM r
),
denom AS (
  SELECT
    grouping_id,
    ProductwithCode,
    SvcStd,
    OZip3,
    DZip3,
    Total AS denominator_total
  FROM r
  WHERE (grouping_id & 1) = 1
)
SELECT
  r.ProductwithCode,
  r.SvcStd,
  r.OZip3,
  r.DZip3,
  r.DaysLateEarly,
  r.Total,
  r.Ontime,
  r.Late,
  r.OnTimeRate,
  r.LateRate,
  r.grouping_id,
  TRY_DIVIDE(r.Total,           d.denominator_total) AS VolumeShare,
  TRY_DIVIDE(r.cumulative_total, d.denominator_total) AS CumulativeVolumeShare
FROM r_with_cume r
LEFT JOIN denom d
  ON  d.grouping_id      = r.denominator_grouping_id
  AND d.ProductwithCode <=> r.ProductwithCode
  AND d.SvcStd          <=> r.SvcStd
  AND d.OZip3           <=> r.OZip3
  AND d.DZip3           <=> r.DZip3;
