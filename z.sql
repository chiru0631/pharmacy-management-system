-- Create Database
CREATE DATABASE IF NOT EXISTS drugdatabase;
USE drugdatabase;

-- Create Tables
CREATE TABLE seller (
    seller_id VARCHAR(20) PRIMARY KEY,
    password VARCHAR(255) NOT NULL,
    company_name VARCHAR(100) NOT NULL,
    license_number VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    phone VARCHAR(20) NOT NULL,
    address TEXT NOT NULL,
    status ENUM('active', 'suspended', 'inactive') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE customer (
    customer_id VARCHAR(20) PRIMARY KEY,
    password VARCHAR(255) NOT NULL,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    phone VARCHAR(20) NOT NULL,
    address TEXT NOT NULL,
    membership_level ENUM('basic', 'silver', 'gold', 'platinum') DEFAULT 'basic',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE product (
    product_id VARCHAR(20) PRIMARY KEY,
    seller_id VARCHAR(20),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    manufacturer VARCHAR(100) NOT NULL,
    mfg_date DATE NOT NULL,
    exp_date DATE NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    stock_quantity INT NOT NULL,
    min_stock_level INT DEFAULT 10,
    status ENUM('available', 'low_stock', 'out_of_stock') DEFAULT 'available',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (seller_id) REFERENCES seller(seller_id) ON DELETE CASCADE
);

CREATE TABLE order_item (
    order_id VARCHAR(20),
    product_id VARCHAR(20),
    quantity INT NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    subtotal DECIMAL(10,2) NOT NULL,
    customer_id VARCHAR(20),
    seller_id VARCHAR(20),
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status ENUM('pending', 'processing', 'shipped', 'delivered', 'cancelled') DEFAULT 'pending',
    PRIMARY KEY (order_id, product_id),
    FOREIGN KEY (product_id) REFERENCES product(product_id),
    FOREIGN KEY (customer_id) REFERENCES customer(customer_id),
    FOREIGN KEY (seller_id) REFERENCES seller(seller_id)
);

-- Triggers
DELIMITER //

-- Trigger to update product status based on stock quantity
CREATE TRIGGER update_product_status
BEFORE UPDATE ON product
FOR EACH ROW
BEGIN
    IF NEW.stock_quantity <= 0 THEN
        SET NEW.status = 'out_of_stock';
    ELSEIF NEW.stock_quantity <= NEW.min_stock_level THEN
        SET NEW.status = 'low_stock';
    ELSE
        SET NEW.status = 'available';
    END IF;
END//

-- Trigger to validate expiration date
CREATE TRIGGER validate_expiration_date
BEFORE INSERT ON product
FOR EACH ROW
BEGIN
    IF NEW.exp_date <= NEW.mfg_date THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Expiration date must be after manufacturing date';
    END IF;
END//

-- Trigger to calculate subtotal automatically
CREATE TRIGGER calculate_subtotal
BEFORE INSERT ON order_item
FOR EACH ROW
BEGIN
    SET NEW.subtotal = NEW.quantity * NEW.unit_price;
END//

-- Stored Procedures
-- Procedure to get low stock products
CREATE PROCEDURE get_low_stock_products(IN seller_id_param VARCHAR(20))
BEGIN
    SELECT 
        product_id,
        name,
        stock_quantity,
        min_stock_level,
        status
    FROM product
    WHERE seller_id = seller_id_param 
    AND (status = 'low_stock' OR status = 'out_of_stock');
END//

-- Procedure to process order
CREATE PROCEDURE process_order(
    IN p_order_id VARCHAR(20),
    IN p_customer_id VARCHAR(20),
    IN p_product_id VARCHAR(20),
    IN p_quantity INT
)
BEGIN
    DECLARE v_unit_price DECIMAL(10,2);
    DECLARE v_seller_id VARCHAR(20);
    DECLARE v_current_stock INT;
    
    START TRANSACTION;
    
    -- Get product details
    SELECT unit_price, seller_id, stock_quantity 
    INTO v_unit_price, v_seller_id, v_current_stock
    FROM product 
    WHERE product_id = p_product_id
    FOR UPDATE;
    
    -- Check stock availability
    IF v_current_stock >= p_quantity THEN
        -- Create order item
        INSERT INTO order_item (
            order_id, 
            product_id, 
            quantity, 
            unit_price, 
            subtotal, 
            customer_id, 
            seller_id,
            status
        ) VALUES (
            p_order_id,
            p_product_id,
            p_quantity,
            v_unit_price,
            v_unit_price * p_quantity,
            p_customer_id,
            v_seller_id,
            'processing'
        );
        
        -- Update stock
        UPDATE product 
        SET stock_quantity = stock_quantity - p_quantity
        WHERE product_id = p_product_id;
        
        COMMIT;
    ELSE
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Insufficient stock';
        ROLLBACK;
    END IF;
END//

DELIMITER ;

-- Sample Queries

-- Nested Query with GUI: Get all products with sales above average
SELECT p.*, 
    (SELECT COUNT(*) 
     FROM order_item oi 
     WHERE oi.product_id = p.product_id) as total_sales
FROM product p
WHERE (
    SELECT SUM(oi.quantity) 
    FROM order_item oi 
    WHERE oi.product_id = p.product_id
) > (
    SELECT AVG(total_sold)
    FROM (
        SELECT product_id, SUM(quantity) as total_sold
        FROM order_item
        GROUP BY product_id
    ) as avg_sales
);

-- Join Query with GUI: Get customer order history with product details
SELECT 
    c.customer_id,
    c.first_name,
    c.last_name,
    oi.order_id,
    oi.order_date,
    p.name as product_name,
    oi.quantity,
    oi.unit_price,
    oi.subtotal,
    s.company_name as seller_name
FROM customer c
JOIN order_item oi ON c.customer_id = oi.customer_id
JOIN product p ON oi.product_id = p.product_id
JOIN seller s ON oi.seller_id = s.seller_id;

-- Aggregate Query with GUI: Get sales summary by seller
SELECT 
    s.seller_id,
    s.company_name,
    COUNT(DISTINCT oi.order_id) as total_orders,
    SUM(oi.quantity) as total_items_sold,
    SUM(oi.subtotal) as total_revenue,
    AVG(oi.unit_price) as average_unit_price
FROM seller s
LEFT JOIN order_item oi ON s.seller_id = oi.seller_id
GROUP BY s.seller_id, s.company_name
ORDER BY total_revenue DESC;


FLUSH PRIVILEGES;