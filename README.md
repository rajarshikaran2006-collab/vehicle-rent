# Roam — Vehicle Rental Management System

A responsive full-stack vehicle-rental application with a React frontend, Flask REST API, and a relational SQLAlchemy database. **MySQL is supported**; a seeded SQLite database is the default so the project can be explored without installing a database server. The public website, customer portal, and administrator/staff workspace use the same live records.

> **Demo / test use only.** The default payment provider simulates payments; it does not charge money. Razorpay test-mode integration can be enabled with your own test credentials. Live keys are deliberately rejected. The seeded people, reviews, contact details, and vehicles are illustrative. The generated vehicle imagery is illustrative, not a photograph of the exact listed model. This project needs a security, operational, legal, and payment-provider review before any real deployment.

## Contents

- `frontend/` — React, React Router, Vite, Chart.js, responsive CSS, and image assets
- `backend/app.py` — Flask API, authentication, booking, payments, returns, maintenance, analytics
- `backend/models.py` — relational models and constraints
- `backend/seed.py` — repeatable sample-data generator
- `backend/create_admin.py` — interactive administrator creation
- `schema_mysql.sql` — equivalent MySQL 8 / InnoDB schema
- `tests/` — backend integration and concurrency tests
- `frontend/e2e/` — Playwright browser smoke tests
- `.env.example` — configuration template

## Quick start: SQLite demo

Requires Python 3.10+ (3.11+ recommended), Node.js 20+, and npm.

```bash
# From the project root
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
python -m backend.seed               # creates the schema and demo records if empty
python -m backend.app                # API on http://localhost:5000
```

In a **second terminal**:

```bash
cd frontend
npm install
npm run dev                           # website on http://localhost:3000
```

Open <http://localhost:3000>. Vite proxies `/api` and `/uploads` to Flask; browser code never calls localhost directly.

### Quick start: single port (live link / one command)

To serve the built website and the API together from one port — the easiest way to get a shareable preview link or to host the demo on a single small server:

```bash
./run_live.sh                 # installs deps, builds the SPA, seeds demo data, serves on :5000
# or, manually:
cd frontend && npm run build && cd ..
python -m backend.app         # Flask serves frontend/dist at / and the API at /api
```

Open <http://localhost:5000>. Everything is same-origin, so no proxy or CORS setup is needed. `PORT=8080 ./run_live.sh` changes the port, and `SEED_DEMO=0` (or `APP_ENV=production`) skips sample data. The API creates its tables and seeds an empty database on first startup when `SEED_DEMO=1`. SQLite data is stored in `backend/instance/roam.db`.

### Demo accounts

The login screen also has one-click demo buttons.

| Role | Email | Password |
|---|---|---|
| Customer | `customer@roam.test` | `Customer123!` |
| Administrator | `admin@roam.test` | `Admin123!` |
| Staff | `staff@roam.test` | `Staff123!` |

The sample includes available vehicles, a future reservation, completed trips and reviews, an overdue active rental, and a vehicle in maintenance. Dates are generated relative to the time the database is seeded.

To recreate the sample data, **destroying all existing records in the configured database**, stop the API and run:

```bash
python -m backend.seed --reset
```

## Deploy a shareable public link

The app is deployment-ready as **one service**: Flask serves the built SPA at `/` and the API at `/api`, so a single URL covers the whole product (no CORS, no second host).

### Render (recommended, free tier works)

1. Push this repository to GitHub (the repo must be visible to Render).
2. In Render: **New + → Blueprint**, select the repository, **Apply**.
   `render.yaml` provisions the service automatically: Docker build, health check on `/api/health`, a generated `SECRET_KEY`, demo seeding, mock payments, and password-reset links disabled.
3. Wait for the first build (a few minutes). Render prints a public URL such as `https://roam-rental.onrender.com` — that is your shareable link. Add a custom domain under **Settings → Custom Domains** if you want.

To configure it by hand instead: **New + → Web Service → Docker**, repository, Dockerfile path `./Dockerfile`, health check path `/api/health`, then add the environment variables listed in `render.yaml`.

### Docker anywhere (Railway, Fly.io, VPS, Kubernetes)

```bash
docker build -t roam .
docker run -p 8000:8000 \
  -e SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  -e SEED_DEMO=1 -e SHOW_RESET_LINK=0 -e PORT=8000 \
  roam     # http://localhost:8000
```

Railway/Fly detect the `Dockerfile` automatically. On any host, keep `PORT` as provided by the platform and terminate TLS in front of the container — the app trusts `X-Forwarded-Proto`, so cookies become `Secure` automatically.

### Free-tier and demo caveats

