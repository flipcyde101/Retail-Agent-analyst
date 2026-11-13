-- Tables for the retail analyst agent. Re-runnable: drops and recreates everything.
-- The COMMENTs are not decoration: the agent reads them (describe_schema, and the schema
-- written into its instructions) to know what each column means.

DROP VIEW IF EXISTS v_price_per_unit;
DROP TABLE IF EXISTS prices;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS locations;

CREATE TABLE products (
    code        text PRIMARY KEY,
    name        text NOT NULL,
    brand       text,
    category    text,
    categories  text[] NOT NULL DEFAULT '{}',
    quantity    numeric,
    unit        text CHECK (unit IN ('g', 'ml'))
);

COMMENT ON TABLE products IS 'One row per product, identified by its barcode.';
COMMENT ON COLUMN products.code IS 'Barcode (EAN).';
COMMENT ON COLUMN products.name IS 'Product name, usually in French.';
COMMENT ON COLUMN products.brand IS 'Manufacturer brand(s), comma separated. Not the supermarket chain.';
COMMENT ON COLUMN products.category IS 'Most specific Open Food Facts category, e.g. whole-milks.';
COMMENT ON COLUMN products.categories IS
    'All category tags from broad to specific, e.g. {en:dairies,en:milks,en:whole-milks}. '
    'Filter with: ''en:milks'' = ANY(categories).';
COMMENT ON COLUMN products.quantity IS 'Package size in the unit of the unit column. NULL if unknown.';
COMMENT ON COLUMN products.unit IS 'g or ml. NULL if unknown.';

CREATE TABLE locations (
    id           integer PRIMARY KEY,
    chain        text,
    store_brand  text,
    name         text,
    city         text,
    postcode     text,
    country      text NOT NULL
);

COMMENT ON TABLE locations IS 'One row per physical store. All stores are in France.';
COMMENT ON COLUMN locations.chain IS
    'Normalised supermarket chain, e.g. E.Leclerc, Carrefour. NULL for independent stores.';
COMMENT ON COLUMN locations.store_brand IS
    'Brand as written in the source, including store format, e.g. Carrefour Market.';
COMMENT ON COLUMN locations.country IS 'ISO country code. Always FR.';

CREATE TABLE prices (
    id             integer PRIMARY KEY,
    product_code   text NOT NULL REFERENCES products (code),
    location_id    integer NOT NULL REFERENCES locations (id),
    price          numeric(8, 2) NOT NULL CHECK (price > 0),
    currency       text NOT NULL,
    date           date NOT NULL,
    is_discounted  boolean NOT NULL
);

COMMENT ON TABLE prices IS
    'One observed shelf price: a product, in a store, on a date. Crowdsourced, so coverage '
    'is uneven: most products have only a few observations.';
COMMENT ON COLUMN prices.price IS 'Price paid for one package, in EUR.';
COMMENT ON COLUMN prices.currency IS 'Always EUR.';
COMMENT ON COLUMN prices.date IS 'Day the price was observed. Most data is from 2024 onwards.';
COMMENT ON COLUMN prices.is_discounted IS 'True if the price was a promotion.';

CREATE INDEX prices_product_idx ON prices (product_code);
CREATE INDEX prices_location_idx ON prices (location_id);
CREATE INDEX prices_date_idx ON prices (date);
