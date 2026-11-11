# The Tanning Co. Coventry — Salon Management System

Staff bed board, customer accounts, minutes, reports and campaigns for the Wyken and Earlsdon salons.

| Page | Who | What |
|---|---|---|
| `/` | Staff (signed in) | Bed board, customers, sales, queue, reports |
| `/login` | Staff | Sign in |
| `/customer` | Customers | Sign up, or check minutes with mobile number + PIN |

## Security

* **Staff must sign in.** Each person has their own account. Every API route is closed unless
  signed in. There are two roles:
  * **Staff**: bed board, customers, sales, daily report.
  * **Manager (admin)**: everything else too: campaigns, import/export, deleting customers,
    email/SMS settings, staff accounts and the activity log.
* **Customers can only see their own account.** They use their mobile number + their own PIN.
  The old default PIN `1234` no longer works. Existing customers need staff to set a PIN
  (Customer → *Set PIN*).
* Passwords and PINs are stored hashed (scrypt). Sessions use secure, HttpOnly cookies.
* Sign-ins are rate limited: 5 wrong passwords locks that account for 15 minutes.
* **Activity log** (manager only) records sign-ins, customer changes, sales, campaigns and settings changes.
* The site is hidden from search engines (`robots.txt` + `noindex`), and the automatic `/docs` API page is switched off.
* Everything customers type is escaped before it's shown on staff screens.

## First-time setup on Render

1. In the Render dashboard → your service → **Environment**, add:
   * `ADMIN_USERNAME`, for example `manager`
   * `ADMIN_PASSWORD`, a long password (at least 10 characters)
2. Deploy. On first start the app creates that manager account. Sign in at `/login`.
3. Go to **More → Staff accounts** and add an account for each member of staff.
4. You can remove `ADMIN_PASSWORD` from Render afterwards. It's only used when no staff exist.

### Keep your data safe (important)

The database is a single SQLite file. On Render's **free** plan the disk is wiped on every deploy
and restart, which means **all customers and minutes would be lost**. To keep data:

1. Change the service to a paid instance (Starter or above). This also stops the 30-second
   "waking up" delay.
2. Add a **Disk** (Settings → Disks) mounted at `/var/data`, 1 GB is plenty.
3. Set environment variable `DATABASE_PATH=/var/data/glowboard.db`.

Back up regularly with **More → Export customers (Excel)**. Render disks also keep daily snapshots.

### Optional environment variables

| Variable | Use |
|---|---|
| `SMTP_PASSWORD` | Email password. If set, it's used instead of the one saved in Settings. |
| `TWILIO_AUTH_TOKEN` | Twilio token. If set, it's used instead of the one saved in Settings. |
| `STAFF_SESSION_HOURS` | How long staff stay signed in (default 12). |
| `SEED_DEMO_DATA=1` | Adds two demo customers to an empty database (testing only). |

## Customer sign-up

Customers sign up at `/customer` (e.g. on a tablet at the desk or via a QR code). They give their name,
mobile, postcode, date of birth (must be 18+), salon, skin type, health declaration, marketing choices,
and choose a PIN.

New online sign-ups appear in **New sign-ups to check** on the bed board. Check photo ID, then press
**Mark ID checked**. Customers who choose **skin type 1** are flagged in red, because sunbeds aren't
recommended for that skin type.

## Marketing (UK GDPR / PECR)

Email and SMS campaigns **only go to customers who have opted in**. Online sign-ups choose for themselves.
For other customers, staff can tick the boxes in *Edit* once the customer has agreed.
Imported customers are not opted in automatically.

* Every email includes an unsubscribe link (`/unsubscribe`).
* Texts get "Reply STOP to opt out." added. Check that STOP handling is switched on for your Twilio number.

## Running locally

```bash
pip install -r requirements.txt
cd backend
ADMIN_USERNAME=manager ADMIN_PASSWORD='choose-a-long-password' python main.py
```

Then open http://localhost:8000 (staff) or http://localhost:8000/customer.

## Excel import

Columns **Name** and **Phone** are required. **Email** is optional. Numbers already on the system are skipped.

## Files

* `backend/main.py`: the app (FastAPI + SQLite)
* `backend/security.py`: password hashing, sessions, rate limiting
* `frontend/`: `index.html` (staff board), `login.html`, `customer.html`
* `static/css`, `static/js`: styles and scripts (kept out of the HTML so a strict security policy can be used)
