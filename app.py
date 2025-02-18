from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
from flask_login import UserMixin
import mysql.connector
from mysql.connector import pooling
from datetime import datetime
import os
from dotenv import load_dotenv
import uuid
import logging
from decimal import Decimal
from flask_login import current_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')

# Database connection pool configuration
dbconfig = {
    "pool_name": "mypool",
    "pool_size": 5,
    "host": os.getenv('DB_HOST', 'localhost'),
    "user": os.getenv('DB_USER', 'root'),
    "password": os.getenv('DB_PASSWORD', ''),
    "database": os.getenv('DB_NAME', 'drugdatabase'),
    "connect_timeout": 30
}

# Initialize connection pool
try:
    connection_pool = pooling.MySQLConnectionPool(**dbconfig)
    logger.info("Database connection pool created successfully")
except Exception as e:
    logger.error(f"Failed to create connection pool: {e}")
    raise

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


class User(UserMixin):
    def __init__(self, id, email, role):
        self.id = id
        self.email = email
        self.role = role

    def get_id(self):
        return str(self.id)

# Database context manager with improved error handling


class DatabaseConnection:
    def __init__(self):
        self.conn = None
        self.cursor = None

    def __enter__(self):
        try:
            self.conn = connection_pool.get_connection()
            self.cursor = self.conn.cursor(dictionary=True, buffered=True)
            return self.cursor
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            if self.conn:
                self.conn.close()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                logger.error(f"Database operation failed: {exc_val}")
                if self.conn:
                    self.conn.rollback()
            else:
                if self.conn:
                    self.conn.commit()
        finally:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()

# Role-based access control with multiple roles support