- **Ephemeral disk:** the free plan recreates the container on deploys and restarts, and the SQLite demo database is regenerated and reseeded. Bookings made through the link are lost on redeploy. To persist data, attach a paid Render disk and set `DATABASE_URL=sqlite:////var/data/roam.db` plus `UPLOAD_DIR=/var/data/uploads` (see the commented block in `render.yaml`), or point `DATABASE_URL` at MySQL.
- **Cold starts:** a free instance sleeps after ~15 minutes idle; the first request takes roughly 30–60 seconds.
- **It is a public demo:** anyone with the link can sign in as the demo administrator and change or delete records. Share it only as a demo, and see `create_admin.py` if you need a private instance.
- **Not production-hardened:** payments are simulated, `APP_ENV` stays `development`, and the notes at the end of this file (WSGI server, HTTPS, backups, rate limiting, security review) still apply to a real deployment.

### Why not Vercel

Vercel is built for static sites and short-lived serverless functions. This app is a long-running Flask server with a server-side session store and SQLite on local disk; on Vercel the database would reset on every invocation and logins would not survive. A Vercel-split deployment (static React on Vercel + API on Render/MySQL) is possible but needs cross-origin CORS and third-party cookie configuration that this repo does not implement, so a single Render service is the simpler and more reliable link.

## MySQL setup

Use MySQL 8.0.16+ with InnoDB and `utf8mb4`. Create a database and a dedicated user (replace the password):

```sql
CREATE DATABASE vehicle_rental CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'rental_user'@'localhost' IDENTIFIED BY 'choose-a-strong-password';
GRANT ALL PRIVILEGES ON vehicle_rental.* TO 'rental_user'@'localhost';
```

Set the URL in `.env`, using the correct host, username and password:

```dotenv
DATABASE_URL=mysql+pymysql://rental_user:choose-a-strong-password@127.0.0.1:3306/vehicle_rental?charset=utf8mb4
```

URL-encode reserved characters in the password (for example `@` becomes `%40`). Then run `python -m backend.seed` and start Flask as above. The application creates the tables automatically. Alternatively, import `schema_mysql.sql` into an **empty** database before seeding:

```bash
mysql -u rental_user -p vehicle_rental < schema_mysql.sql
python -m backend.seed
```

The schema includes the requested users, customers, categories, branches, vehicles, bookings, payments, vehicle returns, maintenance, reviews, and notifications tables, plus revocable authentication sessions and contact messages. Primary/foreign keys, unique constraints, indexes, status checks, and monetary/date constraints are included. InnoDB row locks serialize conflicting bookings on the same vehicle. The default SQLite demo uses an immediate write transaction instead.

The schema import and concurrent booking smoke test were also exercised against MariaDB 11.8 using the MySQL protocol. Run the optional test below against your own MySQL version before deployment.

## How to use the application

- **Public website:** search by location, category and dates; browse, filter and sort the fleet; view details and date-specific availability; contact the team.
- **Customer:** register or use the demo account, select dates and locations, review the server-calculated quote, reserve, choose a payment method, download a PDF receipt, manage bookings, edit a profile and review completed trips.
- **Administrator:** use the dashboard to manage vehicles and image uploads, customers, bookings, payments, returns, maintenance, contact messages, and reports. Export a report as CSV.
- **Staff:** manage bookings, returns and maintenance. Customer-account management, payments, financial reports and revenue metrics are restricted to administrators.
- **Returns:** a staff member records the actual time, condition, notes and any additional charge. A late fee is added for each started 24-hour period after the scheduled return, at the booked daily rate. The vehicle is returned to the available fleet only after the return is completed. Additional charges create a separate pending payment.
- **Maintenance:** scheduled service blocks overlapping reservations; in-progress service blocks bookings. Due scheduled work is activated when the API is accessed, and a vehicle is made available again only when service is completed and no other active work or rental blocks it.

### Demo policies and calculations

- Prices are in INR. Rental duration is rounded up to a whole day. The server calculates the rental charge, a 5% service fee, and 5% demo GST on the rental charge plus service fee. These are illustrative, not tax advice.
- An unpaid, newly created reservation holds its dates for **15 minutes**. It is cancelled and released on the next API request after expiry. Cash selection or a successful payment confirms it; administrators may also approve a pending booking.
- Customers can cancel pending or confirmed bookings at least 24 hours before pickup. Mock paid payments are marked refunded on cancellation. A Razorpay test payment or a paid cash payment requires administrator refund review; the system does not claim a real-world refund was completed automatically.
- A vehicle currently marked rented or under maintenance cannot be booked, even for later dates, until the return/service workflow is completed. Future confirmed bookings and scheduled maintenance also block overlapping periods.
- Dates are stored as local wall times in `RENTAL_TIMEZONE`, default `Asia/Kolkata`; the demo branches are in India. All users and deployments should agree on this business timezone.
- Dashboard revenue is the sum of payments currently marked Paid, excluding Refunded payments. Period reports use payment `paid_at` for revenue, booking creation time for bookings (most-rented counts include active/completed rentals), and maintenance start time for maintenance expenses. The overdue count and fleet status are current snapshots.

