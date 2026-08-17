# John's Joyful Gifts

A simple, mobile-first e-commerce web app for a small home business selling toys,
stationery, school bags, kids products and gifts. Customers create an account
(mobile number + password) to check out; the owner manages products, stock and
orders from a mobile-friendly admin panel. Built to run at **zero ongoing cost,
with no exceptions** — every service used is free, self-hosted, or a fixed
free tier; nothing in this app can generate a bill.

## Overview

- Customers create an account with a mobile number and password, browse/search
  products, check out, get an order number, and can view their order history
  (or track any order later with just the order number + mobile number).
- The owner logs into `/admin` (linked at the bottom of every page, or go
  directly to `/admin/login`) to add products, upload photos, manage stock,
  and update order status/courier tracking.
- Payment is **UPI / Bank Transfer only** — the owner fills in a UPI ID or
  bank details in `/admin/settings`, customers pay directly (with a QR code
  shown on the order confirmation page), and the owner manually marks the
  order Paid. No payment gateway, no transaction fees, no third-party account
  required to receive money. See "Zero-Cost Guarantee" below for why this is
  deliberate.

## Zero-Cost Guarantee

Every piece of this app was chosen specifically to avoid any ongoing cost:

| Need | What's used | Cost |
|---|---|---|
| Source control | GitHub (private repo) | Free |
| Backend/hosting | FastAPI (Python) on Render's free Web Service tier | Free |
| Database | Supabase Postgres (free tier, 500MB) | Free |
| Image storage | Supabase Storage (free tier, 1GB) | Free |
| Authentication | Self-hosted password hashing (bcrypt), no third-party auth service | Free |
| Customer contact | `wa.me` WhatsApp deep links (not the paid WhatsApp Business API) | Free |
| Payment | UPI / Bank Transfer, confirmed manually by the owner | Free |
| Domain | Free hosting subdomain (`*.onrender.com`) — no domain purchase needed | Free |

An earlier version of this app included an optional Razorpay online-payment
integration. It was **removed entirely** (not just disabled) because every
real payment gateway takes a percentage of each transaction — there is no
truly free way to accept card/UPI payments automatically, so keeping that
door open at all would contradict a hard zero-cost requirement. If online
payment is wanted in the future, the honest zero-cost equivalent is a manual
UPI option (show a UPI ID or QR code at checkout, customer pays directly with
any UPI app, admin manually confirms) — not built here, but straightforward
to add later without needing any of this app's other pieces to change.

**Free-tier limits still apply** — see "Free Deployment (Render + Supabase)"
below. Free doesn't mean unlimited: Render's and Supabase's free allowances
are fixed amounts of compute/database/storage, sufficient for a small shop
but worth monitoring as it grows.

## Architecture

A single monolithic FastAPI app — no separate frontend build, no JS framework,
no Node.js required at all:

- **Backend**: FastAPI (Python), server-rendered with Jinja2 templates.
- **Interactivity**: vanilla JS (`app/static/js/cart.js`) progressively enhances
  plain HTML forms — every action (add to cart, checkout, update order status)
  still works as a normal form POST if JavaScript fails.
- **Database**: Postgres (Supabase, free tier) via SQLAlchemy, with row-level
  locking for order creation (see "Inventory safety" below).
- **Images**: stored in Supabase Storage (a free-tier S3-like object store),
  behind a small abstraction (`app/storage.py`) so the storage backend can
  change again later without touching route code.
- **Auth**: signed, httpOnly session cookie for admin login (`app/auth.py`),
  bcrypt password hashing. No third-party auth service.

### Why this stack

Node.js was not available in the target environment, so the whole app is one
language (Python) with zero JS build tooling — nothing to compile, bundle, or
break. This also directly satisfies the project's "prefer simple monolithic
architecture" and "zero mandatory cost" requirements.

### Inventory safety (the most important part)

Stock must never be oversold — e.g. if stock is 2 and two customers both try
to buy 2 at nearly the same moment, only one may succeed. This is implemented
in `app/routers/checkout.py` using Postgres row-level locking:

- The order-creation transaction queries every product in the cart with
  `SELECT ... FOR UPDATE` (`.with_for_update()`), which locks **only those
  specific product rows** for the rest of the transaction.
- Two concurrent checkouts that share a product **serialize** on that
  product: the second blocks until the first commits or rolls back, then
  re-reads genuinely current stock. There is no window where both can read
  stale stock and both succeed. Checkouts for *unrelated* products are never
  blocked by each other — a strict improvement over locking the whole
  database.
- Order-number generation (`app/utils/order_number.py`) uses a separate
  `pg_advisory_xact_lock` keyed by date, since it does a count-then-format
  over the `orders` table rather than touching product rows.
- This is covered by an automated, real-concurrency test:
  `tests/test_inventory_safety.py::test_concurrent_checkouts_cannot_oversell`.

