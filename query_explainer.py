# Databricks notebook source

# COMMAND ----------

# MAGIC %md
# MAGIC # Why the Dashboard Query Was Slow — Step by Step
# MAGIC
# MAGIC This notebook walks through exactly what the Databricks AI/BI dashboard engine
# MAGIC generated and why it was expensive. We use a tiny 3-row dataset so every step
# MAGIC is visible. The same mechanics apply at scale.
# MAGIC
# MAGIC **The four problems:**
# MAGIC 1. GROUPING SETS multiplies every row into N subtotal levels
# MAGIC 2. Correlated scalar subqueries fire against the raw table for every grouped row
# MAGIC 3. OR predicates block the optimizer from using clean equi-joins
# MAGIC 4. The result: Cartesian product in the execution plan

# COMMAND ----------

# MAGIC %md
# MAGIC ## Setup: The Example Dataset
# MAGIC
# MAGIC Three rows — two products, two service standards, three delivery outcomes.
# MAGIC In production this table is 229MB. The point is the *shape* of the query, not the size.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TEMP VIEW raw_data AS
# MAGIC SELECT 'Priority-A' AS ProductwithCode, '2DAY' AS SvcStd, -1 AS DaysLateEarly, 100 AS Total, 90  AS Ontime, 10  AS Late
# MAGIC UNION ALL
# MAGIC SELECT 'Priority-A',                    '2DAY',           0,                   200,          200,          0
# MAGIC UNION ALL
# MAGIC SELECT 'Priority-B',                    'GRND',           1,                   150,          100,          50;
# MAGIC
# MAGIC SELECT * FROM raw_data;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Problem 1: GROUPING SETS Multiplies Your Data
# MAGIC
# MAGIC The dashboard needs aggregations at multiple levels simultaneously — by product+service+day,
# MAGIC by product+service, by product only, and a grand total. Instead of running four queries,
# MAGIC the engine runs one query with GROUPING SETS.
# MAGIC
# MAGIC Watch what happens to our 3 rows. Using just 2 dimensions here (ProductwithCode, SvcStd)
# MAGIC to keep the output readable — the real dashboard uses 5 dimensions, producing 10 levels.
# MAGIC
# MAGIC **3 input rows → 9 output rows**

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   ProductwithCode,
# MAGIC   SvcStd,
# MAGIC   DaysLateEarly,
# MAGIC   SUM(Total)  AS total_sum,
# MAGIC   SUM(Ontime) AS ontime_sum,
# MAGIC   SUM(Late)   AS late_sum,
# MAGIC   GROUPING_ID(ProductwithCode, SvcStd, DaysLateEarly) AS grouping_id,
# MAGIC   CASE GROUPING_ID(ProductwithCode, SvcStd, DaysLateEarly)
# MAGIC     WHEN 0 THEN 'All 3 dimensions — finest grain'
# MAGIC     WHEN 1 THEN 'DaysLateEarly rolled up'
# MAGIC     WHEN 2 THEN 'SvcStd rolled up'
# MAGIC     WHEN 3 THEN 'SvcStd + DaysLateEarly rolled up'
# MAGIC     WHEN 4 THEN 'ProductwithCode rolled up'
# MAGIC     WHEN 5 THEN 'ProductwithCode + DaysLateEarly rolled up'
# MAGIC     WHEN 6 THEN 'ProductwithCode + SvcStd rolled up'
# MAGIC     WHEN 7 THEN 'Grand total'
# MAGIC   END AS what_this_row_represents
# MAGIC FROM raw_data
# MAGIC GROUP BY GROUPING SETS (
# MAGIC   (ProductwithCode, SvcStd, DaysLateEarly),
# MAGIC   (ProductwithCode, SvcStd),
# MAGIC   (ProductwithCode, DaysLateEarly),
# MAGIC   (ProductwithCode),
# MAGIC   (SvcStd, DaysLateEarly),
# MAGIC   (SvcStd),
# MAGIC   (DaysLateEarly),
# MAGIC   ()
# MAGIC )
# MAGIC ORDER BY grouping_id, ProductwithCode, SvcStd, DaysLateEarly;

# COMMAND ----------

