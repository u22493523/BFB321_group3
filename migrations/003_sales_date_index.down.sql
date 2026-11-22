-- Undo 003. No data changes; the dashboard is only slower on large databases.
DROP INDEX idx_sales_date ON sales_orders;
