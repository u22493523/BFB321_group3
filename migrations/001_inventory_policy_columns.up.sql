-- 001 (Step 3 fix): databases built before the fix lack these columns.
-- The model now saves safety stock per product, and every table has audit fields.
ALTER TABLE products
    ADD COLUMN safety_stock INT NOT NULL DEFAULT 0 CHECK (safety_stock >= 0) AFTER std_dev_demand;

ALTER TABLE users
    ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at;