# MAGIC %md
# MAGIC **Key takeaway:** Every row of the raw table fans out into one row per grouping level.
# MAGIC NULL in a column means that dimension is "rolled up" (subtotaled). The `grouping_id`
# MAGIC encodes which dimensions are rolled up as a bitmask — bit 0 = DaysLateEarly,
# MAGIC bit 1 = SvcStd, bit 2 = ProductwithCode.
# MAGIC
# MAGIC With 5 dimensions (as in the real query), the dashboard produces 10 grouping levels.
# MAGIC This expansion is the foundation of all the problems that follow.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Problem 2: Correlated Subqueries Fire for Every Grouped Row
# MAGIC
# MAGIC Volume Share = `this row's total / the subtotal at the level above (DaysLateEarly rolled up)`.
# MAGIC
# MAGIC The dashboard engine computes the denominator by running a subquery back against
# MAGIC the raw table for every row in the grouped result. Let's make that explicit.
# MAGIC
# MAGIC First, look at what the denominator needs to be for each row:

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Step 1: compute the grouped result (call it r)
# MAGIC CREATE OR REPLACE TEMP VIEW r AS
# MAGIC SELECT
# MAGIC   ProductwithCode,
# MAGIC   SvcStd,
# MAGIC   DaysLateEarly,
# MAGIC   SUM(Total)  AS total_sum,
# MAGIC   GROUPING_ID(ProductwithCode, SvcStd, DaysLateEarly) AS grouping_id
# MAGIC FROM raw_data
# MAGIC GROUP BY GROUPING SETS (
# MAGIC   (ProductwithCode, SvcStd, DaysLateEarly),
# MAGIC   (ProductwithCode, SvcStd),
# MAGIC   (ProductwithCode, DaysLateEarly),
# MAGIC   (ProductwithCode),
# MAGIC   (DaysLateEarly),
# MAGIC   ()
# MAGIC );
# MAGIC
# MAGIC -- Step 2: for each row in r, show what subquery fires against raw_data
# MAGIC -- The denominator for Volume Share is total at the level where DaysLateEarly is rolled up
# MAGIC SELECT
# MAGIC   r.ProductwithCode,
# MAGIC   r.SvcStd,
# MAGIC   r.DaysLateEarly,
# MAGIC   r.total_sum                              AS numerator,
# MAGIC   r.grouping_id,
# MAGIC   -- This subquery fires once per row of r, scanning raw_data each time
# MAGIC   (SELECT SUM(s.Total)
# MAGIC    FROM raw_data s
# MAGIC    WHERE
# MAGIC      (((r.grouping_id & (1 << 2)) <> 0) OR s.ProductwithCode <=> r.ProductwithCode)
# MAGIC      AND
# MAGIC      (((r.grouping_id & (1 << 1)) <> 0) OR s.SvcStd <=> r.SvcStd)
# MAGIC      -- bit 0 (DaysLateEarly) intentionally NOT checked — always rolled up for denominator
# MAGIC   )                                        AS denominator,
# MAGIC   TRY_DIVIDE(
# MAGIC     r.total_sum,
# MAGIC     (SELECT SUM(s.Total)
# MAGIC      FROM raw_data s
# MAGIC      WHERE
# MAGIC        (((r.grouping_id & (1 << 2)) <> 0) OR s.ProductwithCode <=> r.ProductwithCode)
# MAGIC        AND
# MAGIC        (((r.grouping_id & (1 << 1)) <> 0) OR s.SvcStd <=> r.SvcStd)
# MAGIC     )
# MAGIC   )                                        AS volume_share
# MAGIC FROM r
# MAGIC ORDER BY r.grouping_id, r.ProductwithCode, r.SvcStd, r.DaysLateEarly;

# COMMAND ----------

