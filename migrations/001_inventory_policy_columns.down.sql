-- Undo 001. The safety stock values are lost; run `python init_db.py` or
-- POST /api/products/<id>/recalculate after re-applying to calculate them again.
ALTER TABLE users DROP COLUMN updated_at;
ALTER TABLE products DROP COLUMN safety_stock;