## Payments and password reset

### Simulated payments (default)

`PAYMENT_MODE=mock` records UPI, card, and net-banking choices as **simulated** paid transactions. No payment-card fields or real money are handled. Cash is confirmed with a pending payment; an administrator marks it paid when collected. Receipts clearly state that the data is for a demo.

### Razorpay test mode

1. Create a Razorpay test-mode account and enable the desired test payment methods and automatic capture.
2. In `.env`, set `PAYMENT_MODE=razorpay_test`, `RAZORPAY_KEY_ID=rzp_test_...`, and `RAZORPAY_KEY_SECRET=...`. Restart Flask.
3. The server creates an INR test order; the client opens Razorpay's hosted checkout. The server verifies the checkout HMAC and fetches the payment from Razorpay to check order, amount, currency and **captured** status before marking it Paid. Card data is entered only into the hosted gateway, never this app or its database.
4. The administrator refund action initiates a test refund and checks it again if the gateway reports it is still processing. A payment is marked Refunded only once the gateway reports it processed.

The Razorpay transport and verification paths are tested with mocked gateway responses; an end-to-end transaction with actual test credentials was not performed here. The app does **not** implement gateway webhooks or automatic reconciliation if the customer closes the checkout before the callback. Reconcile such test payments manually. **Do not use for live payments**: live keys are rejected, and a proper webhook/reconciliation and security review is required first.

### Password reset

In the default demo, `SHOW_RESET_LINK=1` displays a one-use, one-hour reset link on the recovery page. This exposes account existence and must not be enabled on a public deployment. For email delivery, set `SHOW_RESET_LINK=0`, `PUBLIC_BASE_URL`, and the `SMTP_*` settings in `.env` (SMTP with STARTTLS). The API returns the same generic response for existing and nonexistent addresses. Booking and return notifications are in-app; SMS and transactional email notifications are not configured.

## Tests

```bash
# Backend tests use isolated temporary SQLite databases.
python -m pytest -q

# Optional MySQL concurrency smoke test. Use a NEW, disposable empty database;
# this test creates tables and sample data and changes that database.
TEST_MYSQL_URL='mysql+pymysql://user:password@127.0.0.1:3306/roam_test?charset=utf8mb4' \
  python -m pytest tests/test_mysql_optional.py -q
```

The backend suite covers CSRF and role/ownership checks, password hashing and reset, session revocation, server-side prices, overlapping and concurrent reservations, expiring holds, mock payment and PDF receipt, cancellation, returns and late fees, maintenance conflicts, reporting, image uploads, and mocked Razorpay signature/capture/refund handling.

Browser tests require the API and Vite servers running with seeded demo data and mock payments:

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

The browser suite creates and cancels a demo booking. Reset the demo database afterward if you want the original sample state. On Linux, Playwright may require additional system libraries; see Playwright's `install-deps` guidance.

## Configuration and deployment notes

All configuration is read from environment variables or the root `.env`; do not commit real secrets. Set `SEED_DEMO=0` to prevent sample accounts/data from being created. To create an administrator without a public privileged-registration endpoint:

```bash
python -m backend.create_admin
```

For an experimental single-server build, run `npm run build` in `frontend/` and then start Flask; it will serve the built SPA. Behind a TLS-terminating proxy the app trusts `X-Forwarded-Proto`/`X-Forwarded-Host` (`TRUST_PROXY=0` disables this); over HTTPS it marks the session cookie `Secure` and, outside production, `SameSite=None` so an embedded preview iframe keeps its login. `FRAME_ANCESTORS` sets the CSP `frame-ancestors` allow-list for the served pages: unset it means `*` in development and `'self'` in production. Use a production WSGI server and HTTPS rather than Flask's development server. `APP_ENV=production` adds sanity checks requiring a long random `SECRET_KEY`, secure cookies, `SHOW_RESET_LINK=0`, `SEED_DEMO=0`, and a non-mock payment mode. Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

Passwords use Werkzeug's scrypt hashing. Session cookies are signed, HttpOnly and SameSite=Lax; random session tokens are stored only as hashes and can be revoked on logout or password reset. State-changing API calls require a CSRF token. User ownership and role checks are enforced on the server, input validation is server-side, SQL uses parameterized ORM queries, and uploaded images are type-checked and re-encoded. The simple login/reset rate limiter is process-local; a production deployment needs a shared rate limiter, HTTPS, hardened infrastructure, backups, monitoring, migrations, tested email, retention policies, and a full security review. The default demo secret, sample users and demo reset links are not safe for production.
