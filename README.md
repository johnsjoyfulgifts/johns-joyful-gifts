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
- Payment is **Cash on Delivery only** — no payment gateway, no transaction
  fees, no third-party account required to receive money. See "Zero-Cost
  Guarantee" below for why this is deliberate.

## Zero-Cost Guarantee

Every piece of this app was chosen specifically to avoid any ongoing cost:

| Need | What's used | Cost |
|---|---|---|
| Backend/hosting | FastAPI (Python), deployable on Fly.io's free allowance | Free |
| Database | SQLite (a file, not a hosted service) | Free |
| Image storage | Local disk on the same host | Free |
| Authentication | Self-hosted password hashing (bcrypt), no third-party auth service | Free |
| Customer contact | `wa.me` WhatsApp deep links (not the paid WhatsApp Business API) | Free |
| Payment | Cash on Delivery | Free |
| Domain | Free hosting subdomain (e.g. `*.fly.dev`) — no domain purchase needed | Free |

An earlier version of this app included an optional Razorpay online-payment
integration. It was **removed entirely** (not just disabled) because every
real payment gateway takes a percentage of each transaction — there is no
truly free way to accept card/UPI payments automatically, so keeping that
door open at all would contradict a hard zero-cost requirement. If online
payment is wanted in the future, the honest zero-cost equivalent is a manual
UPI option (show a UPI ID or QR code at checkout, customer pays directly with
any UPI app, admin manually confirms) — not built here, but straightforward
to add later without needing any of this app's other pieces to change.

**Free-tier limits still apply** — see "Free Deployment (Fly.io)" below. Free
doesn't mean unlimited: Fly.io's free allowance is a fixed amount of compute
and storage, sufficient for a small shop but worth monitoring as it grows.

## Architecture

A single monolithic FastAPI app — no separate frontend build, no JS framework,
no Node.js required at all:

- **Backend**: FastAPI (Python), server-rendered with Jinja2 templates.
- **Interactivity**: vanilla JS (`app/static/js/cart.js`) progressively enhances
  plain HTML forms — every action (add to cart, checkout, update order status)
  still works as a normal form POST if JavaScript fails.
- **Database**: SQLite via SQLAlchemy, with a dedicated locking strategy for
  order creation (see "Inventory safety" below).
- **Images**: stored on local disk under `uploads/`, served by FastAPI, behind
  a small abstraction (`app/storage.py`) so it can be swapped for S3/Cloudinary
  later without touching route code.
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
in `app/database.py` / `app/routers/checkout.py`:

- A **second SQLAlchemy engine** (`write_engine`), used only for order
  creation, is configured to issue `BEGIN IMMEDIATE` instead of SQLite's
  default deferred transaction — it takes the write lock the instant the
  transaction starts.
- Two concurrent checkouts for the same product **serialize**: the second one
  blocks until the first commits or rolls back, then re-reads genuinely
  current stock. There is no window where both can read stale stock and both
  succeed.
- Ordinary reads (page views, admin lists) stay on the main `engine` with
  plain deferred transactions in WAL mode, so they're never blocked by a
  checkout in progress.
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
| `DATABASE_URL` | SQLite file path, e.g. `sqlite:///./johns_joyful_gifts.db`. |
| `UPLOAD_DIR` | Folder for product images, e.g. `./uploads`. |
| `MAX_UPLOAD_SIZE_BYTES` | Per-image upload limit (default 5 MB). |
| `STORE_NAME`, `STORE_TAGLINE`, `WHATSAPP_NUMBER`, `INSTAGRAM_URL`, `DEFAULT_DELIVERY_CHARGE`, `FREE_DELIVERY_THRESHOLD` | Initial defaults — all editable later from `/admin/settings` without redeploying. |
| `ENVIRONMENT` | Set to `production` to enable secure (HTTPS-only) cookies. |

## Database Setup

Migrations are managed with Alembic.

```bash
alembic upgrade head
```

This creates `johns_joyful_gifts.db` (or your configured path) with the full
schema. Re-run `alembic upgrade head` after pulling any future migration —
it only ever applies forward, additive changes; it will never drop or reset
existing data.

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
keeping independent delivery-address snapshots). All 26 tests pass, and the
concurrency tests were run repeatedly to confirm they aren't flaky.

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

## Free Deployment (Fly.io)

Fly.io's free allowance includes a small persistent volume, which SQLite and
the local `uploads/` folder both need to survive restarts/redeploys — most
other free tiers (e.g. Vercel) only offer ephemeral storage, which would lose
your database and product photos on every deploy.

1. Install the Fly CLI and run `fly launch` from this directory (choose "no"
   when it offers to set up a Postgres database — this app uses SQLite).
2. Create a volume for persistent data: `fly volumes create data --size 1`.
3. In `fly.toml`, mount it, e.g.:
   ```toml
   [mounts]
     source = "data"
     destination = "/data"
   ```
4. Set `DATABASE_URL=sqlite:////data/johns_joyful_gifts.db` and
   `UPLOAD_DIR=/data/uploads` as Fly secrets/env vars, along with `SECRET_KEY`
   and `ENVIRONMENT=production`.
5. `fly deploy`, then run migrations and create the first admin via
   `fly ssh console` (`alembic upgrade head`, `python scripts/create_admin.py`).

**Free-tier limits to be aware of** (spec §54): Fly.io's free allowance is not
unlimited traffic/compute/storage forever — it's a small number of shared-cpu
VMs and a few GB of volume storage, sufficient for a small shop's traffic but
worth monitoring as the business grows. The app stays lightweight (SQLite,
no heavy dependencies) specifically so it fits comfortably within that.

## Backup

Since everything lives in one SQLite file plus the `uploads/` folder,
backup is just copying those two things periodically:

```bash
fly ssh sftp get /data/johns_joyful_gifts.db ./backup-$(date +%Y%m%d).db
```

(or the equivalent file copy on whatever host you use). Do this on a schedule
appropriate to your order volume — daily is reasonable for a small shop.

## Troubleshooting

- **"Your session has expired. Please log in again."** — admin sessions last
  8 hours; just log back in at `/admin/login`.
- **Blank/500 page** — check server logs; customers only ever see a friendly
  message, but the real error and stack trace are always logged server-side.
- **Images not showing after deploy** — confirm `UPLOAD_DIR` points at a
  path on your persistent volume, not ephemeral local disk.
- **"database is locked" errors under heavy load** — SQLite is fine for a
  small shop's order volume; if this becomes frequent, see "Future
  Improvements" below.

## Future Improvements

The storage and settings layers are deliberately abstracted so these can be
added later without a rewrite:

- Swap SQLite for hosted Postgres (e.g. Neon/Supabase free tier) if order
  volume grows enough that SQLite's single-writer model becomes limiting.
- Swap local image storage for S3/Cloudinary via `app/storage.py`.
- A free manual-UPI payment option (show a UPI ID/QR code at checkout, admin
  confirms payment manually) if online payment is wanted without reintroducing
  gateway fees — see "Zero-Cost Guarantee" above.
- Self-service password reset once/if a free email or SMS channel becomes
  available — currently a deliberate gap, handled by admin manual reset.
- WhatsApp Business API for automated order confirmations, if the business
  outgrows the manual `wa.me` deep-link flow (note: paid, would break the
  zero-cost guarantee unless budget changes).
