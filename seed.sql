-- ============================================================================
-- TapHouse Sports Bar and Grill - DIDSS Seed Data (Step 3)
-- Populates suppliers, beverage products, historical sales, and test manager
-- ============================================================================

-- 1. Seed Suppliers (4 Pretoria / South African Beverage Distributors)
INSERT INTO suppliers (name, lead_time_days, std_dev_lead_time, contact_email) VALUES
    ('SAB', 4, 1.00, 'orders@sab.co.za'),
    ('Distell', 3, 0.50, 'supply@distell.co.za'),
    ('Cape Wines', 5, 0.80, 'dispatch@capewines.co.za'),
    ('Beverage Co', 4, 1.00, 'orders@beverageco.co.za');

-- 2. Seed Beverage Products (12 TapHouse SKUs; BEV-001 to BEV-004 match UI mockup)
INSERT INTO products (
    sku, name, category, supplier_id, current_stock, unit_cost,
    annual_demand, order_cost, holding_cost, avg_daily_demand,
    std_dev_demand, reorder_point, image_filename
) VALUES
    ('BEV-001', 'Lager 500ml', 'Beer', 1, 48, 15.50, 3650, 120.00, 3.10, 10, 3.20, 60, 'lager_icon.png'),
    ('BEV-002', 'Cider 330ml', 'Cider', 2, 112, 18.00, 6205, 110.00, 3.60, 17, 4.40, 70, 'cider_icon.png'),
    ('BEV-003', 'House Red Wine', 'Wine', 3, 24, 65.00, 1095, 150.00, 13.00, 3, 1.00, 20, 'wine_icon.png'),
    ('BEV-004', 'Tonic Water', 'Mixer', 4, 20, 8.50, 2190, 80.00, 1.70, 6, 1.60, 35, 'tonic_icon.png'),
    ('BEV-005', 'Craft IPA 440ml', 'Beer', 1, 85, 22.00, 2920, 120.00, 4.40, 8, 2.80, 48, 'lager_icon.png'),
    ('BEV-006', 'Draught Keg 30L', 'Beer', 1, 6, 850.00, 365, 200.00, 170.00, 1, 0.45, 6, 'lager_icon.png'),
    ('BEV-007', 'Dry Lemon 200ml', 'Mixer', 4, 96, 7.80, 2555, 80.00, 1.56, 7, 2.10, 41, 'tonic_icon.png'),
    ('BEV-008', 'Sauvignon Blanc 750ml', 'Wine', 3, 30, 72.00, 1460, 150.00, 14.40, 4, 1.50, 28, 'wine_icon.png'),
    ('BEV-009', 'Apple Cider 500ml', 'Cider', 2, 64, 21.50, 3285, 110.00, 4.30, 9, 3.00, 38, 'cider_icon.png'),
    ('BEV-010', 'Soda Water 200ml', 'Mixer', 4, 40, 7.20, 2920, 80.00, 1.44, 8, 2.40, 47, 'tonic_icon.png'),
    ('BEV-011', 'Ginger Ale 200ml', 'Mixer', 4, 75, 8.00, 1825, 80.00, 1.60, 5, 1.75, 30, 'tonic_icon.png'),
    ('BEV-012', 'Rosé Wine 750ml', 'Wine', 3, 18, 68.00, 1095, 150.00, 13.60, 3, 1.20, 21, 'wine_icon.png');

-- 3. Seed Historical POS Sales Orders (36 transactions across 30 days)
INSERT INTO sales_orders (product_id, quantity_sold, unit_price, sale_date) VALUES
    (1, 24, 32.00, '2026-10-22 18:15:00'),
    (2, 30, 35.00, '2026-10-22 19:40:00'),
    (4, 18, 18.00, '2026-10-23 17:30:00'),
    (3, 6, 135.00, '2026-10-23 20:10:00'),
    (1, 65, 32.00, '2026-10-24 16:45:00'),
    (2, 72, 35.00, '2026-10-24 17:20:00'),
    (6, 2, 1650.00, '2026-10-24 18:00:00'),
    (5, 28, 42.00, '2026-10-24 19:15:00'),
    (7, 22, 16.00, '2026-10-25 14:30:00'),
    (8, 8, 145.00, '2026-10-26 19:05:00'),
    (1, 18, 32.00, '2026-10-27 18:50:00'),
    (10, 25, 15.00, '2026-10-28 17:10:00'),
    (9, 20, 40.00, '2026-10-29 18:30:00'),
    (4, 16, 18.00, '2026-10-30 19:45:00'),
    (1, 70, 32.00, '2026-10-31 16:15:00'),
    (2, 68, 35.00, '2026-10-31 17:50:00'),
    (12, 7, 140.00, '2026-10-31 20:25:00'),
    (11, 15, 17.00, '2026-11-01 15:10:00'),
    (3, 5, 135.00, '2026-11-02 19:30:00'),
    (5, 19, 42.00, '2026-11-03 18:20:00'),
    (1, 22, 32.00, '2026-11-04 17:40:00'),
    (7, 20, 16.00, '2026-11-05 18:10:00'),
    (2, 34, 35.00, '2026-11-06 19:00:00'),
    (1, 78, 32.00, '2026-11-07 16:30:00'),
    (6, 3, 1650.00, '2026-11-07 17:45:00'),
    (4, 24, 18.00, '2026-11-07 18:55:00'),
    (8, 9, 145.00, '2026-11-08 13:45:00'),
    (9, 18, 40.00, '2026-11-09 18:15:00'),
    (10, 21, 15.00, '2026-11-10 17:25:00'),
    (11, 14, 17.00, '2026-11-11 19:10:00'),
    (1, 26, 32.00, '2026-11-12 18:35:00'),
    (2, 32, 35.00, '2026-11-13 19:50:00'),
    (1, 82, 32.00, '2026-11-14 16:10:00'),
    (5, 30, 42.00, '2026-11-14 18:05:00'),
    (12, 6, 140.00, '2026-11-15 15:40:00'),
    (4, 19, 18.00, '2026-11-18 18:20:00');

-- 4. Seed Test Manager Account
-- Login Email: manager@taphouse.co.za
-- Plain Password (for README test credentials): TapHouse2026!
INSERT INTO users (first_name, last_name, email, password_hash) VALUES
    ('TapHouse', 'Manager', 'manager@taphouse.co.za', 'scrypt:32768:8:1$zFiHItjSzSnDd8F7$0f69dcb7e53e4a45227a34d6a95e8abdf1a642d147100aacc1dc33d80ad017dceebab16cee5f4ef9bcef0e216c951b59d47315322f3af3f108f338adf0d31091');