def role_required(roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Please log in to access this page.', 'warning')
                return redirect(url_for('login'))
            if current_user.role not in roles:
                flash('You do not have access to this page.', 'danger')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@login_manager.user_loader
def load_user(user_id):
    try:
        with DatabaseConnection() as cursor:
            # Check seller table first
            cursor.execute(
                "SELECT *, 'seller' as role FROM seller WHERE seller_id = %s", (user_id,))
            user_data = cursor.fetchone()

            if not user_data:
                # Check customer table if not found in seller
                cursor.execute(
                    "SELECT *, 'customer' as role FROM customer WHERE customer_id = %s", (user_id,))
                user_data = cursor.fetchone()

            if user_data:
                role = user_data.pop('role')
                return User(user_id, user_data['email'], role)
    except Exception as e:
        logger.error(f"Error loading user: {e}")
        return None

# Enhanced error handling middleware


@app.errorhandler(Exception)
def handle_error(error):
    logger.error(f"Unhandled error: {error}")
    return render_template('500.html'), 500

# Routes with improved validation and error handling


@app.route('/')
def home():
    try:
        with DatabaseConnection() as cursor:
            cursor.execute("""
                SELECT p.*, s.company_name 
                FROM product p 
                JOIN seller s ON p.seller_id = s.seller_id 
                WHERE p.status = 'available' 
                LIMIT 10
            """)
            featured_products = cursor.fetchall()
        return render_template('home.html', featured_products=featured_products)
    except Exception as e:
        logger.error(f"Error loading home page: {e}")
        return render_template('home.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user_id = request.form['user_id']
        password = request.form['password']
        role = request.form['role']

        try:
            with DatabaseConnection() as cursor:
                if role == 'seller':
                    cursor.execute(
                        "SELECT * FROM seller WHERE seller_id = %s", (user_id,))
                elif role == 'customer':
                    cursor.execute(
                        "SELECT * FROM customer WHERE customer_id = %s", (user_id,))
                else:
                    flash('Invalid role selected', 'error')
                    return redirect(url_for('login'))

                user_data = cursor.fetchone()
                if user_data and check_password_hash(user_data['password'], password):
                    user = User(user_id, user_data['email'], role)
                    login_user(user)
                    flash('Login successful', 'success')
                    if role == 'seller':
                        return redirect(url_for('seller_dashboard'))
                    elif role == 'customer':
                        return redirect(url_for('customer_dashboard'))
                else:
                    flash('Invalid user ID or password', 'error')
                    return redirect(url_for('login'))

        except Exception as e:
            logger.error(f"Login error: {e}")
            flash('An error occurred during login', 'error')
            return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/register_seller', methods=['GET', 'POST'])
def register_seller():
    if request.method == 'POST':
        try:
            # Validate required fields
            required_fields = ['seller_id', 'company_name',
                               'license_number', 'email', 'phone', 'address', 'password']
            if not all(request.form.get(field) for field in required_fields):
                flash('All fields are required', 'error')
                return render_template('register_seller.html')

            hashed_password = generate_password_hash(request.form['password'])

            with DatabaseConnection() as cursor:
                # Check for existing email
                cursor.execute(
                    "SELECT 1 FROM seller WHERE email = %s", (request.form['email'],))
                if cursor.fetchone():
                    flash('Email already registered', 'error')
                    return render_template('register_seller.html')

                cursor.execute("""
                    INSERT INTO seller (
                        seller_id, password, company_name, license_number, 
                        email, phone, address, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    request.form['seller_id'],
                    hashed_password,
                    request.form['company_name'],
                    request.form['license_number'],
                    request.form['email'],
                    request.form['phone'],
                    request.form['address'],
                    request.form.get('status', 'active')
                ))

                flash('Registration successful', 'success')
                return redirect(url_for('login'))

        except Exception as e:
            logger.error(f"Error registering seller: {e}")
            flash('An error occurred during registration', 'error')
            return render_template('register_seller.html')

    return render_template('register_seller.html')


@app.route('/register_customer', methods=['GET', 'POST'])
def register_customer():
    if request.method == 'POST':
        try:
            # Validate required fields
            required_fields = ['customer_id', 'first_name',
                               'last_name', 'email', 'phone', 'address', 'password']
            if not all(request.form.get(field) for field in required_fields):
                flash('All fields are required', 'error')
                return render_template('register_customer.html')

            hashed_password = generate_password_hash(request.form['password'])

            with DatabaseConnection() as cursor:
                # Check for existing email
                cursor.execute(
                    "SELECT 1 FROM customer WHERE email = %s", (request.form['email'],))
                if cursor.fetchone():
                    flash('Email already registered', 'error')
                    return render_template('register_customer.html')

                cursor.execute("""
                    INSERT INTO customer (
                        customer_id, password, first_name, last_name, 
                        email, phone, address, membership_level
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    request.form['customer_id'],
                    hashed_password,
                    request.form['first_name'],
                    request.form['last_name'],
                    request.form['email'],
                    request.form['phone'],
                    request.form['address'],
                    request.form.get('membership_level', 'basic')
                ))

                flash('Registration successful', 'success')
                return redirect(url_for('login'))

        except Exception as e:
            logger.error(f"Error registering customer: {e}")
            flash('An error occurred during registration', 'error')
            return render_template('register_customer.html')

    return render_template('register_customer.html')


@app.route('/dashboard')
@login_required
def dashboard():
    return seller_dashboard() if current_user.role == 'seller' else customer_dashboard()


@app.route('/seller_dashboard')
@login_required
@role_required(['seller'])
def seller_dashboard():
    try:
        with DatabaseConnection() as cursor:
            # Recent orders query
            cursor.execute("""
                SELECT oi.order_date, oi.product_id, p.name as product_name,
                       oi.quantity, oi.unit_price, oi.subtotal, oi.customer_id
                FROM order_item oi
                JOIN product p ON oi.product_id = p.product_id
                WHERE p.seller_id = %s
                ORDER BY oi.order_date DESC
                LIMIT 5
            """, (current_user.id,))
            recent_orders = cursor.fetchall()
            logger.info(f"Recent orders: {recent_orders}")

            # Low stock products query
            cursor.execute("""
                SELECT product_id, name, stock_quantity, min_stock_level, status
                FROM product
                WHERE seller_id = %s AND stock_quantity <= min_stock_level
            """, (current_user.id,))
            low_stock_products = cursor.fetchall()
            logger.info(f"Low stock products: {low_stock_products}")

            # Product stats query
            cursor.execute("""
                SELECT COUNT(*) as total_products,
                       SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) as available_products,
                       SUM(CASE WHEN status = 'low_stock' THEN 1 ELSE 0 END) as low_stock_products,
                       SUM(CASE WHEN status = 'out_of_stock' THEN 1 ELSE 0 END) as out_of_stock_products
                FROM product
                WHERE seller_id = %s
            """, (current_user.id,))
            product_stats = cursor.fetchone()
            logger.info(f"Product stats: {product_stats}")

            # Nested query: Get all products with sales above average
            cursor.execute("""
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
                )
                AND p.seller_id = %s
            """, (current_user.id,))
            products_above_average_sales = cursor.fetchall()
            logger.info(
                f"Products above average sales: {products_above_average_sales}")

            # Aggregate query: Get sales summary by seller
            cursor.execute("""
                SELECT 
                    s.seller_id,
                    s.company_name,
                    COUNT(DISTINCT oi.order_id) as total_orders,
                    SUM(oi.quantity) as total_items_sold,
                    SUM(oi.subtotal) as total_revenue,
                    AVG(oi.unit_price) as average_unit_price
                FROM seller s
                LEFT JOIN order_item oi ON s.seller_id = oi.seller_id
                WHERE s.seller_id = %s
                GROUP BY s.seller_id, s.company_name
                ORDER BY total_revenue DESC
            """, (current_user.id,))
            sales_summary = cursor.fetchone()

            # Initialize default values if None
            if sales_summary:
                sales_summary['total_items_sold'] = sales_summary['total_items_sold'] or 0
                sales_summary['total_revenue'] = sales_summary['total_revenue'] or 0.0
                sales_summary['average_unit_price'] = sales_summary['average_unit_price'] or 0.0

            logger.info(f"Sales summary: {sales_summary}")

            return render_template('seller_dashboard.html',
                                   recent_orders=recent_orders,
                                   low_stock_products=low_stock_products,
                                   product_stats=product_stats,
                                   products_above_average_sales=products_above_average_sales,
                                   sales_summary=sales_summary)
    except Exception as e:
        logger.error(f"Error loading seller dashboard: {e}")
        flash('Error loading dashboard', 'error')
        return redirect(url_for('home'))


@app.route('/customer_dashboard')
@login_required
@role_required(['customer'])
def customer_dashboard():
    try:
        with DatabaseConnection() as cursor:
            cursor.execute("""
                SELECT oi.order_id, oi.product_id, oi.quantity, oi.unit_price, oi.subtotal, oi.customer_id, oi.seller_id, oi.order_date,
                       p.name as product_name, s.company_name as seller_name
                FROM order_item oi
                JOIN product p ON oi.product_id = p.product_id
                JOIN seller s ON oi.seller_id = s.seller_id
                WHERE oi.customer_id = %s
                ORDER BY oi.order_date DESC
                LIMIT 5
            """, (current_user.id,))
            recent_orders = cursor.fetchall()

            return render_template('customer_dashboard.html', recent_orders=recent_orders)
    except Exception as e:
        logger.error(f"Error loading customer dashboard: {e}")
        flash('Error loading dashboard', 'error')
        return redirect(url_for('home'))


@app.route('/products', methods=['GET', 'POST'])
@login_required
@role_required(['seller'])
def products():
    try:
        with DatabaseConnection() as cursor:
            if request.method == 'POST':
                # Validate input data
                required_fields = [
                    'name', 'manufacturer', 'mfg_date', 'exp_date', 'unit_price', 'stock_quantity']
                if not all(request.form.get(field) for field in required_fields):
                    flash('All required fields must be filled', 'error')
                    return redirect(url_for('products'))

                try:
                    # Validate dates
                    mfg_date = datetime.strptime(
                        request.form['mfg_date'], '%Y-%m-%d')
                    exp_date = datetime.strptime(
                        request.form['exp_date'], '%Y-%m-%d')

                    # Ensure dates are not in the past and expiry is after manufacture
                    current_date = datetime.now().date()
                    if mfg_date.date() < current_date:
                        flash('Manufacturing date cannot be in the past', 'error')
                        return redirect(url_for('products'))

                    if exp_date <= mfg_date:
                        flash(
                            'Expiration date must be after manufacturing date', 'error')
                        return redirect(url_for('products'))

                    # Validate numeric values
                    unit_price = Decimal(request.form['unit_price'])
                    stock_quantity = int(request.form['stock_quantity'])
                    min_stock_level = int(
                        request.form.get('min_stock_level', 10))

                    if unit_price <= 0:
                        flash('Unit price must be greater than 0', 'error')
                        return redirect(url_for('products'))

                    if stock_quantity < 0:
                        flash('Stock quantity cannot be negative', 'error')
                        return redirect(url_for('products'))

                    if min_stock_level < 0:
                        flash('Minimum stock level cannot be negative', 'error')
                        return redirect(url_for('products'))

                    # Generate product ID
                    product_id = f"P{uuid.uuid4().hex[:19]}"

                    # Determine initial status based on stock quantity
                    status = 'out_of_stock' if stock_quantity == 0 else \
                        'low_stock' if stock_quantity <= min_stock_level else \
                        'available'

                    # Check for duplicate product name for this seller
                    cursor.execute("""
                        SELECT 1 FROM product 
                        WHERE seller_id = %s AND LOWER(name) = LOWER(%s)
                    """, (current_user.id, request.form['name']))

                    if cursor.fetchone():
                        flash('A product with this name already exists', 'error')
                        return redirect(url_for('products'))

                    # Insert new product
                    cursor.execute("""
                        INSERT INTO product (
                            product_id, seller_id, name, description,
                            manufacturer, mfg_date, exp_date, unit_price,
                            stock_quantity, min_stock_level, status, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        product_id, current_user.id, request.form['name'],
                        request.form.get(
                            'description', ''), request.form['manufacturer'],
                        mfg_date, exp_date, unit_price, stock_quantity,
                        min_stock_level, status
                    ))

                    flash('Product added successfully', 'success')
                    return redirect(url_for('products'))

                except ValueError as e:
                    logger.error(f"Product validation error: {e}")
                    flash('Invalid input values', 'error')
                    return redirect(url_for('products'))

            # GET request - fetch and display products with sales metrics
            cursor.execute("""
                SELECT 
                    p.*,
                    COALESCE(SUM(oi.quantity), 0) as total_sold,
                    COALESCE(SUM(oi.subtotal), 0) as total_revenue,
                    COUNT(DISTINCT oi.order_id) as number_of_orders,
                    MAX(oi.order_date) as last_ordered_date
                FROM product p
                LEFT JOIN order_item oi ON p.product_id = oi.product_id
                WHERE p.seller_id = %s
                GROUP BY p.product_id
                ORDER BY p.created_at DESC
            """, (current_user.id,))
            products = cursor.fetchall()

            # Get total product count
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_products,
                    SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) as available_products,
                    SUM(CASE WHEN status = 'low_stock' THEN 1 ELSE 0 END) as low_stock_products,
                    SUM(CASE WHEN status = 'out_of_stock' THEN 1 ELSE 0 END) as out_of_stock_products
                FROM product
                WHERE seller_id = %s
            """, (current_user.id,))
            product_stats = cursor.fetchone()

            return render_template('products.html',
                                   products=products,
                                   product_stats=product_stats)

    except Exception as e:
        logger.error(f"Error in products route: {e}")
        flash('An error occurred while processing your request', 'error')
        return redirect(url_for('dashboard'))


@app.route('/update_product/<product_id>', methods=['POST'])
@login_required
@role_required(['seller'])
def update_product(product_id):
    try:
        with DatabaseConnection() as cursor:
            # Verify product ownership
            cursor.execute("""
                SELECT 1 FROM product 
                WHERE product_id = %s AND seller_id = %s
            """, (product_id, current_user.id))

            if not cursor.fetchone():
                flash('Product not found or access denied', 'error')
                return redirect(url_for('products'))

            # Validate and update fields
            updates = {}
            if 'name' in request.form:
                updates['name'] = request.form['name']
            if 'description' in request.form:
                updates['description'] = request.form['description']
            if 'unit_price' in request.form:
                updates['unit_price'] = Decimal(request.form['unit_price'])
            if 'stock_quantity' in request.form:
                updates['stock_quantity'] = int(request.form['stock_quantity'])
            if 'min_stock_level' in request.form:
                updates['min_stock_level'] = int(
                    request.form['min_stock_level'])

            if updates:
                # Construct dynamic UPDATE query
                query = "UPDATE product SET " + \
                        ", ".join(f"{k} = %s" for k in updates.keys()) + \
                        " WHERE product_id = %s AND seller_id = %s"

                cursor.execute(query,
                               (*updates.values(), product_id, current_user.id))

                flash('Product updated successfully', 'success')
            else:
                flash('No changes to update', 'info')

        return redirect(url_for('products'))

    except ValueError as e:
        flash('Invalid input values', 'error')
        return redirect(url_for('products'))
    except Exception as e:
        logger.error(f"Error updating product: {e}")
        flash('Error updating product', 'error')
        return redirect(url_for('products'))


@app.route('/delete_product/<product_id>', methods=['POST'])
@login_required
@role_required(['seller'])
def delete_product(product_id):
    try:
        with DatabaseConnection() as cursor:
            # Verify product ownership and delete
            cursor.execute("""
                DELETE FROM product 
                WHERE product_id = %s AND seller_id = %s
            """, (product_id, current_user.id))

            if cursor.rowcount > 0:
                flash('Product deleted successfully', 'success')
            else:
                flash('Product not found or access denied', 'error')

        return redirect(url_for('products'))
    except Exception as e:
        logger.error(f"Error deleting product: {e}")
        flash('Error deleting product', 'error')
        return redirect(url_for('products'))


@app.route('/orders', methods=['GET'])
@login_required
@role_required(['customer'])
def orders():
    try:
        with DatabaseConnection() as cursor:
            # Get available products with seller information
            cursor.execute("""
                SELECT p.*, s.company_name as seller_name
                FROM product p
                JOIN seller s ON p.seller_id = s.seller_id
                WHERE p.status = 'available'
                AND p.stock_quantity > 0
                ORDER BY p.name
            """)
            products = cursor.fetchall()

            # Get customer's order history
            cursor.execute("""
                SELECT 
                    oi.*,
                    p.name as product_name,
                    s.company_name as seller_name
                FROM order_item oi
                JOIN product p ON oi.product_id = p.product_id
                JOIN seller s ON oi.seller_id = s.seller_id
                WHERE oi.customer_id = %s
                ORDER BY oi.order_date DESC
            """, (current_user.id,))
            order_history = cursor.fetchall()

            return render_template('orders.html',
                                   products=products,
                                   order_history=order_history,
                                   cart=session.get('cart', []))
    except Exception as e:
        logger.error(f"Error loading orders page: {e}")
        flash('Error loading orders page', 'error')
        return redirect(url_for('dashboard'))


@app.route('/add_to_cart', methods=['POST'])
@login_required
@role_required(['customer'])
def add_to_cart():
    try:
        product_id = request.form['product_id']
        quantity = int(request.form['quantity'])
        unit_price = float(request.form['unit_price'])
        seller_id = request.form['seller_id']
        subtotal = quantity * unit_price

        cart_item = {
            'product_id': product_id,
            'quantity': quantity,
            'unit_price': unit_price,
            'subtotal': subtotal,
            'seller_id': seller_id
        }

        cart = session.get('cart', [])
        cart.append(cart_item)
        session['cart'] = cart
        session.modified = True

        flash('Product added to cart', 'success')
        return redirect(url_for('orders'))

    except ValueError:
        flash('Invalid quantity', 'error')
        return redirect(url_for('orders'))
    except Exception as e:
        logger.error(f"Error adding to cart: {e}")
        flash('Error adding product to cart', 'error')
        return redirect(url_for('orders'))


@app.route('/process_order', methods=['POST'])
@login_required
@role_required(['customer'])
def process_order():
    if not session.get('cart'):
        flash('Your cart is empty', 'error')
        return redirect(url_for('orders'))

    try:
        with DatabaseConnection() as cursor:
            cursor.execute("START TRANSACTION")

            # Generate a unique order_id
            order_id = f"ORD{datetime.now().strftime('%Y%m%d%H%M%S')}"

            for item in session['cart']:
                product_id = item['product_id']
                quantity = item['quantity']
                unit_price = item['unit_price']
                subtotal = item['subtotal']
                seller_id = item['seller_id']

                # Check stock availability
                cursor.execute("""
                    SELECT stock_quantity 
                    FROM product 
                    WHERE product_id = %s 
                    FOR UPDATE
                """, (product_id,))
                current_stock = cursor.fetchone()

                if not current_stock or current_stock['stock_quantity'] < quantity:
                    raise Exception(
                        f"Insufficient stock for product {product_id}")

                # Update stock
                cursor.execute("""
                    UPDATE product 
                    SET stock_quantity = stock_quantity - %s 
                    WHERE product_id = %s
                """, (quantity, product_id))

                # Add order item
                cursor.execute("""
                    INSERT INTO order_item (
                        order_id, product_id, quantity, unit_price, subtotal, customer_id, seller_id, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing')
                """, (
                    order_id,
                    product_id,
                    quantity,
                    unit_price,
                    subtotal,
                    current_user.id,
                    seller_id
                ))

            cursor.execute("COMMIT")
            session.pop('cart', None)
            flash('Order placed successfully', 'success')
            return redirect(url_for('orders'))

    except Exception as e:
        cursor.execute("ROLLBACK")
        logger.error(f"Error processing order: {e}")
        flash('Error processing order', 'error')
        return redirect(url_for('orders'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('home'))

# Error handlers


@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return render_template('500.html'), 500


if __name__ == '__main__':
    app.run(
        debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true',
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000))
    )