# MAGIC %md
# MAGIC **Key takeaway:** Count the rows returned. Every single one triggered a separate
# MAGIC scan of `raw_data`. With our 3-row table that's trivial. In production, `r` has
# MAGIC millions of rows across 10 grouping levels — millions of subquery executions,
# MAGIC each scanning the 229MB raw table.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Problem 3: The OR Predicate Blocks Clean Join Optimization
# MAGIC
# MAGIC Look at the subquery predicate:
# MAGIC ```sql
# MAGIC WHERE
# MAGIC   (((r.grouping_id & (1 << 2)) <> 0) OR s.ProductwithCode <=> r.ProductwithCode)
# MAGIC   AND
# MAGIC   (((r.grouping_id & (1 << 1)) <> 0) OR s.SvcStd <=> r.SvcStd)
# MAGIC ```
# MAGIC
# MAGIC In plain English: *"If this dimension is rolled up (bit is set), ignore it for matching.
# MAGIC Otherwise, match exactly."*
# MAGIC
# MAGIC The optimizer wants to turn correlated subqueries into joins — that's called
# MAGIC **decorrelation**. A clean decorrelated join needs a simple equality condition:
# MAGIC `s.ProductwithCode = r.ProductwithCode`. But the OR means it can't — the condition
# MAGIC is sometimes an equality and sometimes always-true depending on the bit.
# MAGIC
# MAGIC Let's see what the predicate actually evaluates to for two different rows:

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Row 1: grouping_id = 0 (nothing rolled up) — needs exact match on both dimensions
# MAGIC -- Row 2: grouping_id = 1 (DaysLateEarly rolled up) — still needs exact match
# MAGIC -- Row 3: grouping_id = 3 (SvcStd + DaysLateEarly rolled up) — only ProductwithCode matters
# MAGIC -- Row 4: grouping_id = 7 (everything rolled up) — match ALL rows (grand total denominator)
# MAGIC
# MAGIC SELECT
# MAGIC   grouping_id,
# MAGIC   what_this_row_represents,
# MAGIC   CASE
# MAGIC     WHEN (grouping_id & (1 << 2)) <> 0 THEN 'IGNORE ProductwithCode (always match)'
# MAGIC     ELSE                                     'MATCH ProductwithCode exactly'
# MAGIC   END AS product_condition,
# MAGIC   CASE
# MAGIC     WHEN (grouping_id & (1 << 1)) <> 0 THEN 'IGNORE SvcStd (always match)'
# MAGIC     ELSE                                     'MATCH SvcStd exactly'
# MAGIC   END AS svcstd_condition
# MAGIC FROM (
# MAGIC   SELECT 0 AS grouping_id, 'All 3 dimensions'             AS what_this_row_represents UNION ALL
# MAGIC   SELECT 1,                'DaysLateEarly rolled up'                                  UNION ALL
# MAGIC   SELECT 3,                'SvcStd + DaysLateEarly rolled up'                         UNION ALL
# MAGIC   SELECT 7,                'Grand total'
# MAGIC );

# COMMAND ----------

# MAGIC %md
# MAGIC **Key takeaway:** The join condition changes per row. Spark's optimizer cannot build
# MAGIC a single hash table or sort-merge join for this — the join key is dynamic. It falls
# MAGIC back to a **broadcast + filter** or **Cartesian product + filter**, which is what
# MAGIC the query profile showed:
# MAGIC
# MAGIC ```
# MAGIC Left Outer Join → Shuffle → Grouping Aggregate → Shuffle → Grouping Aggregate → Cartesian product → ORDER BY
# MAGIC ```
# MAGIC
# MAGIC The Cartesian product is the smoking gun. It means every row of `r` was evaluated
# MAGIC against every row of the raw table, then filtered. At scale that's an O(n²) operation.

# COMMAND ----------

# MAGIC %md
# MAGIC ## The Fix: Solution 1 Rewrite (Eliminate the Correlated Subqueries)
# MAGIC
# MAGIC Instead of subqueries back to raw_data, we:
# MAGIC - Compute the denominator by self-joining `r` to itself (bit-flipping to get the rolled-up level)
# MAGIC - Compute cumulative total with a window function
# MAGIC
# MAGIC Both are operations entirely within `r` — no raw table scan after the initial GROUP BY.

# COMMAND ----------

# MAGIC %sql
# MAGIC WITH r AS (
# MAGIC   SELECT
# MAGIC     ProductwithCode, SvcStd, DaysLateEarly,
# MAGIC     SUM(Total)  AS total_sum,
# MAGIC     SUM(Ontime) AS ontime_sum,
# MAGIC     SUM(Late)   AS late_sum,
# MAGIC     GROUPING_ID(ProductwithCode, SvcStd, DaysLateEarly) AS grouping_id
# MAGIC   FROM raw_data
# MAGIC   GROUP BY GROUPING SETS (
# MAGIC     (ProductwithCode, SvcStd, DaysLateEarly),
# MAGIC     (ProductwithCode, SvcStd),
# MAGIC     (ProductwithCode, DaysLateEarly),
# MAGIC     (ProductwithCode),
# MAGIC     (DaysLateEarly),
# MAGIC     ()
# MAGIC   )
# MAGIC ),
# MAGIC -- Tag each row with the grouping_id of its denominator (DaysLateEarly bit flipped on)
# MAGIC r_with_keys AS (
# MAGIC   SELECT *, grouping_id | 1 AS denominator_grouping_id
# MAGIC   FROM r
# MAGIC ),
# MAGIC -- Cumulative total via window function — no subquery needed
# MAGIC r_with_cume AS (
# MAGIC   SELECT *,
# MAGIC     CASE WHEN (grouping_id & 1) = 0 THEN
# MAGIC       SUM(total_sum) OVER (
# MAGIC         PARTITION BY grouping_id, ProductwithCode, SvcStd
# MAGIC         ORDER BY DaysLateEarly
# MAGIC         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
# MAGIC       )
# MAGIC     ELSE total_sum END AS cumulative_total_sum
# MAGIC   FROM r_with_keys
# MAGIC ),
# MAGIC -- Pull the denominator rows (where DaysLateEarly is rolled up)
# MAGIC denom AS (
# MAGIC   SELECT grouping_id, ProductwithCode, SvcStd, total_sum AS denominator_total_sum
# MAGIC   FROM r
# MAGIC   WHERE (grouping_id & 1) = 1
# MAGIC )
# MAGIC -- Final join: r × denom on equi-join keys — clean, no Cartesian product
# MAGIC SELECT
# MAGIC   r.ProductwithCode, r.SvcStd, r.DaysLateEarly,
# MAGIC   r.total_sum, r.ontime_sum, r.late_sum,
# MAGIC   r.grouping_id,
# MAGIC   TRY_DIVIDE(r.total_sum,            d.denominator_total_sum) AS volume_share,
# MAGIC   TRY_DIVIDE(r.cumulative_total_sum,  d.denominator_total_sum) AS cumulative_volume_share
# MAGIC FROM r_with_cume r
# MAGIC LEFT JOIN denom d
# MAGIC   ON  d.grouping_id    = r.denominator_grouping_id
# MAGIC   AND d.ProductwithCode <=> r.ProductwithCode
# MAGIC   AND d.SvcStd          <=> r.SvcStd
# MAGIC ORDER BY r.grouping_id, r.ProductwithCode, r.SvcStd, r.DaysLateEarly;

