-- 003 (Step 7): the scalability test showed the dashboard slowing down as sales grow
-- (0.5 s at 300,000 sales). The dashboard only reads recent sales, so index the date.
CREATE INDEX idx_sales_date ON sales_orders (sale_date);
