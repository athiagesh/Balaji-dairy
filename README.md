# 🥛 Balaji Dairy — Deployment Guide
## Stack: Netlify (frontend) + Vercel (backend) + Supabase (database)

---

## Step 1 — Supabase Setup (Database)

1. Go to [supabase.com](https://supabase.com) and create a **free account**
2. Click **New Project** → choose a name (e.g. `balaji-dairy`) → set a database password → create
3. Wait ~2 minutes for the project to spin up
4. In the left sidebar, go to **SQL Editor** → click **+ New query**
5. Open the file [`supabase_schema.sql`](./supabase_schema.sql) and **paste its entire contents** into the editor
6. **Important:** Before running, generate an admin password hash:
   ```bash
   python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-admin-password'))"
   ```
   Replace `PASTE_YOUR_HASH_HERE` in the SQL with the output
7. Click **Run** — your tables and initial data will be created
8. Get your **Database URL**: Project Settings → Database → Connection String → **URI** tab
   - It looks like: `postgresql://postgres:[password]@db.xxxx.supabase.co:5432/postgres`
   - Add `?sslmode=require` at the end

---

## Step 2 — Vercel Setup (Backend)

1. Go to [vercel.com](https://vercel.com) and sign up (use GitHub)
2. Push this entire `balaji` folder to a **GitHub repository** first:
   ```bash
   git init
   git add .
   git commit -m "Initial commit — Balaji Dairy"
   git remote add origin https://github.com/YOUR-USERNAME/balaji-dairy.git
   git push -u origin main
   ```
3. In Vercel: **Add New Project** → Import your GitHub repo
4. Vercel will auto-detect the `vercel.json` config
5. Go to **Settings → Environment Variables** and add:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | Your Supabase URI (with `?sslmode=require`) |
   | `FRESHMILK_SECRET` | Any random 32-char string |
   | `MAIL_SENDER` | Your Gmail address |
   | `MAIL_PASSWORD` | Your Gmail App Password |

6. Click **Deploy** — wait for it to finish
7. Copy your Vercel URL (e.g. `https://balaji-dairy-abc123.vercel.app`)

---

## Step 3 — Netlify Setup (Frontend / CDN)

1. Open [`netlify.toml`](./netlify.toml) and replace `YOUR-VERCEL-URL` with your actual Vercel URL
   ```toml
   to = "https://balaji-dairy-abc123.vercel.app/:splat"
   ```
2. Commit and push this change to GitHub
3. Go to [netlify.com](https://netlify.com) → **Add new site** → Import from GitHub
4. Select your repo → leave build settings blank (no build command needed)
5. Click **Deploy site**
6. Your site is live at `https://your-app-name.netlify.app` 🎉

---

## Step 4 — Gmail App Password (for OTP emails)

1. Log in to your Gmail account
2. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
3. Enable **2-Step Verification** if not already
4. Search for **App Passwords** → Generate one for "Mail"
5. Use this 16-character password as your `MAIL_PASSWORD` env var in Vercel

---

## File Structure

```
balaji/
├── api/
│   └── index.py          ← Flask app (Vercel serverless entry point)
├── templates/            ← All Jinja2 HTML templates
│   ├── base.html
│   ├── index.html
│   ├── product.html
│   ├── login.html
│   ├── register.html
│   ├── profile.html
│   ├── cart.html
│   ├── checkout.html
│   ├── dashboard_user.html
│   ├── dashboard_admin.html
│   ├── order_detail.html
│   ├── forgot.html
│   ├── verify_otp.html
│   ├── reset_otp.html
│   ├── admin_add_product.html
│   └── admin_edit_product.html
├── static/               ← Static assets (logo.png etc.)
├── public/
│   └── index.html        ← Netlify placeholder
├── supabase_schema.sql   ← Run this in Supabase SQL Editor
├── vercel.json           ← Vercel routing config
├── netlify.toml          ← Netlify proxy config (update with your Vercel URL!)
├── requirements.txt      ← Python dependencies
└── .env.example          ← Copy to .env for local development
```

---

## Local Development

```bash
# Install dependencies
pip install flask psycopg2-binary werkzeug pytz

# Create .env from example
copy .env.example .env
# Edit .env with your Supabase DATABASE_URL

# Run locally
python api/index.py
# Open http://localhost:5000
```

---

## Notes

- **Image uploads**: Product and avatar images use **URL links** (paste image URL). File uploads are not supported on Vercel's serverless platform.
- **Admin account**: Created by the SQL in `supabase_schema.sql`. Username: `admin`
- **Session cookies**: Configured to work cross-domain (Netlify ↔ Vercel).