### Duplicate order protection

- The frontend disables the "Place Order" button on submit and reuses one
  idempotency key for the whole checkout attempt (`sessionStorage`).
- The server requires that key on every checkout request. A retry with the
  same key (double-click, refresh, slow network) returns the **original**
  order instead of creating a new one.
- A true concurrent double-submit (two requests racing with the same key) is
  handled via the same write lock: the loser's insert hits a UNIQUE
  constraint on `idempotency_key`, and the server returns the winner's order
  instead of erroring.

### Order data integrity

- Checkout **always** recomputes price, stock, and delivery charge from the
  database — the cart cookie only ever stores `product_id -> quantity`, never
  a price, so there is nothing for a client to tamper with.
- Each order item stores a **snapshot** of the product name and price at the
  time of purchase, so past orders never change if a product's price or name
  changes later.

### Order tracking privacy

`/track-order` requires both the order number **and** the mobile number used
at checkout, and returns the identical "not found" message whether the order
number is wrong or just the mobile doesn't match — so it can't be used to
enumerate valid order numbers.

### Customer accounts

Every order requires a logged-in customer — there's no guest checkout.
Login is mobile number + password (`app/customer_auth.py`, mirroring the
admin session pattern in `app/auth.py`): no OTP/SMS, no email verification,
because both would need a paid provider.

- `Customer.mobile` is unique — one account per mobile number, reused across
  every order that customer places.
- `Order` stores its own delivery snapshot (`delivery_name`, `delivery_address`,
  etc., independent of `Customer`) so editing a saved address, or shipping a
  future order elsewhere, never rewrites a past order's record.
- **No self-service password reset** — that would need a paid SMS/email
  service too. If a customer forgets their password, an admin can set a
  temporary one from that customer's order in `/admin/orders/...` and relay
  it over WhatsApp/phone.
- Rate-limited login attempts (`app/rate_limit.py`, in-memory, no external
  service) to blunt brute-force attempts against an account.

## Local Setup

Requires Python 3.10+ (no Node.js needed).

```bash
cd johns-joyful-gifts
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

## Environment Variables

Copy `.env.example` to `.env` and fill in real values:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs admin session + cart cookies. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. **Must** be changed for production. |
| `DATABASE_URL` | Supabase Postgres **Session pooler** connection string (Project Settings → Database). The direct connection is IPv6-only and won't resolve from most networks/hosts — always use the pooler URL. |
| `SUPABASE_URL` | Supabase project URL (Project Settings → API), e.g. `https://xxxx.supabase.co`. |
| `SUPABASE_SERVICE_KEY` | Supabase `service_role`/"secret" key (Project Settings → API) — used server-side for Storage uploads. Never expose this to the frontend. |
| `UPLOAD_DIR` | Unused in production (images go to Supabase Storage); kept only as a legacy local-dev fallback path. |
| `MAX_UPLOAD_SIZE_BYTES` | Per-image upload limit (default 5 MB). |
| `STORE_NAME`, `STORE_TAGLINE`, `WHATSAPP_NUMBER`, `INSTAGRAM_URL`, `DEFAULT_DELIVERY_CHARGE`, `FREE_DELIVERY_THRESHOLD` | Initial defaults — all editable later from `/admin/settings` without redeploying. |
| `ENVIRONMENT` | Set to `production` to enable secure (HTTPS-only) cookies. |

## Database Setup

Migrations are managed with Alembic, applied against your Supabase Postgres
database (via `DATABASE_URL`).

```bash
alembic upgrade head
```

This creates the full schema in your Supabase project. Re-run
`alembic upgrade head` after pulling any future migration — it only ever
applies forward, additive changes; it will never drop or reset existing data.
(The Dockerfile also runs this automatically on every deploy, before the app
starts.)

To add demo products for testing (never run this against a real store's
database):

```bash
python scripts/seed_demo_data.py
```

## Admin Setup

There is no hardcoded admin account. Create the first one with:

```bash
python scripts/create_admin.py
```

You'll be prompted for a name, email, and password (min 8 characters). Log in
at `/admin/login` with those credentials. Run the script again any time to
add more admin accounts.

## Development

```bash
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000`. The admin panel is at `/admin`.

## Testing

```bash
pip install -r requirements.txt   # includes pytest, httpx
pytest -v
```

The suite specifically exercises the safety-critical paths: concurrent
checkout oversell prevention, duplicate-order idempotency (both sequential
retries and true concurrent double-submits), order-tracking authorization,
that pricing/stock are always server-computed, stock restoration on order
cancellation, and the customer-account system (registration validation,
login, checkout genuinely blocked — both the page and the API — when logged
out, and that repeat orders from one customer reuse a single account while
keeping independent delivery-address snapshots). All 39 tests pass against
the live Supabase Postgres database, and the concurrency tests were run
repeatedly to confirm they aren't flaky.

