# ---------- BALAJI DAIRY — Vercel (Serverless) + Supabase Edition ----------
# Key changes from original:
#   1. DB_CONFIG replaced by DATABASE_URL env var (Supabase connection string)
#   2. init_db() removed — run supabase_schema.sql in Supabase SQL Editor instead
#   3. File uploads to disk WON'T persist on Vercel (ephemeral FS) → use image URL field
#   4. SESSION_COOKIE_SAMESITE + SECURE set for Netlify ↔ Vercel cross-domain cookies
#   5. Template/static folders resolve relative to this file (../templates, ../static)

import os, json, pytz, psycopg2, psycopg2.extras
from flask import (
    Flask, render_template, render_template_string, request, redirect,
    url_for, session, g, flash, jsonify, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, timedelta, timezone
from collections import defaultdict, OrderedDict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# ---------- PATHS ----------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))  # backend/api/
TEMPLATE_DIR = os.path.join(BASE_DIR, '..', 'templates')   # backend/templates/
STATIC_DIR   = os.path.join(BASE_DIR, '..', 'static')      # backend/static/

# NOTE: UPLOAD_DIR is kept for LOCAL development only.
# On Vercel, the filesystem is ephemeral — use image URLs instead.
UPLOAD_DIR = os.path.join(BASE_DIR, '..', 'uploads')
try:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
except OSError:
    pass # Vercel has a read-only filesystem, ignore this error

# ---------- SUPABASE / DB CONNECTION ----------
# Set DATABASE_URL in Vercel environment variables (Project Settings → Env Vars)
# Format: postgresql://postgres:[password]@db.[ref].supabase.co:5432/postgres?sslmode=require
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ---------- FLASK APP ----------
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.secret_key = os.environ.get("FRESHMILK_SECRET", "replace-with-a-secure-random-string")
app.config.update(
    DEBUG=False,
    UPLOAD_DIR=UPLOAD_DIR,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    # Required so session cookies work when Netlify proxies to Vercel (cross-origin)
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_SECURE=True,
)

# ---------- DB ----------
def get_conn():
    if not hasattr(g, "pg_conn") or g.pg_conn.closed:
        g.pg_conn = psycopg2.connect(DATABASE_URL)
    return g.pg_conn

@app.teardown_appcontext
def close_conn(exception):
    if hasattr(g, "pg_conn"):
        try:
            g.pg_conn.close()
        except Exception:
            pass
        del g.pg_conn

# ---------- HELPERS ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def get_current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
        return cur.fetchone()

@app.context_processor
def inject_user_and_cartcount():
    user = get_current_user()
    cart = session.get('cart', {})
    total_items = sum(int(v) for v in cart.values()) if cart else 0
    if not user:
        return dict(current_user=None, cart_count=total_items)
    class U: pass
    u = U()
    u.id       = user['id']
    u.username = user['username']
    u.is_admin = bool(user['is_admin'])
    u.avatar   = user.get('avatar')
    u.initial  = (u.username[0].upper() if u.username else '?')
    return dict(current_user=u, cart_count=total_items)

# ---------- DECORATORS ----------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user or not user['is_admin']:
            flash("Admin access required", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return wrapper

# ---------- EMAIL OTP ----------
def send_otp_email(to_email, otp):
    try:
        sender   = os.environ.get("MAIL_SENDER", "")
        password = os.environ.get("MAIL_PASSWORD", "")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Balaji Dairy — Password Reset OTP"
        msg["From"]    = sender
        msg["To"]      = to_email
        html = f"""
        <html><body style="font-family:sans-serif;">
        <h2 style="color:#059669;">Balaji Dairy Password Reset</h2>
        <p>Your OTP: <b style="font-size:24px;color:#059669;">{otp}</b></p>
        <p>Valid for 10 minutes.</p>
        </body></html>
        """
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)

# ---------- TIME HELPERS ----------
def to_ist_display(dt):
    if not dt: return ''
    if isinstance(dt, str):
        try: dt = datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
        except: return dt
    ist = pytz.timezone('Asia/Kolkata')
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(ist).strftime('%b %d, %Y %I:%M %p')

def parse_order_items(items_json):
    try:
        arr = items_json if isinstance(items_json, list) else (json.loads(items_json) if isinstance(items_json, str) else [])
        parsed, names = [], []
        for it in arr:
            parsed.append({'id': it.get('id'), 'name': it.get('name'),
                           'qty': int(it.get('qty', 1)), 'price': float(it.get('price', 0))})
            names.append(f"{it.get('name')} x{it.get('qty')}")
        return parsed, ", ".join(names[:3]) + ("..." if len(names) > 3 else "")
    except Exception:
        return [], ""

# ---------- ROUTES ----------
@app.route('/')
def index():
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM products ORDER BY id DESC;")
        products = cur.fetchall()
    return render_template('index.html', products=products)

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM products WHERE id=%s;", (product_id,))
        product = cur.fetchone()
    if not product:
        flash('Product not found', 'error')
        return redirect(url_for('index'))
    return render_template('product.html', product=product)

# ---------- AUTH ----------
@app.route('/register', methods=['GET', 'POST'])
def register():
    conn = get_conn()
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        address  = request.form.get('address', '').strip()
        phone    = request.form.get('phone', '').strip()
        if not all([username, email, password, address, phone]):
            flash('All fields are required.', 'error')
            return redirect(url_for('register'))
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return redirect(url_for('register'))
        if not phone.isdigit() or len(phone) != 10:
            flash('Please enter a valid 10-digit phone number.', 'error')
            return redirect(url_for('register'))
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s OR email=%s", (username, email))
            if cur.fetchone():
                flash('Username or email already exists.', 'error')
                return redirect(url_for('register'))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, email, password, address, phone) VALUES (%s,%s,%s,%s,%s)",
                (username, email, generate_password_hash(password), address, phone)
            )
        conn.commit()
        flash('Account created successfully. Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = (request.form.get('username') or '').strip()
        password   = request.form.get('password') or ''
        conn = get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username=%s OR email=%s", (identifier, identifier))
            user = cur.fetchone()
        if user and check_password_hash(user['password'], password):
            session['user_id']  = user['id']
            session['username'] = user['username']
            flash('Logged in successfully', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid credentials', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))

# ---------- PROFILE ----------
# NOTE: On Vercel, uploaded files won't persist (ephemeral filesystem).
# Use image URLs instead of file uploads for profile avatars.
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = get_conn()
    if request.method == 'POST':
        # Try URL-based avatar first (always works on Vercel)
        avatar_url = (request.form.get('avatar_url') or '').strip()
        # Try file upload (works locally, NOT on Vercel production)
        file = request.files.get('avatar')
        if file and allowed_file(file.filename):
            filename = secure_filename(f"user_{session['user_id']}_{int(datetime.utcnow().timestamp())}_{file.filename}")
            path = os.path.join(app.config['UPLOAD_DIR'], filename)
            file.save(path)
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET avatar=%s WHERE id=%s", (url_for('uploaded_file', filename=filename), session['user_id']))
            conn.commit()
            flash('Avatar uploaded', 'success')
        elif avatar_url:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET avatar=%s WHERE id=%s", (avatar_url, session['user_id']))
            conn.commit()
            flash('Avatar URL saved', 'success')
        else:
            flash('Please provide an image file or URL.', 'error')
        return redirect(url_for('profile'))
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, username, avatar FROM users WHERE id=%s", (session['user_id'],))
        user = cur.fetchone()
    class U: pass
    u = U()
    u.id = user['id']; u.username = user['username']
    u.avatar = user.get('avatar')
    u.initial = (u.username[0].upper() if u.username else '?')
    return render_template('profile.html', user=u)

@app.route('/profile/remove')
@login_required
def profile_remove():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET avatar=NULL WHERE id=%s", (session['user_id'],))
    conn.commit()
    flash('Avatar removed', 'success')
    return redirect(url_for('profile'))

# ---------- FORGOT PASSWORD ----------
@app.route('/forgot', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identifier = (request.form.get('identifier') or '').strip()
        conn = get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username=%s OR email=%s", (identifier, identifier))
            user = cur.fetchone()
        if not user:
            flash('No account found with that username or email', 'error')
            return redirect(url_for('forgot_password'))
        import secrets
        otp     = f"{secrets.randbelow(1000000):06d}"
        expires = datetime.utcnow() + timedelta(minutes=10)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reset_otps WHERE user_id=%s", (user['id'],))
            cur.execute("INSERT INTO reset_otps (user_id, email, otp, expires_at) VALUES (%s,%s,%s,%s)",
                        (user['id'], user.get('email'), otp, expires))
        conn.commit()
        ok, _  = send_otp_email(user.get('email') or "", otp)
        session['reset_user'] = user['id']
        flash('OTP sent to your email.' if ok else 'Failed to send OTP. Try again.', 'success' if ok else 'error')
        return redirect(url_for('verify_otp') if ok else url_for('forgot_password'))
    return render_template('forgot.html')

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    if 'reset_user' not in session:
        return redirect(url_for('forgot_password'))
    conn    = get_conn()
    user_id = session['reset_user']
    if request.method == 'POST':
        otp = (request.form.get('otp') or '').strip()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM reset_otps WHERE user_id=%s AND otp=%s", (user_id, otp))
            rec = cur.fetchone()
        if not rec:
            flash('Invalid OTP', 'error')
            return redirect(url_for('verify_otp'))
        exp = rec['expires_at']
        now = datetime.now(timezone.utc) if (exp.tzinfo is not None) else datetime.utcnow()
        if now > exp:
            with conn.cursor() as cur: cur.execute("DELETE FROM reset_otps WHERE user_id=%s", (user_id,))
            conn.commit()
            flash('OTP expired. Please request again.', 'error')
            return redirect(url_for('forgot_password'))
        with conn.cursor() as cur:
            cur.execute("UPDATE reset_otps SET verified=TRUE WHERE id=%s", (rec['id'],))
        conn.commit()
        session['otp_verified'] = True
        flash('OTP verified! Set your new password.', 'success')
        return redirect(url_for('reset_with_otp'))
    return render_template('verify_otp.html')

@app.route('/reset_with_otp', methods=['GET', 'POST'])
def reset_with_otp():
    if not session.get('otp_verified') or 'reset_user' not in session:
        return redirect(url_for('forgot_password'))
    conn = get_conn()
    uid  = session['reset_user']
    if request.method == 'POST':
        new_pass = request.form.get('password') or ''
        confirm  = request.form.get('confirm') or ''
        if len(new_pass) < 6:
            flash('Password must be at least 6 characters', 'error')
            return redirect(url_for('reset_with_otp'))
        if new_pass != confirm:
            flash('Passwords do not match', 'error')
            return redirect(url_for('reset_with_otp'))
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password=%s WHERE id=%s", (generate_password_hash(new_pass), uid))
            cur.execute("DELETE FROM reset_otps WHERE user_id=%s", (uid,))
        conn.commit()
        session.pop('reset_user', None); session.pop('otp_verified', None)
        flash('Password reset successful! Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_otp.html')

# ---------- CART ----------
@app.route('/cart')
def cart():
    cart_data = session.get('cart', {})
    if not cart_data:
        return render_template('cart.html', items=[], total=0.0)
    conn = get_conn()
    items, total = [], 0.0
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for pid_str, qty in cart_data.items():
            try: pid = int(pid_str)
            except: continue
            cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
            p = cur.fetchone()
            if p:
                subtotal = float(p['price']) * int(qty)
                items.append({'product': p, 'qty': qty, 'subtotal': subtotal})
                total += subtotal
    return render_template('cart.html', items=items, total=total)

@app.route('/cart/add/<int:product_id>', methods=['POST'])
def add_to_cart(product_id):
    try: qty = max(1, int(request.form.get('qty', 1)))
    except: qty = 1
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
        p = cur.fetchone()
    if not p:
        flash('Product not found', 'error'); return redirect(url_for('index'))
    if int(p['stock']) < qty:
        flash(f"Not enough stock. Only {p['stock']} left.", 'error')
        return redirect(url_for('product_detail', product_id=product_id))
    cart_data = session.get('cart', {})
    cart_data[str(product_id)] = cart_data.get(str(product_id), 0) + qty
    session['cart'] = cart_data
    flash('Added to cart', 'success')
    return redirect(url_for('cart'))

@app.route('/api/cart/add', methods=['POST'])
def api_cart_add():
    product_id = request.form.get('product_id', type=int)
    qty        = request.form.get('qty', type=int)
    if not product_id or not qty:
        return jsonify({'error': 'Invalid input'}), 400
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
        product = cur.fetchone()
    if not product:
        return jsonify({'error': 'Product not found'}), 404
    if int(product['stock']) < int(qty):
        return jsonify({'error': 'Not enough stock'}), 400
    cart_data = session.get('cart', {})
    cart_data[str(product_id)] = cart_data.get(str(product_id), 0) + qty
    session['cart'] = cart_data
    return jsonify({'success': True, 'product_name': product['name'], 'total_items': sum(cart_data.values())})

@app.route('/cart/remove/<int:product_id>', methods=['POST'])
def remove_from_cart(product_id):
    cart_data = session.get('cart', {})
    cart_data.pop(str(product_id), None)
    session['cart'] = cart_data
    flash('Removed from cart', 'info')
    return redirect(url_for('cart'))

# ---------- CHECKOUT ----------
@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    cart_data = session.get('cart', {})
    if not cart_data:
        flash('Cart is empty', 'error'); return redirect(url_for('index'))
    conn = get_conn()
    items, total = [], 0.0
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for pid_str, qty in cart_data.items():
            try: pid = int(pid_str)
            except: continue
            cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
            product = cur.fetchone()
            if not product: continue
            if int(product['stock']) < int(qty):
                flash(f"Not enough stock for {product['name']}.", "error")
                return redirect(url_for('cart'))
            subtotal = float(product['price']) * int(qty)
            items.append({'product': dict(product), 'qty': int(qty), 'subtotal': subtotal})
            total += subtotal
    if request.method == 'POST':
        address = (request.form.get('address') or '').strip()
        if not address:
            flash('Address required', 'error'); return redirect(url_for('checkout'))
        order_items = [{'id': it['product']['id'], 'name': it['product']['name'],
                        'qty': it['qty'], 'price': float(it['product']['price'])} for it in items]
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO orders (user_id, items, total, address) VALUES (%s,%s,%s,%s) RETURNING id",
                    (session['user_id'], json.dumps(order_items), total, address)
                )
                order_id = cur.fetchone()['id']
                for it in items:
                    cur.execute("UPDATE products SET stock = stock - %s WHERE id=%s AND stock >= %s",
                                (it['qty'], it['product']['id'], it['qty']))
            conn.commit()
            session['cart'] = {}
            flash(f"Order #{order_id} placed successfully!", "success")
            return redirect(url_for('user_dashboard'))
        except Exception as e:
            conn.rollback()
            flash("Order could not be placed. Try again.", "error")
            return redirect(url_for('checkout'))
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE id=%s", (session['user_id'],))
        user = cur.fetchone()
    return render_template('checkout.html', items=items, total=total, user=user)

# ---------- USER DASHBOARD ----------
@app.route('/dashboard')
@login_required
def user_dashboard():
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM orders WHERE user_id=%s ORDER BY created_at DESC", (session['user_id'],))
        rows = cur.fetchall()
    orders = []
    for r in rows:
        parsed, summary = parse_order_items(r['items'] or '[]')
        orders.append({'id': r['id'], 'created_at': to_ist_display(r['created_at']),
                       'status': r['status'], 'total': float(r['total']),
                       'items_parsed': parsed, 'items_summary': summary, 'address': r['address']})
    return render_template('dashboard_user.html', orders=orders)

@app.route('/user/stats_fragment')
@login_required
def user_stats_fragment():
    start = request.args.get('start'); end = request.args.get('end')
    conn = get_conn()
    base = "SELECT status, total FROM orders WHERE user_id=%s"
    params = [session['user_id']]
    if start and end:   base += " AND DATE(created_at) BETWEEN %s AND %s"; params += [start, end]
    elif start:          base += " AND DATE(created_at) >= %s"; params += [start]
    elif end:            base += " AND DATE(created_at) <= %s"; params += [end]
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(base, params); rows = cur.fetchall()
    total_orders     = len(rows)
    delivered_orders = sum(1 for r in rows if r['status'] == 'Delivered')
    total_spent      = sum(float(r['total'] or 0) for r in rows)
    return f"""
      <div class='p-4 rounded-2xl bg-gradient-to-br from-emerald-50 to-white shadow-sm backdrop-blur-md border border-emerald-100'>
        <div class='text-sm text-gray-500'>Total Orders</div>
        <div class='text-2xl font-bold text-emerald-700'>{total_orders}</div>
      </div>
      <div class='p-4 rounded-2xl bg-gradient-to-br from-emerald-50 to-white shadow-sm backdrop-blur-md border border-emerald-100'>
        <div class='text-sm text-gray-500'>Delivered Orders</div>
        <div class='text-2xl font-bold text-emerald-700'>{delivered_orders}</div>
      </div>
      <div class='p-4 rounded-2xl bg-gradient-to-br from-emerald-50 to-white shadow-sm backdrop-blur-md border border-emerald-100'>
        <div class='text-sm text-gray-500'>Total Spent</div>
        <div class='text-2xl font-bold text-emerald-700'>₹{total_spent:.2f}</div>
      </div>
    """

@app.route('/user/orders_fragment')
@login_required
def user_orders_fragment():
    start = request.args.get('start'); end = request.args.get('end')
    conn = get_conn()
    base = "SELECT * FROM orders WHERE user_id=%s"
    params = [session['user_id']]
    if start and end: base += " AND DATE(created_at) BETWEEN %s AND %s"; params += [start, end]
    elif start:        base += " AND DATE(created_at) >= %s"; params += [start]
    elif end:          base += " AND DATE(created_at) <= %s"; params += [end]
    base += " ORDER BY created_at DESC"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(base, params); rows = cur.fetchall()
    if not rows:
        return '<div class="bg-white p-6 rounded shadow">No orders yet.</div>'
    out = []
    for r in rows:
        _, summary = parse_order_items(r['items'] or '[]')
        created = to_ist_display(r['created_at'])
        out.append(f'''
        <div class="border rounded p-3 mb-3">
          <div class="flex justify-between items-start">
            <div>
              <div class="text-sm font-medium">Order #{r['id']} — {created}</div>
              <div class="text-xs text-gray-500 mt-1">Status: {r['status']}</div>
              <div class="text-xs text-gray-500 mt-1">Items: {summary}</div>
            </div>
            <div class="text-right">
              <div class="text-sm font-semibold">₹{float(r['total'] or 0):.2f}</div>
              <div class="mt-2"><a href="{url_for('view_order', order_id=r['id'])}" class="text-sm underline text-emerald-700">View</a></div>
            </div>
          </div>
        </div>''')
    return '\n'.join(out)

# ---------- ADMIN ----------
@app.route('/admin')
@admin_required
def admin_dashboard():
    start = request.args.get('start'); end = request.args.get('end')
    conn  = get_conn()
    sql   = "SELECT o.*, u.username FROM orders o LEFT JOIN users u ON o.user_id=u.id WHERE 1=1"
    params = []
    if start and end: sql += " AND DATE(o.created_at) BETWEEN %s AND %s"; params += [start, end]
    elif start:        sql += " AND DATE(o.created_at) >= %s"; params += [start]
    elif end:          sql += " AND DATE(o.created_at) <= %s"; params += [end]
    sql += " ORDER BY o.created_at DESC"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params); rows = cur.fetchall()
        cur.execute("SELECT * FROM products ORDER BY id DESC"); products = cur.fetchall()
        stats_sql = "SELECT COUNT(*) AS order_count, COALESCE(SUM(total),0) AS revenue FROM orders WHERE 1=1"
        stats_params = []
        if start and end: stats_sql += " AND DATE(created_at) BETWEEN %s AND %s"; stats_params += [start, end]
        elif start:        stats_sql += " AND DATE(created_at) >= %s"; stats_params += [start]
        elif end:          stats_sql += " AND DATE(created_at) <= %s"; stats_params += [end]
        cur.execute(stats_sql, stats_params); stats_row = cur.fetchone() or {'order_count': 0, 'revenue': 0}
        cur.execute("SELECT COUNT(*) AS user_count FROM users"); user_row = cur.fetchone() or {'user_count': 0}
    daywise = defaultdict(list)
    for r in rows:
        _, summary = parse_order_items(r['items'] or '[]')
        entry = {'id': r['id'], 'user_id': r['user_id'], 'username': r.get('username'),
                 'items_summary': summary, 'total': float(r['total']),
                 'status': r['status'], 'created_at': to_ist_display(r['created_at'])}
        dt = r['created_at']
        if isinstance(dt, datetime):
            if dt.tzinfo: dt_ist = dt.astimezone(pytz.timezone('Asia/Kolkata'))
            else:          dt_ist = pytz.utc.localize(dt).astimezone(pytz.timezone('Asia/Kolkata'))
            date_key = dt_ist.strftime('%Y-%m-%d')
        else:
            date_key = str(dt).split(' ')[0]
        daywise[date_key].append(entry)
    stats = {'users': user_row.get('user_count', 0),
             'orders': stats_row.get('order_count', 0),
             'revenue': float(stats_row.get('revenue', 0) or 0.0)}
    if request.headers.get('X-Partial') == 'stats':
        return render_template_string('''
        <div class="space-y-3">
          <div>Total users: <strong>{{ stats.users }}</strong></div>
          <div>Total orders: <strong>{{ stats.orders }}</strong></div>
          <div>Revenue: <strong>₹{{ "%.2f"|format(stats.revenue) }}</strong></div>
        </div>''', stats=stats)
    return render_template('dashboard_admin.html', daywise=daywise, products=products, stats=stats)

@app.route('/admin/orders_fragment')
@admin_required
def admin_orders_fragment():
    start = request.args.get('start'); end = request.args.get('end')
    conn = get_conn()
    sql = "SELECT o.*, u.username FROM orders o LEFT JOIN users u ON o.user_id=u.id"
    params = []
    if start and end: sql += " WHERE DATE(o.created_at) BETWEEN %s AND %s"; params = [start, end]
    elif start:        sql += " WHERE DATE(o.created_at) >= %s"; params = [start]
    elif end:          sql += " WHERE DATE(o.created_at) <= %s"; params = [end]
    sql += " ORDER BY o.created_at DESC"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params); rows = cur.fetchall()
    daywise = defaultdict(list)
    for r in rows:
        _, summary = parse_order_items(r['items'] or '[]')
        entry = {'id': r['id'], 'user_id': r['user_id'], 'username': r.get('username'),
                 'items_summary': summary, 'total': float(r['total']),
                 'status': r['status'], 'created_at': to_ist_display(r['created_at'])}
        dt = r['created_at']
        date_key = (dt.astimezone(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
                    if isinstance(dt, datetime) else str(dt).split(' ')[0])
        daywise[date_key].append(entry)
    if not daywise:
        return '<div>No orders yet.</div>'
    parts = []
    for day, orders in daywise.items():
        parts.append(f'''
        <div class="mb-6 border border-white/50 rounded-lg overflow-hidden">
          <div class="bg-emerald-50/80 px-4 py-2 text-sm font-semibold text-emerald-800 border-b border-emerald-100">
            {day} — {len(orders)} order{'s' if len(orders)>1 else ''}
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm border-collapse min-w-[720px]">
              <thead class="bg-emerald-100 text-emerald-900"><tr>
                <th class="py-2 px-4">Order</th><th class="py-2 px-4">User</th>
                <th class="py-2 px-4">Items</th><th class="py-2 px-4 text-right">Total</th>
                <th class="py-2 px-4">Status</th><th class="py-2 px-4 text-center">Action</th>
              </tr></thead><tbody class="bg-white/50">''')
        for o in orders:
            parts.append(f'''
                <tr class="border-t hover:bg-white/70">
                  <td class="py-2 px-4"><div class="font-medium">#{o["id"]}</div><div class="text-xs text-gray-500">{o["created_at"]}</div></td>
                  <td class="py-2 px-4">{o.get("username") or "User "+str(o["user_id"])}</td>
                  <td class="py-2 px-4">{o["items_summary"]}</td>
                  <td class="py-2 px-4 text-right font-semibold">₹{o["total"]:.2f}</td>
                  <td class="py-2 px-4">{o["status"]}</td>
                  <td class="py-2 px-4 text-center"><a href="{url_for("view_order", order_id=o["id"])}" class="text-sm text-emerald-700 hover:underline">View</a></td>
                </tr>''')
        parts.append('</tbody></table></div></div>')
    return '\n'.join(parts)

@app.route('/admin/sales_data')
@admin_required
def admin_sales_data():
    period = request.args.get('period', 'day')
    start  = request.args.get('start'); end = request.args.get('end')
    conn   = get_conn()
    base   = "SELECT total, created_at FROM orders WHERE 1=1"
    params = []
    if start and end: base += " AND DATE(created_at) BETWEEN %s AND %s"; params = [start, end]
    elif start:        base += " AND DATE(created_at) >= %s"; params = [start]
    elif end:          base += " AND DATE(created_at) <= %s"; params = [end]
    base += " ORDER BY created_at ASC"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(base, params); rows = cur.fetchall()
    data_map = OrderedDict()
    for r in rows:
        dt = r['created_at'] if isinstance(r['created_at'], datetime) else datetime.utcnow()
        if dt.tzinfo: dt = dt.astimezone(pytz.utc).replace(tzinfo=None)
        if period == 'day':   key = dt.strftime('%Y-%m-%d'); label = dt.strftime('%d %b')
        elif period == 'week': iso = dt.isocalendar(); key = f"{iso[0]}-W{iso[1]:02d}"; label = (dt - timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
        elif period == 'month': key = dt.strftime('%Y-%m'); label = dt.strftime('%b %Y')
        else:                  key = dt.strftime('%Y'); label = dt.strftime('%Y')
        if key not in data_map: data_map[key] = {'label': label, 'total': 0.0}
        data_map[key]['total'] += float(r['total'] or 0)
    return jsonify({'labels': [v['label'] for v in data_map.values()],
                    'values': [round(v['total'], 2) for v in data_map.values()]})

# ---------- ADMIN: ORDER ACTIONS ----------
@app.route('/admin/order/<int:order_id>/update', methods=['POST'])
@admin_required
def admin_update_order(order_id):
    status = request.form.get('status') or 'Pending'
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE orders SET status=%s WHERE id=%s", (status, order_id))
    conn.commit()
    flash('Order updated', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/order/<int:order_id>/delete', methods=['POST'])
@admin_required
def delete_order(order_id):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM orders WHERE id=%s", (order_id,))
    conn.commit()
    flash('Order deleted', 'success')
    return redirect(url_for('admin_dashboard'))

# ---------- ORDER VIEW / CANCEL ----------
@app.route('/order/<int:order_id>')
@login_required
def view_order(order_id):
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT o.*, u.username, u.phone FROM orders o LEFT JOIN users u ON o.user_id=u.id WHERE o.id=%s", (order_id,))
        r = cur.fetchone()
    if not r:
        flash('Order not found', 'error'); return redirect(url_for('index'))
    parsed, _ = parse_order_items(r['items'] or '[]')
    order_obj = dict(r)
    order_obj['created_at'] = to_ist_display(r['created_at'])
    return render_template('order_detail.html', order=order_obj, parsed_items=parsed)

@app.route('/order/<int:order_id>/cancel', methods=['POST'])
@login_required
def cancel_order(order_id):
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM orders WHERE id=%s AND user_id=%s", (order_id, session['user_id']))
        r = cur.fetchone()
    if not r:
        flash('Order not found or access denied', 'error'); return redirect(url_for('user_dashboard'))
    if r['status'] != 'Pending':
        flash('Only pending orders can be cancelled', 'error'); return redirect(url_for('user_dashboard'))
    parsed, _ = parse_order_items(r['items'] or '[]')
    with conn.cursor() as cur:
        for it in parsed:
            cur.execute("UPDATE products SET stock = stock + %s WHERE id=%s", (it['qty'], it['id']))
        cur.execute("DELETE FROM orders WHERE id=%s", (order_id,))
    conn.commit()
    flash('Order cancelled and stock restored', 'success')
    return redirect(url_for('user_dashboard'))

# ---------- ADMIN: PRODUCT CRUD ----------
@app.route('/admin/product/add', methods=['GET', 'POST'])
@admin_required
def admin_add_product():
    if request.method == 'POST':
        name      = (request.form.get('name') or '').strip()
        desc      = request.form.get('description') or ''
        price     = float(request.form.get('price') or 0)
        stock     = int(request.form.get('stock') or 0)
        image_url  = (request.form.get('image_url') or '').strip()
        image_file = request.files.get('image_file')
        image_path = image_url  # Default to URL
        # File upload works locally; on Vercel use URL instead
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(f"product_{int(datetime.utcnow().timestamp())}_{image_file.filename}")
            dest = os.path.join(app.config['UPLOAD_DIR'], filename)
            image_file.save(dest)
            image_path = url_for('uploaded_file', filename=filename)
        if not name:
            flash('Product name is required.', 'error')
            return redirect(url_for('admin_add_product'))
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO products (name, description, price, image, stock) VALUES (%s,%s,%s,%s,%s)",
                        (name, desc, price, image_path, stock))
        conn.commit()
        flash('Product added successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_add_product.html')

@app.route('/admin/product/<int:product_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_product(product_id):
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
        p = cur.fetchone()
    if not p:
        flash('Product not found', 'error'); return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        name      = (request.form.get('name') or '').strip()
        desc      = request.form.get('description') or ''
        try:    price = float(request.form.get('price') or 0)
        except: price = 0.0
        try:    stock = int(request.form.get('stock') or 0)
        except: stock = 0
        image_url  = (request.form.get('image_url') or '').strip()
        image_file = request.files.get('image_file')
        image_path = p['image']
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(f"product_{product_id}_{int(datetime.utcnow().timestamp())}_{image_file.filename}")
            dest = os.path.join(app.config['UPLOAD_DIR'], filename)
            image_file.save(dest)
            image_path = url_for('uploaded_file', filename=filename)
        elif image_url:
            image_path = image_url
        with conn.cursor() as cur:
            cur.execute("UPDATE products SET name=%s, description=%s, price=%s, image=%s, stock=%s WHERE id=%s",
                        (name, desc, price, image_path, stock, product_id))
        conn.commit()
        flash('Product updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_edit_product.html', product=p)

@app.route('/admin/product/<int:product_id>/delete', methods=['POST'])
@admin_required
def admin_delete_product(product_id):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM products WHERE id=%s", (product_id,))
    conn.commit()
    flash('Product deleted', 'success')
    return redirect(url_for('admin_dashboard'))

# ---------- STATIC / UPLOADS ----------
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_DIR'], filename)

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

# ---------- ERROR HANDLERS ----------
@app.errorhandler(404)
def not_found(e):
    return render_template_string("<h1>404 Not Found</h1><p>The page does not exist. <a href='/'>Go Home</a></p>"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template_string("<h1>500 Server Error</h1><p>Something went wrong.</p>"), 500

# ---------- LOCAL DEV ENTRY ----------
# Vercel uses the 'app' WSGI object directly — no __main__ block needed.
# Run locally with: python backend/api/index.py
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
