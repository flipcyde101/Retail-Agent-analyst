-- Price per kilo or litre, so products of different sizes can be compared.
-- Only products with a known size in g or ml appear here. Sizes under 5 g/ml are
-- excluded: they are almost always data-entry errors and produce absurd unit prices.

CREATE OR REPLACE VIEW v_price_per_unit AS
SELECT
    p.id                                         AS price_id,
    pr.code                                      AS product_code,
    pr.name                                      AS product_name,
    pr.brand,
    pr.category,
    pr.categories,
    l.chain,
    l.city,
    p.date,
    p.price,
    p.is_discounted,
    CASE pr.unit WHEN 'g' THEN 'kg' WHEN 'ml' THEN 'l' END  AS per_unit,
    round(p.price / pr.quantity * 1000, 2)       AS price_per_unit
FROM prices p
JOIN products pr ON pr.code = p.product_code
JOIN locations l ON l.id = p.location_id
WHERE pr.unit IN ('g', 'ml')
  AND pr.quantity >= 5;

COMMENT ON VIEW v_price_per_unit IS
    'One row per observed price with the price per kilo or litre. Use it to compare '
    'products of different sizes. Products with unknown size are not in this view.';
COMMENT ON COLUMN v_price_per_unit.per_unit IS 'kg or l: the unit price_per_unit refers to.';
COMMENT ON COLUMN v_price_per_unit.price_per_unit IS 'EUR per kilo or per litre.';