# COMMAND ----------

# MAGIC %md
# MAGIC **Key takeaway:** Same results, no correlated subqueries, no Cartesian product.
# MAGIC The join between `r_with_cume` and `denom` uses equi-join keys that Spark can
# MAGIC hash-join efficiently. Run `EXPLAIN` on both queries and compare — the rewrite
# MAGIC eliminates the Cartesian product node entirely.

# COMMAND ----------

# MAGIC %md
# MAGIC ## The Fix: Solution 2a — Base-Grain MV
# MAGIC
# MAGIC Instead of rewriting the dashboard's generated SQL (which we can't control),
# MAGIC we shrink what the dashboard scans. The MV pre-aggregates to the finest grain
# MAGIC the dashboard needs — one row per (ProductwithCode, SvcStd, DaysLateEarly).
# MAGIC
# MAGIC The dashboard engine still generates the same GROUPING SETS query, but against
# MAGIC a table that's already aggregated — far fewer rows to expand and scan.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Simulate the MV output: pre-aggregated to base grain
# MAGIC CREATE OR REPLACE TEMP VIEW mv_base_agg AS
# MAGIC SELECT
# MAGIC   ProductwithCode, SvcStd, DaysLateEarly,
# MAGIC   SUM(Total)  AS Total,
# MAGIC   SUM(Ontime) AS Ontime,
# MAGIC   SUM(Late)   AS Late
# MAGIC FROM raw_data
# MAGIC GROUP BY ProductwithCode, SvcStd, DaysLateEarly;
# MAGIC
# MAGIC SELECT * FROM mv_base_agg;

# COMMAND ----------

# MAGIC %md
# MAGIC In our toy example the row count doesn't change much (data was already at base grain).
# MAGIC In production, the raw table has many duplicate rows at the same grain — the MV
# MAGIC collapses them. Fewer input rows means:
# MAGIC - Smaller GROUPING SETS expansion
# MAGIC - Smaller raw table for correlated subqueries to scan
# MAGIC - Less shuffle, less memory pressure
# MAGIC
# MAGIC ---
# MAGIC ## Summary
# MAGIC
# MAGIC | Problem | Root cause | Fix |
# MAGIC |---|---|---|
# MAGIC | GROUPING SETS explosion | Dashboard pre-computes all subtotal levels in one pass | MV reduces input rows so expansion is cheaper |
# MAGIC | Correlated subqueries | Volume Share denominator requires a cross-level lookup | Solution 1 rewrite replaces with window + equi-join |
# MAGIC | OR predicate | Dynamic join condition prevents hash/sort-merge join | Equi-join on `grouping_id` bit-flip instead |
# MAGIC | Cartesian product | Optimizer's fallback when it can't decorrelate the OR | Eliminated by Solution 1 rewrite |
# MAGIC
# MAGIC **Deployed fix:** Solution 2a MV — reduces input row count so the dashboard's
# MAGIC auto-generated query runs against less data. Solution 1 rewrite is the more complete
# MAGIC fix but requires controlling the SQL the dashboard generates.
