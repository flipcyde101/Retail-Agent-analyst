-- Five sanity checks after loading. Run with:
--   docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/checks.sql
-- Each one has an expectation you can judge without knowing the data by heart.

\echo '1. Chains by number of prices: the big French chains on top, one row each'
SELECT l.chain, count(*) AS prices, count(DISTINCT l.id) AS stores
FROM prices p JOIN locations l ON l.id = p.location_id
GROUP BY l.chain ORDER BY prices DESC LIMIT 12;

\echo '2. Prices per year: sparse before 2024, growing after'
SELECT extract(year FROM date)::int AS year, count(*) AS prices
FROM prices GROUP BY 1 ORDER BY 1;

\echo '3. Price distribution: median around 2-3 EUR, nothing <= 0 or > 200'
SELECT min(price), percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS median,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY price) AS p99, max(price)
FROM prices;

\echo '4. Whole milk, median EUR per litre by chain since 2025: expect 1.3-2.5 EUR (mostly branded and organic)'
SELECT chain, count(*) AS prices,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_unit) AS median_eur_per_l
FROM v_price_per_unit
WHERE 'en:whole-milks' = ANY(categories) AND per_unit = 'l'
  AND date >= '2025-01-01' AND chain IS NOT NULL
GROUP BY chain HAVING count(*) >= 10 ORDER BY median_eur_per_l;

\echo '5. Integrity: all four numbers must be 0'
SELECT
    (SELECT count(*) FROM prices p LEFT JOIN products pr ON pr.code = p.product_code
      WHERE pr.code IS NULL)                                         AS prices_without_product,
    (SELECT count(*) FROM (SELECT 1 FROM prices
      GROUP BY product_code, location_id, date HAVING count(*) > 1) d) AS duplicated_keys,
    (SELECT count(*) FROM prices WHERE date > current_date)          AS future_dates,
    (SELECT count(*) FROM v_price_per_unit WHERE price_per_unit <= 0) AS bad_unit_prices;
