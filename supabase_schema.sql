-- Balaji Dairy — Supabase Schema
-- Run this entire file in: Supabase Dashboard → SQL Editor → Paste → Run

-- ─── Users ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(200) NOT NULL,
    full_name VARCHAR(200),
    email VARCHAR(200),
    address TEXT,
    phone VARCHAR(50),
    is_admin BOOLEAN DEFAULT FALSE,
    avatar TEXT
);

-- ─── Products ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    price NUMERIC(10,2) NOT NULL,
    image TEXT,
    stock INTEGER DEFAULT 0
);

-- ─── Orders ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    items JSONB,
    total NUMERIC(10,2),
    address TEXT,
    status VARCHAR(50) DEFAULT 'Pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── Password Reset OTPs ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reset_otps (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    email TEXT,
    otp TEXT,
    expires_at TIMESTAMP WITH TIME ZONE,
    verified BOOLEAN DEFAULT FALSE
);

-- ─── Create Admin User ────────────────────────────────────────────────────────
-- IMPORTANT: Replace the password hash below!
-- Generate a new hash by running this Python snippet locally:
--   from werkzeug.security import generate_password_hash
--   print(generate_password_hash("your-admin-password"))
-- Then paste the hash in place of 'PASTE_YOUR_HASH_HERE'

INSERT INTO users (username, password, full_name, email, is_admin)
VALUES (
    'admin',
    'scrypt:32768:8:1$87EUEbwUrap8zDw9$a5246b2c9a3e488dcebe62dd58e2d8e623053353789729433e0664fe71f86881b3c6a702eab90d7a476a527',
    'Balaji Admin',
    'admin@balajidairy.com',
    TRUE
)
ON CONFLICT (username) DO NOTHING;

-- ─── Sample Products (optional — delete if not needed) ────────────────────────
INSERT INTO products (name, description, price, image, stock) VALUES
  ('Full Cream Milk (1L)', 'Fresh full cream milk from local farms, delivered daily.', 65.00, 'https://i.pinimg.com/736x/99/4e/33/994e338a12b4bd0a18544d5a1f46534c.jpg', 100),
  ('Toned Milk (500ml)', 'Low-fat toned milk for a healthy lifestyle.', 30.00, 'https://images.immediate.co.uk/production/volatile/sites/30/2023/03/Milks-in-glasses-and-bottles-6a7fc97.jpg', 80),
  ('Paneer (200g)', 'Fresh homemade paneer, soft and rich.', 90.00, 'https://i0.wp.com/www.healthshots.com/wp-content/uploads/2021/01/paneer.jpg', 40)
ON CONFLICT DO NOTHING;