Beyond the automated suite, the full customer journey (browse → cart →
checkout → order success → track) and full admin journey (login → add
product → manage order → update status/courier) were manually click-tested
in a browser at both desktop and 390px mobile width, including: unauthorized
admin page/API access, invalid order tracking (wrong mobile, nonexistent
order), malicious file upload rejection, empty-file upload rejection, and
404 handling for unknown routes/products/categories.

## Production Build

There is no separate "build" step (no JS bundling). Before deploying:

```bash
pip install -r requirements.txt
alembic upgrade head
pytest
```

Run with a production ASGI server, e.g.:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

## Free Deployment (Render + Supabase)

Render's free Web Service tier has **no persistent disk** — anything written
to local disk (a SQLite file, an `uploads/` folder) would be wiped on every
restart/redeploy. That's why the database and product images both live in
Supabase instead (Postgres + Storage), which has its own persistent
infrastructure; Render only runs the stateless FastAPI app itself.

**GitHub repo**: https://github.com/johnsjoyfulgifts/johns-joyful-gifts
(private) — Render deploys straight from this repo.

To set up from scratch (e.g. a fork, or a new Render account):

1. **Supabase** — sign up at [supabase.com](https://supabase.com) (free,
   no card). Create a project, then from the dashboard collect:
   - Project Settings → Database → **Session pooler** connection string
     (not the direct connection — that's IPv6-only) → `DATABASE_URL`.
   - Project Settings → API → Project URL → `SUPABASE_URL`, and the
     `service_role`/"secret" key → `SUPABASE_SERVICE_KEY`.
   - Storage → create a bucket named `product-images`, set **Public**.
2. **Render** — sign up at [render.com](https://render.com) (free, no card).
   New → Web Service → connect the GitHub repo above. Render reads
   `render.yaml` in this repo automatically (Docker runtime, free plan). When
   prompted, fill in the env vars marked `sync: false` in `render.yaml`:
   `SECRET_KEY` (generate with
   `python -c "import secrets; print(secrets.token_hex(32))"`),
   `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (all from step 1).
3. Deploy. The Dockerfile's `CMD` runs `alembic upgrade head` automatically
   before starting the app, so the schema is created on first deploy.
4. Push to the connected branch to redeploy — Render rebuilds and deploys
   automatically on every push, no manual `deploy` command needed.
5. Create the first admin (run once, locally, pointed at the same
   `DATABASE_URL` your Render service uses):
   ```bash
   python scripts/create_admin.py
   ```

**Free-tier limits to be aware of**: Render's free Web Service gives 750
compute-hours/month and sleeps after 15 minutes idle (30-60s cold start on
the next request — the same cold-start trade-off most free hosts have).
Supabase's free tier gives 500MB Postgres storage and 1GB file storage, and
**pauses a project after 7 days of zero activity** (one click to resume in
the dashboard; no data is lost). All sufficient for a small shop's traffic,
worth monitoring as the business grows.

## Backup

The database lives in Supabase Postgres; product images live in Supabase
Storage. Back up the database with `pg_dump` against the same connection
string as `DATABASE_URL`:

```bash
pg_dump "postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres" > backup-$(date +%Y%m%d).sql
```

Do this on a schedule appropriate to your order volume — daily is reasonable
for a small shop. Product images in the `product-images` Storage bucket can
be downloaded in bulk from the Supabase dashboard (Storage → bucket →
download) if you want an offline copy.

## Troubleshooting

- **"Your session has expired. Please log in again."** — admin sessions last
  8 hours; just log back in at `/admin/login`.
- **Blank/500 page** — check server logs; customers only ever see a friendly
  message, but the real error and stack trace are always logged server-side.
- **Images not showing after deploy** — confirm `SUPABASE_URL` and
  `SUPABASE_SERVICE_KEY` are set correctly and the `product-images` bucket
  is set to **Public** in the Supabase dashboard.
- **Slow first request after idle** — both Render (free Web Service) and
  Supabase (project pause after 7 days idle) can need 30-60s to wake up on
  the first request after a period of no traffic. This is expected on free
  tiers, not a bug.

## Future Improvements

The storage and settings layers are deliberately abstracted so these can be
added later without a rewrite:

- Swap Supabase Storage for another provider via `app/storage.py` if needed.
- A free manual-UPI payment option (show a UPI ID/QR code at checkout, admin
  confirms payment manually) if online payment is wanted without reintroducing
  gateway fees — see "Zero-Cost Guarantee" above.
- Self-service password reset once/if a free email or SMS channel becomes
  available — currently a deliberate gap, handled by admin manual reset.
- WhatsApp Business API for automated order confirmations, if the business
  outgrows the manual `wa.me` deep-link flow (note: paid, would break the
  zero-cost guarantee unless budget changes).
