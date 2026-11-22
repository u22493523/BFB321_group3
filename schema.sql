-- ============================================================================
-- TapHouse Sports Bar and Grill - DIDSS Database Schema (Step 3)
-- Normalised to Third Normal Form (3NF) for MySQL 8.0+
--
-- This file is the full, current structure, used by init_db.py for a NEW database.
-- An EXISTING database is upgraded step by step with migrate.py (migrations/),
-- so its data is kept. Add every future change in both places.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Suppliers Table (3NF: stores supplier details once per supplier)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    lead_time_days INT NOT NULL CHECK (lead_time_days > 0),
    std_dev_lead_time DECIMAL(6,2) NOT NULL DEFAULT 0.00 CHECK (std_dev_lead_time >= 0),
    contact_email VARCHAR(160) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_suppliers_lead_time (lead_time_days)
);

-- ----------------------------------------------------------------------------
-- 2. Products Table (Beverage Inventory Master & Supply Chain Parameters)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    category VARCHAR(60) NOT NULL,
    supplier_id INT NOT NULL,
    current_stock INT NOT NULL DEFAULT 0 CHECK (current_stock >= 0),
    unit_cost DECIMAL(10,2) NOT NULL CHECK (unit_cost >= 0),
    annual_demand INT NOT NULL DEFAULT 0 CHECK (annual_demand >= 0),
    order_cost DECIMAL(10,2) NOT NULL DEFAULT 0.00 CHECK (order_cost >= 0),
    holding_cost DECIMAL(10,2) NOT NULL DEFAULT 0.00 CHECK (holding_cost >= 0),
    avg_daily_demand INT NOT NULL DEFAULT 0 CHECK (avg_daily_demand >= 0),
    std_dev_demand DECIMAL(6,2) NOT NULL DEFAULT 0.00 CHECK (std_dev_demand >= 0),
    safety_stock INT NOT NULL DEFAULT 0 CHECK (safety_stock >= 0),   -- calculated by supply_chain.py
    reorder_point INT NOT NULL DEFAULT 0 CHECK (reorder_point >= 0), -- calculated by supply_chain.py
    image_filename VARCHAR(120) DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_products_supplier
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    INDEX idx_products_category_stock (category, current_stock),
    INDEX idx_products_supplier_rop (supplier_id, reorder_point)
);

-- ----------------------------------------------------------------------------
-- 3. Sales Orders Table (Manual POS Entries & Historical CSV Uploads)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    product_id INT NOT NULL,
    quantity_sold INT NOT NULL CHECK (quantity_sold > 0),
    unit_price DECIMAL(10,2) NOT NULL CHECK (unit_price >= 0),
    sale_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_sales_product
        FOREIGN KEY (product_id) REFERENCES products(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    INDEX idx_sales_product_date (product_id, sale_date)
);

-- ----------------------------------------------------------------------------
-- 4. Users Table (Manager Authentication for Step 5 & Step 6)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 5. Stock Receipts (Step 6, migration 002): every delivery received
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_receipts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    product_id INT NOT NULL,
    quantity INT NOT NULL CHECK (quantity > 0),
    received_by INT NULL,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_receipt_product FOREIGN KEY (product_id) REFERENCES products(id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_receipt_user FOREIGN KEY (received_by) REFERENCES users(id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    INDEX idx_receipts_product_date (product_id, received_at)
);
