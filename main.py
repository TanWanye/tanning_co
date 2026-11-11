"""The Tanning Co. Coventry - salon management system.

Security model
--------------
* Staff sign in at /login. Every /api route is closed by default and needs a
  signed-in staff member; a few routes also need the "admin" role.
* Customers can only reach the public sign-up form and their own account
  (phone number + their own PIN). They never see the staff system.
* Sessions are random tokens in HttpOnly cookies; only a hash is stored.
* Passwords and PINs are hashed with scrypt.
* Logins and sign-ups are rate limited; important actions are written to an
  activity log.
"""
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, HTMLResponse, StreamingResponse, PlainTextResponse
from pydantic import BaseModel
from typing import List, Optional
from contextlib import contextmanager
from datetime import datetime, timedelta, date, timezone
import html
import io
import os
import re
import secrets
import smtplib
import sqlite3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from security import hash_secret, verify_secret, new_token, token_hash, RateLimiter

app = FastAPI(
    title="The Tanning Co. Coventry",
    # The automatic API documentation pages listed every endpoint publicly.
    docs_url=None, redoc_url=None, openapi_url=None,
)

# ---------------------------------------------------------------------------
# Paths and settings
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
STATIC_DIR = os.path.join(PROJECT_ROOT, "static")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "glowboard.db"))

STAFF_COOKIE = "ttc_staff"
CUSTOMER_COOKIE = "ttc_customer"
STAFF_SESSION_HOURS = int(os.environ.get("STAFF_SESSION_HOURS", "12"))
CUSTOMER_SESSION_MINUTES = 30
MIN_PASSWORD_LENGTH = 10
SHOPS = ("Wyken", "Earlsdon")
SKIN_TYPES = ("1", "2", "3", "4", "5", "6")

staff_login_limiter = RateLimiter(max_failures=5, window_seconds=900, lockout_seconds=900)
staff_login_ip_limiter = RateLimiter(max_failures=20, window_seconds=900, lockout_seconds=900)
customer_login_limiter = RateLimiter(max_failures=5, window_seconds=900, lockout_seconds=900)
customer_login_ip_limiter = RateLimiter(max_failures=20, window_seconds=900, lockout_seconds=900)
signup_limiter = RateLimiter(max_failures=10, window_seconds=3600, lockout_seconds=3600)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_column(c, table, column, coltype):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as conn:
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")

        c.execute('''CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT UNIQUE,
            email TEXT,
            orders INTEGER DEFAULT 0,
            visits INTEGER DEFAULT 0,
            avg_spend REAL DEFAULT 0,
            loyalty INTEGER DEFAULT 0,
            remaining_minutes INTEGER DEFAULT 0,
            pin TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        for col, typ in [
            ("postcode", "TEXT"), ("shop", "TEXT"), ("customer_number", "INTEGER"),
            ("package_type", "TEXT"), ("package_expires", "TEXT"),
            ("pin_hash", "TEXT"),            # hashed customer PIN (old plain 'pin' column is no longer used)
            ("dob", "TEXT"), ("skin_type", "TEXT"),
            ("declaration_at", "TEXT"),       # customer confirmed the health declaration
            ("marketing_email", "INTEGER"),   # 1 = opted in, 0 = opted out, NULL = not asked
            ("marketing_sms", "INTEGER"),
            ("consent_updated_at", "TEXT"),
            ("source", "TEXT"),               # staff / self / import
            ("id_checked_at", "TEXT"), ("id_checked_by", "TEXT"),
            ("unsubscribe_token", "TEXT"),
        ]:
            add_column(c, "customers", col, typ)
        # Old default PINs ('1234') were stored in plain text - stop using them.
        c.execute("UPDATE customers SET pin = NULL WHERE pin IS NOT NULL")

        c.execute('''CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT,
            status TEXT DEFAULT 'available',
            customer_name TEXT,
            remaining_minutes INTEGER DEFAULT 0,
            started_at TIMESTAMP
        )''')
        add_column(c, "rooms", "customer_id", "INTEGER")
        add_column(c, "rooms", "prepaid", "INTEGER DEFAULT 0")

        c.execute('''CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, price REAL NOT NULL, category TEXT, image TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER, total REAL, discount INTEGER DEFAULT 0,
            payment_method TEXT, items TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        add_column(c, "orders", "shop", "TEXT")
        add_column(c, "orders", "staff", "TEXT")
        c.execute('''CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT, room_name TEXT, shop TEXT,
            minutes INTEGER, amount REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        add_column(c, "sessions", "customer_id", "INTEGER")
        c.execute('''CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER, customer_name TEXT, items TEXT,
            total REAL, payment_method TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER, customer_id INTEGER, customer_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS sms_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            account_sid TEXT, auth_token TEXT, from_number TEXT
        )''')
        # This table was used by the email code but never created before.
        c.execute('''CREATE TABLE IF NOT EXISTS email_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            smtp_host TEXT, smtp_port INTEGER, smtp_user TEXT, smtp_password TEXT,
            from_email TEXT, from_name TEXT
        )''')

        # Staff accounts, sessions and the activity log
        c.execute('''CREATE TABLE IF NOT EXISTS staff_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            display_name TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS staff_sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS customer_sessions (
            token_hash TEXT PRIMARY KEY, customer_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            username TEXT, action TEXT, detail TEXT, ip TEXT
        )''')

        # Rooms: Wyken 1-6, Earlsdon 7-12
        if c.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] != 12:
            c.execute("DELETE FROM rooms")
            for i in range(1, 4):
                c.execute("INSERT INTO rooms (id, name, type) VALUES (?, ?, 'Stand-up')", (i, f"Wyken Stand-up {i}"))
            for i in range(4, 7):
                c.execute("INSERT INTO rooms (id, name, type) VALUES (?, ?, 'Lie-down')", (i, f"Wyken Lie-down {i}"))
            for i in range(7, 10):
                c.execute("INSERT INTO rooms (id, name, type) VALUES (?, ?, 'Stand-up')", (i, f"Earlsdon Stand-up {i-6}"))
            for i in range(10, 13):
                c.execute("INSERT INTO rooms (id, name, type) VALUES (?, ?, 'Lie-down')", (i, f"Earlsdon Lie-down {i-6}"))

        # Price list (refreshed on every start)
        c.execute("DELETE FROM products")
        c.executemany("INSERT INTO products (name, price, category, image) VALUES (?, ?, ?, ?)", [
            ("3 MIN", 7.00, "session", "/static/images/minutes.jpg"),
            ("6 MIN", 8.00, "session", "/static/images/minutes.jpg"),
            ("9 MIN", 10.00, "session", "/static/images/minutes.jpg"),
            ("12 MIN", 13.00, "session", "/static/images/minutes.jpg"),
            ("15 MIN", 18.00, "session", "/static/images/minutes.jpg"),
            ("30 MIN Pack", 30.00, "package", "/static/images/minutes.jpg"),
            ("60 MIN Pack", 39.00, "package", "/static/images/minutes.jpg"),
            ("100 MIN Pack", 56.00, "package", "/static/images/minutes.jpg"),
            ("120 MIN Pack", 61.00, "package", "/static/images/minutes.jpg"),
            ("Unlimited Weekly", 46.00, "unlimited", "/static/images/weekly.jpg"),
            ("Unlimited Monthly", 79.00, "unlimited", "/static/images/monthly.jpg"),
        ])

        # Give every customer an unsubscribe token for email links
        for row in c.execute("SELECT id FROM customers WHERE unsubscribe_token IS NULL").fetchall():
            c.execute("UPDATE customers SET unsubscribe_token = ? WHERE id = ?", (secrets.token_urlsafe(16), row["id"]))

        # Demo customers only when explicitly asked for (never on the live system)
        if os.environ.get("SEED_DEMO_DATA") == "1" and c.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            c.executemany("""INSERT INTO customers (name, phone, email, visits, loyalty, remaining_minutes, source, unsubscribe_token)
                             VALUES (?, ?, ?, ?, ?, ?, 'staff', ?)""", [
                ("Demo Customer", "07700900001", "demo1@example.com", 3, 20, 24, secrets.token_urlsafe(16)),
                ("Sample Person", "07700900002", "", 1, 5, 0, secrets.token_urlsafe(16)),
            ])

        # First admin account, from environment variables (only if there are no staff yet)
        if c.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0] == 0:
            admin_user = os.environ.get("ADMIN_USERNAME", "").strip()
            admin_pass = os.environ.get("ADMIN_PASSWORD", "")
            if admin_user and len(admin_pass) >= MIN_PASSWORD_LENGTH:
                c.execute("INSERT INTO staff_users (username, display_name, password_hash, role) VALUES (?, ?, ?, 'admin')",
                          (admin_user, admin_user, hash_secret(admin_pass)))
                print(f"Created admin account '{admin_user}'.")
            else:
                print("WARNING: no staff accounts exist. Set ADMIN_USERNAME and ADMIN_PASSWORD "
                      f"(at least {MIN_PASSWORD_LENGTH} characters) and restart.")

        c.execute("DELETE FROM staff_sessions WHERE expires_at < ?", (now_str(),))
        c.execute("DELETE FROM customer_sessions WHERE expires_at < ?", (now_str(),))


def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit(request: Request, action: str, detail: str = ""):
    user = getattr(request.state, "user", None)
    with db() as conn:
        conn.execute("INSERT INTO audit_log (username, action, detail, ip) VALUES (?, ?, ?, ?)",
                     (user["username"] if user else None, action, detail[:500], client_ip(request)))


def staff_name(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return (user["display_name"] or user["username"]) if user else ""


def shop_from_room(name: str) -> str:
    for s in SHOPS:
        if (name or "").startswith(s):
            return s
    return "Unknown"


def normalise_phone(phone: str) -> str:
    p = re.sub(r"[^\d+]", "", phone or "")
    if p.startswith("+44"):
        p = "0" + p[3:]
    elif p.startswith("44") and len(p) == 12:
        p = "0" + p[2:]
    return p


def valid_uk_mobile(phone: str) -> bool:
    return bool(re.fullmatch(r"07\d{9}", phone))


def valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)) and len(email) <= 200


def age_on(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def check_pin(pin: str):
    if not re.fullmatch(r"\d{4,6}", pin or ""):
        raise HTTPException(400, "PIN must be 4 to 6 digits")
    if len(set(pin)) == 1 or pin in ("1234", "12345", "123456", "4321", "2580", "1212", "0123"):
        raise HTTPException(400, "That PIN is too easy to guess - please choose another")


def check_password(pw: str):
    if len(pw or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


CUSTOMER_FIELDS = """id, name, phone, email, orders, visits, avg_spend, loyalty, remaining_minutes,
    created_at, postcode, shop, customer_number, package_type, package_expires, dob, skin_type,
    declaration_at, marketing_email, marketing_sms, consent_updated_at, source, id_checked_at,
    id_checked_by, (pin_hash IS NOT NULL) AS has_pin"""


def next_customer_number(c) -> int:
    return c.execute("SELECT COALESCE(MAX(customer_number), 0) + 1 FROM customers").fetchone()[0]


init_db()


# ---------------------------------------------------------------------------
# Access control middleware - every /api route is closed unless listed here
# ---------------------------------------------------------------------------
PUBLIC_PATHS = {
    "/login", "/customer", "/robots.txt", "/healthz", "/unsubscribe", "/favicon.ico",
    "/api/auth/login", "/api/auth/status",
    "/api/public/register",
    "/api/customer/login", "/api/customer/logout", "/api/customer/me", "/api/customer/my-history",
}
PUBLIC_PREFIXES = ("/static/",)
ADMIN_PREFIXES = ("/api/email/", "/api/sms/", "/api/import/", "/api/export/", "/api/staff", "/api/audit")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "X-Robots-Tag": "noindex, nofollow",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


def load_staff_user(request: Request):
    token = request.cookies.get(STAFF_COOKIE)
    if not token:
        return None
    with db() as conn:
        row = conn.execute("""SELECT u.id, u.username, u.display_name, u.role, u.active, s.expires_at
                              FROM staff_sessions s JOIN staff_users u ON u.id = s.user_id
                              WHERE s.token_hash = ?""", (token_hash(token),)).fetchone()
    if not row or not row["active"] or row["expires_at"] < now_str():
        return None
    return dict(row)


@app.middleware("http")
async def access_control(request: Request, call_next):
    path = request.url.path
    request.state.user = None
    is_api = path.startswith("/api/")
    public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)

    # Cross-site request protection: browsers won't add this header to a
    # cross-site request without permission (and we never grant it).
    if is_api and request.method in UNSAFE_METHODS and request.headers.get("x-requested-with") != "fetch":
        resp = JSONResponse({"detail": "Request blocked"}, status_code=403)
    else:
        user = None if public and path != "/api/auth/status" else load_staff_user(request)
        request.state.user = user
        if not public and user is None:
            if is_api:
                resp = JSONResponse({"detail": "Please sign in"}, status_code=401)
            else:
                resp = RedirectResponse("/login", status_code=303)
        elif not public and user["role"] != "admin" and (
                path.startswith(ADMIN_PREFIXES)
                or (request.method == "DELETE" and path.startswith("/api/customers/"))):
            resp = JSONResponse({"detail": "Only a manager (admin) can do that"}, status_code=403)
        else:
            resp = await call_next(request)

    for k, v in SECURITY_HEADERS.items():
        resp.headers[k] = v
    if request.url.scheme == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if is_api or path in ("/", "/login", "/customer"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def set_cookie(request: Request, response, name: str, value: str, max_age: int, same_site: str = "strict"):
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite=same_site,
                        secure=request.url.scheme == "https", path="/")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class StaffLogin(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class StaffCreate(BaseModel):
    username: str
    display_name: str = ""
    password: str
    role: str = "staff"


class StaffPassword(BaseModel):
    password: str


class StaffActive(BaseModel):
    active: bool


class CustomerCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = ""
    postcode: Optional[str] = ""
    shop: Optional[str] = None
    dob: Optional[str] = None
    skin_type: Optional[str] = None
    marketing_email: Optional[bool] = None
    marketing_sms: Optional[bool] = None


class PublicRegister(BaseModel):
    name: str
    phone: str
    email: Optional[str] = ""
    postcode: str
    dob: str
    shop: str
    skin_type: str
    declaration: bool
    terms: bool
    marketing_email: bool = False
    marketing_sms: bool = False
    pin: str
    website: Optional[str] = ""   # hidden field - only bots fill it in


class CustomerLogin(BaseModel):
    phone: str
    pin: str


class SetPin(BaseModel):
    pin: str


class OrderItem(BaseModel):
    id: int
    name: str
    price: float
    qty: int


class OrderCreate(BaseModel):
    customer_id: int
    items: List[OrderItem]
    discount: int = 0
    payment_method: str
    total: float = 0
    shop: Optional[str] = None


class AssignRoom(BaseModel):
    queue_id: int
    room_id: int


class UseMinutes(BaseModel):
    customer_id: int
    minutes: int
    room_id: int


class WaitlistAdd(BaseModel):
    room_id: int
    customer_name: str
    customer_id: Optional[int] = None


class EmailSettings(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str = ""
    from_email: str
    from_name: str = "The Tanning Co. Coventry"


class CampaignEmail(BaseModel):
    subject: str
    body: str
    customer_ids: Optional[List[int]] = None


class CampaignSMS(BaseModel):
    message: str
    customer_ids: Optional[List[int]] = None


class SMSSettings(BaseModel):
    account_sid: str
    auth_token: str = ""
    from_number: str


# ---------------------------------------------------------------------------
# Staff sign-in
# ---------------------------------------------------------------------------
@app.post("/api/auth/login")
def staff_login(data: StaffLogin, request: Request):
    username = data.username.strip().lower()
    ip = client_ip(request)
    key = f"{ip}|{username}"
    wait = staff_login_limiter.is_locked(key) or staff_login_ip_limiter.is_locked(ip)
    if wait:
        raise HTTPException(429, f"Too many attempts. Try again in {max(1, wait // 60)} minute(s).")

    with db() as conn:
        user = conn.execute("SELECT * FROM staff_users WHERE username = ?", (username,)).fetchone()
    ok = user is not None and user["active"] and verify_secret(data.password, user["password_hash"])
    if not ok:
        if user is None:
            verify_secret(data.password, hash_secret("timing-equaliser"))
        staff_login_limiter.fail(key)
        staff_login_ip_limiter.fail(ip)
        with db() as conn:
            conn.execute("INSERT INTO audit_log (username, action, detail, ip) VALUES (?, 'login_failed', '', ?)", (username, ip))
        raise HTTPException(401, "Wrong username or password")

    with db() as conn:
        staff_login_limiter.reset(key)
        token = new_token()
        expires = (datetime.now(timezone.utc) + timedelta(hours=STAFF_SESSION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO staff_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (token_hash(token), user["id"], expires))
        conn.execute("UPDATE staff_users SET last_login_at = ? WHERE id = ?", (now_str(), user["id"]))
        conn.execute("INSERT INTO audit_log (username, action, detail, ip) VALUES (?, 'login', '', ?)", (user["username"], ip))

    resp = JSONResponse({"ok": True})
    set_cookie(request, resp, STAFF_COOKIE, token, STAFF_SESSION_HOURS * 3600)
    return resp


@app.get("/api/auth/status")
def auth_status(request: Request):
    with db() as conn:
        has_staff = conn.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0] > 0
    return {"signed_in": request.state.user is not None, "setup_needed": not has_staff}


@app.post("/api/auth/logout")
def staff_logout(request: Request):
    token = request.cookies.get(STAFF_COOKIE)
    if token:
        with db() as conn:
            conn.execute("DELETE FROM staff_sessions WHERE token_hash = ?", (token_hash(token),))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(STAFF_COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def staff_me(request: Request):
    u = request.state.user
    return {"username": u["username"], "display_name": u["display_name"] or u["username"], "role": u["role"]}


@app.post("/api/auth/password")
def change_own_password(data: PasswordChange, request: Request):
    check_password(data.new_password)
    uid = request.state.user["id"]
    with db() as conn:
        row = conn.execute("SELECT password_hash FROM staff_users WHERE id = ?", (uid,)).fetchone()
        if not verify_secret(data.current_password, row["password_hash"]):
            raise HTTPException(400, "Current password is wrong")
        conn.execute("UPDATE staff_users SET password_hash = ? WHERE id = ?", (hash_secret(data.new_password), uid))
        # Sign out other devices
        current = token_hash(request.cookies.get(STAFF_COOKIE, ""))
        conn.execute("DELETE FROM staff_sessions WHERE user_id = ? AND token_hash != ?", (uid, current))
    audit(request, "password_changed")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Staff accounts (admin only)
# ---------------------------------------------------------------------------
@app.get("/api/staff")
def list_staff():
    with db() as conn:
        rows = conn.execute("""SELECT id, username, display_name, role, active, created_at, last_login_at
                               FROM staff_users ORDER BY active DESC, username""").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/staff")
def create_staff(data: StaffCreate, request: Request):
    username = data.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", username):
        raise HTTPException(400, "Username: 3-30 letters, numbers, dots, dashes or underscores")
    if data.role not in ("staff", "admin"):
        raise HTTPException(400, "Role must be staff or admin")
    check_password(data.password)
    try:
        with db() as conn:
            conn.execute("INSERT INTO staff_users (username, display_name, password_hash, role) VALUES (?, ?, ?, ?)",
                         (username, data.display_name.strip()[:60] or username, hash_secret(data.password), data.role))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That username is already taken")
    audit(request, "staff_created", f"{username} ({data.role})")
    return {"ok": True}


@app.post("/api/staff/{staff_id}/password")
def reset_staff_password(staff_id: int, data: StaffPassword, request: Request):
    check_password(data.password)
    with db() as conn:
        row = conn.execute("SELECT username FROM staff_users WHERE id = ?", (staff_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        conn.execute("UPDATE staff_users SET password_hash = ? WHERE id = ?", (hash_secret(data.password), staff_id))
        conn.execute("DELETE FROM staff_sessions WHERE user_id = ?", (staff_id,))
    audit(request, "staff_password_reset", row["username"])
    return {"ok": True}


@app.post("/api/staff/{staff_id}/active")
def set_staff_active(staff_id: int, data: StaffActive, request: Request):
    if staff_id == request.state.user["id"] and not data.active:
        raise HTTPException(400, "You can't switch off your own account")
    with db() as conn:
        row = conn.execute("SELECT username, role FROM staff_users WHERE id = ?", (staff_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        conn.execute("UPDATE staff_users SET active = ? WHERE id = ?", (1 if data.active else 0, staff_id))
        if not data.active:
            conn.execute("DELETE FROM staff_sessions WHERE user_id = ?", (staff_id,))
    audit(request, "staff_enabled" if data.active else "staff_disabled", row["username"])
    return {"ok": True}


@app.get("/api/audit")
def audit_log(limit: int = 200):
    with db() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 1000),)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Rooms, customers, orders (staff)
# ---------------------------------------------------------------------------
@app.get("/api/rooms")
def get_rooms():
    with db() as conn:
        rooms = conn.execute("SELECT id, name, type, status, customer_name, customer_id, remaining_minutes, started_at FROM rooms ORDER BY id").fetchall()
    out = []
    for r in rooms:
        d = dict(r)
        d["shop"] = shop_from_room(d["name"])
        out.append(d)
    return out


@app.get("/api/customers")
def search_customers(q: str = ""):
    with db() as conn:
        q = q.strip()
        if q:
            like = f"%{q}%"
            digits = normalise_phone(q)
            rows = conn.execute(f"""SELECT {CUSTOMER_FIELDS} FROM customers
                                    WHERE name LIKE ? OR phone LIKE ? OR email LIKE ? OR CAST(customer_number AS TEXT) = ?
                                    ORDER BY name LIMIT 50""",
                                (like, f"%{digits or q}%", like, q)).fetchall()
        else:
            rows = conn.execute(f"SELECT {CUSTOMER_FIELDS} FROM customers ORDER BY id DESC LIMIT 50").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/customers/recent-signups")
def recent_signups():
    """Customers who signed themselves up and haven't had ID checked yet."""
    with db() as conn:
        rows = conn.execute(f"""SELECT {CUSTOMER_FIELDS} FROM customers
                                WHERE source = 'self' AND id_checked_at IS NULL
                                ORDER BY id DESC LIMIT 20""").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/customers/{customer_id}")
def get_customer(customer_id: int):
    with db() as conn:
        row = conn.execute(f"SELECT {CUSTOMER_FIELDS} FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Customer not found")
    return dict(row)


def validate_customer_fields(data: CustomerCreate):
    name = data.name.strip()
    phone = normalise_phone(data.phone)
    if not name or len(name) > 80:
        raise HTTPException(400, "Please enter a name (up to 80 characters)")
    if not phone or len(phone) < 10 or len(phone) > 15:
        raise HTTPException(400, "Please enter a valid phone number")
    email = (data.email or "").strip()
    if email and not valid_email(email):
        raise HTTPException(400, "That email address doesn't look right")
    shop = data.shop if data.shop in SHOPS else None
    skin = data.skin_type if data.skin_type in SKIN_TYPES else None
    dob = None
    if data.dob:
        try:
            dob = date.fromisoformat(data.dob).isoformat()
        except ValueError:
            raise HTTPException(400, "Date of birth is not a valid date")
    return name, phone, email, (data.postcode or "").strip().upper()[:10], shop, skin, dob


def bool_or_none(v):
    return None if v is None else (1 if v else 0)


@app.post("/api/customers")
def create_customer(data: CustomerCreate, request: Request):
    name, phone, email, postcode, shop, skin, dob = validate_customer_fields(data)
    try:
        with db() as conn:
            c = conn.cursor()
            c.execute("""INSERT INTO customers (name, phone, email, postcode, shop, customer_number, skin_type, dob,
                                                 marketing_email, marketing_sms, consent_updated_at, source, unsubscribe_token)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'staff', ?)""",
                      (name, phone, email, postcode, shop, next_customer_number(c), skin, dob,
                       bool_or_none(data.marketing_email), bool_or_none(data.marketing_sms),
                       now_str() if data.marketing_email is not None or data.marketing_sms is not None else None,
                       secrets.token_urlsafe(16)))
            new_id = c.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "A customer with that phone number already exists")
    audit(request, "customer_created", f"#{new_id} {name}")
    return {"id": new_id, "message": "Customer created"}


@app.put("/api/customers/{customer_id}")
def update_customer(customer_id: int, data: CustomerCreate, request: Request):
    name, phone, email, postcode, shop, skin, dob = validate_customer_fields(data)
    try:
        with db() as conn:
            old = conn.execute("SELECT marketing_email, marketing_sms FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if not old:
                raise HTTPException(404, "Customer not found")
            me, ms = bool_or_none(data.marketing_email), bool_or_none(data.marketing_sms)
            consent_changed = (me, ms) != (old["marketing_email"], old["marketing_sms"])
            conn.execute("""UPDATE customers SET name = ?, phone = ?, email = ?, postcode = ?, shop = ?,
                                   skin_type = ?, dob = ?, marketing_email = ?, marketing_sms = ?,
                                   consent_updated_at = CASE WHEN ? THEN ? ELSE consent_updated_at END
                            WHERE id = ?""",
                         (name, phone, email, postcode, shop, skin, dob, me, ms,
                          1 if consent_changed else 0, now_str(), customer_id))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Another customer already has that phone number")
    audit(request, "customer_updated", f"#{customer_id} {name}")
    return {"message": "Customer updated"}


@app.delete("/api/customers/{customer_id}")
def delete_customer(customer_id: int, request: Request):
    with db() as conn:
        row = conn.execute("SELECT name FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Customer not found")
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        conn.execute("DELETE FROM customer_sessions WHERE customer_id = ?", (customer_id,))
    audit(request, "customer_deleted", f"#{customer_id} {row['name']}")
    return {"message": "Customer deleted"}


@app.post("/api/customers/{customer_id}/pin")
def set_customer_pin(customer_id: int, data: SetPin, request: Request):
    check_pin(data.pin)
    with db() as conn:
        c = conn.execute("UPDATE customers SET pin_hash = ? WHERE id = ?", (hash_secret(data.pin), customer_id))
        if c.rowcount == 0:
            raise HTTPException(404, "Customer not found")
        conn.execute("DELETE FROM customer_sessions WHERE customer_id = ?", (customer_id,))
    audit(request, "customer_pin_set", f"#{customer_id}")
    return {"ok": True}


@app.post("/api/customers/{customer_id}/id-check")
def mark_id_checked(customer_id: int, request: Request):
    with db() as conn:
        c = conn.execute("UPDATE customers SET id_checked_at = ?, id_checked_by = ? WHERE id = ?",
                         (now_str(), staff_name(request), customer_id))
        if c.rowcount == 0:
            raise HTTPException(404, "Customer not found")
    audit(request, "customer_id_checked", f"#{customer_id}")
    return {"ok": True}


@app.get("/api/products")
def get_products():
    with db() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    return [dict(p) for p in rows]


@app.post("/api/orders")
def create_order(order: OrderCreate, request: Request):
    if not order.items:
        raise HTTPException(400, "No items")
    with db() as conn:
        c = conn.cursor()
        customer = c.execute("SELECT name FROM customers WHERE id = ?", (order.customer_id,)).fetchone()
        if not customer:
            raise HTTPException(404, "Customer not found")

        # Use the real prices from the price list, not whatever the browser sent
        priced = []
        for i in order.items:
            p = c.execute("SELECT id, name, price FROM products WHERE id = ?", (i.id,)).fetchone()
            if not p or i.qty < 1 or i.qty > 20:
                raise HTTPException(400, "Unknown product or quantity")
            priced.append((p["name"], p["price"], i.qty))
        discount = min(max(order.discount, 0), 100)
        total = round(sum(price * qty for _, price, qty in priced) * (100 - discount) / 100, 2)
        items_str = ", ".join(f"{qty}x {name}" for name, _, qty in priced)
        shop = order.shop if order.shop in SHOPS else "Unknown"

        c.execute("""INSERT INTO orders (customer_id, total, discount, payment_method, items, shop, staff)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (order.customer_id, total, discount, order.payment_method[:40], items_str, shop, staff_name(request)))
        order_id = c.lastrowid

        is_package_only = all(("pack" in n.lower() or "unlimited" in n.lower() or "min" in n.lower()) for n, _, _ in priced)
        if not is_package_only:
            c.execute("INSERT INTO queue (customer_id, customer_name, items, total, payment_method) VALUES (?, ?, ?, ?, ?)",
                      (order.customer_id, customer["name"], items_str, total, order.payment_method[:40]))

        c.execute("""UPDATE customers SET orders = orders + 1, visits = visits + 1, loyalty = loyalty + ?,
                            avg_spend = (avg_spend * orders + ?) / (orders + 1)
                     WHERE id = ?""", (int(total), total, order.customer_id))

        for name, _, qty in priced:
            n = name.lower()
            if "min pack" in n:
                mins = next((m for m in (120, 100, 60, 30) if str(m) in n), 0)
                c.execute("UPDATE customers SET remaining_minutes = remaining_minutes + ? WHERE id = ?", (mins * qty, order.customer_id))
            elif " min" in n and "unlimited" not in n:
                mins = next((m for m in (15, 12, 9, 6, 3) if str(m) in n), 0)
                c.execute("UPDATE customers SET remaining_minutes = remaining_minutes + ? WHERE id = ?", (mins * qty, order.customer_id))
            elif "unlimited weekly" in n:
                expires = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
                c.execute("""UPDATE customers SET package_type = 'weekly', package_expires = ?,
                                    remaining_minutes = remaining_minutes + 105 WHERE id = ?""", (expires, order.customer_id))
            elif "unlimited monthly" in n:
                expires = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                c.execute("""UPDATE customers SET package_type = 'monthly', package_expires = ?,
                                    remaining_minutes = remaining_minutes + 450 WHERE id = ?""", (expires, order.customer_id))
    audit(request, "order_created", f"#{order_id} customer #{order.customer_id}: {items_str} £{total:.2f}")
    return {"order_id": order_id, "total": total, "message": "Order created"}


@app.get("/api/queue")
def get_queue():
    with db() as conn:
        rows = conn.execute("SELECT * FROM queue ORDER BY created_at").fetchall()
    return [dict(q) for q in rows]


@app.post("/api/queue/{queue_id}/assign")
def assign_room(queue_id: int, data: AssignRoom, request: Request):
    with db() as conn:
        c = conn.cursor()
        q = c.execute("SELECT * FROM queue WHERE id = ?", (queue_id,)).fetchone()
        if not q:
            raise HTTPException(404, "Queue item not found")
        room = c.execute("SELECT * FROM rooms WHERE id = ?", (data.room_id,)).fetchone()
        if not room or room["status"] != "available":
            raise HTTPException(400, "Bed not available")
        items = (q["items"] or "").lower()
        minutes = next((m for m in (15, 12, 9, 6, 3) if f"{m} min" in items), 10)
        c.execute("""UPDATE rooms SET status = 'occupied', customer_name = ?, customer_id = ?, prepaid = 0,
                            remaining_minutes = ?, started_at = CURRENT_TIMESTAMP WHERE id = ?""",
                  (q["customer_name"], q["customer_id"], minutes, data.room_id))
        c.execute("DELETE FROM queue WHERE id = ?", (queue_id,))
    audit(request, "bed_assigned", f"{room['name']} ← {q['customer_name']} ({minutes} min)")
    return {"message": "Room assigned", "minutes": minutes}


@app.delete("/api/queue/{queue_id}")
def cancel_queue(queue_id: int, request: Request):
    with db() as conn:
        conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))
    audit(request, "queue_removed", f"#{queue_id}")
    return {"message": "Removed from queue"}


@app.post("/api/rooms/{room_id}/clean")
def mark_clean(room_id: int, request: Request):
    with db() as conn:
        conn.execute("""UPDATE rooms SET status = 'available', customer_name = NULL, customer_id = NULL,
                               remaining_minutes = 0, started_at = NULL, prepaid = 0
                        WHERE id = ? AND status = 'cleaning'""", (room_id,))
    return {"message": "Bed available"}


@app.post("/api/rooms/{room_id}/finish")
def finish_session(room_id: int, request: Request):
    """End a session, log it and mark the bed for cleaning."""
    with db() as conn:
        c = conn.cursor()
        room = c.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not room or room["status"] != "occupied":
            raise HTTPException(400, "Bed is not in use")
        minutes_used = room["remaining_minutes"] or 0
        # Minutes were already taken off when "Use minutes" was used (prepaid),
        # so only deduct here for sessions started from the queue.
        if not room["prepaid"] and minutes_used > 0:
            if room["customer_id"]:
                c.execute("UPDATE customers SET remaining_minutes = MAX(0, remaining_minutes - ?) WHERE id = ?",
                          (minutes_used, room["customer_id"]))
        c.execute("INSERT INTO sessions (customer_name, customer_id, room_name, shop, minutes) VALUES (?, ?, ?, ?, ?)",
                  (room["customer_name"], room["customer_id"], room["name"], shop_from_room(room["name"]), minutes_used))
        c.execute("UPDATE rooms SET status = 'cleaning', prepaid = 0 WHERE id = ?", (room_id,))
    audit(request, "session_finished", f"{room['name']} ({room['customer_name']}, {minutes_used} min)")
    return {"message": "Session finished", "minutes": minutes_used}


@app.post("/api/use-minutes")
def use_minutes(data: UseMinutes, request: Request):
    """Take minutes off a customer's balance and start them on a bed."""
    if data.minutes < 1 or data.minutes > 30:
        raise HTTPException(400, "Minutes must be between 1 and 30")
    with db() as conn:
        c = conn.cursor()
        customer = c.execute("SELECT * FROM customers WHERE id = ?", (data.customer_id,)).fetchone()
        if not customer:
            raise HTTPException(404, "Customer not found")
        if (customer["remaining_minutes"] or 0) < data.minutes:
            raise HTTPException(400, "Not enough minutes")
        room = c.execute("SELECT * FROM rooms WHERE id = ?", (data.room_id,)).fetchone()
        if not room or room["status"] != "available":
            raise HTTPException(400, "Bed not available")
        c.execute("UPDATE customers SET remaining_minutes = remaining_minutes - ?, visits = visits + 1 WHERE id = ?",
                  (data.minutes, data.customer_id))
        c.execute("""UPDATE rooms SET status = 'occupied', customer_name = ?, customer_id = ?, prepaid = 1,
                            remaining_minutes = ?, started_at = CURRENT_TIMESTAMP WHERE id = ?""",
                  (customer["name"], data.customer_id, data.minutes, data.room_id))
    audit(request, "bed_assigned", f"{room['name']} ← {customer['name']} ({data.minutes} min)")
    return {"message": "Bed assigned", "minutes_used": data.minutes}


@app.get("/api/customer/{customer_id}/history")
def customer_history(customer_id: int):
    with db() as conn:
        rows = conn.execute("SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20", (customer_id,)).fetchall()
    return [dict(o) for o in rows]


@app.get("/api/customer/{customer_id}/bed-usage")
def customer_bed_usage(customer_id: int):
    with db() as conn:
        rows = conn.execute("""SELECT room_name, minutes, created_at FROM sessions
                               WHERE customer_id = ? ORDER BY created_at DESC LIMIT 10""", (customer_id,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/waitlist/{room_id}")
def get_waitlist(room_id: int):
    with db() as conn:
        rows = conn.execute("SELECT * FROM waitlist WHERE room_id = ? ORDER BY created_at", (room_id,)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/waitlist")
def add_to_waitlist(data: WaitlistAdd):
    name = data.customer_name.strip()[:80]
    if not name:
        raise HTTPException(400, "Enter a name")
    with db() as conn:
        conn.execute("INSERT INTO waitlist (room_id, customer_id, customer_name) VALUES (?, ?, ?)",
                     (data.room_id, data.customer_id, name))
    return {"message": "Added to waiting list"}


@app.delete("/api/waitlist/{wait_id}")
def remove_from_waitlist(wait_id: int):
    with db() as conn:
        conn.execute("DELETE FROM waitlist WHERE id = ?", (wait_id,))
    return {"message": "Removed"}


@app.get("/api/report/daily")
def daily_report(date: str = None):
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    products_list = ["3 MIN", "6 MIN", "9 MIN", "12 MIN", "15 MIN", "30 MIN Pack", "60 MIN Pack",
                     "100 MIN Pack", "120 MIN Pack", "Unlimited Weekly", "Unlimited Monthly"]
    with db() as conn:
        sessions = conn.execute("SELECT * FROM sessions WHERE date(created_at) = ? ORDER BY created_at", (date,)).fetchall()
        orders = conn.execute("SELECT total, shop, items FROM orders WHERE date(created_at) = ?", (date,)).fetchall()
        minutes_left = {s: conn.execute("SELECT COALESCE(SUM(remaining_minutes), 0) FROM customers WHERE shop = ?", (s,)).fetchone()[0]
                        for s in SHOPS}

    shops = {s: {"sessions": [], "minutes": 0, "money": 0, "minutes_left": minutes_left[s],
                 "breakdown": {p: 0 for p in products_list}} for s in SHOPS}
    total_minutes = total_money = 0
    for o in orders:
        amt = o["total"] or 0
        total_money += amt
        if o["shop"] in shops:
            shops[o["shop"]]["money"] += amt
            items = (o["items"] or "").lower()
            # Match the longest product name first so "15 MIN" doesn't swallow "15 MIN Pack" etc.
            for p in sorted(products_list, key=len, reverse=True):
                if p.lower() in items:
                    shops[o["shop"]]["breakdown"][p] += amt
                    break
    for s in sessions:
        s = dict(s)
        shop = s["shop"] if s["shop"] in shops else "Wyken"
        shops[shop]["sessions"].append(s)
        shops[shop]["minutes"] += s["minutes"] or 0
        total_minutes += s["minutes"] or 0
    return {"date": date, "shops": shops, "total_minutes": total_minutes,
            "total_money": round(total_money, 2), "order_count": len(orders)}


# ---------------------------------------------------------------------------
# Import / export (admin)
# ---------------------------------------------------------------------------
@app.post("/api/import/excel")
async def import_excel(request: Request, file: UploadFile = File(...)):
    import pandas as pd
    if not (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload an Excel file (.xlsx or .xls)")
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(400, "File is too large (5 MB maximum)")
    try:
        df = pd.read_excel(io.BytesIO(contents))
    except Exception:
        raise HTTPException(400, "Could not read that Excel file")

    df.columns = [str(c).strip().lower() for c in df.columns]
    name_col = next((c for c in df.columns if c in ("name", "customer", "full name", "customer name")), None)
    phone_col = next((c for c in df.columns if c in ("phone", "mobile", "telephone", "phone number", "mobile number")), None)
    email_col = next((c for c in df.columns if c in ("email", "e-mail", "email address")), None)
    if not name_col or not phone_col:
        raise HTTPException(400, "Excel must have at least 'Name' and 'Phone' columns")

    imported = skipped = 0
    with db() as conn:
        c = conn.cursor()
        for _, row in df.iterrows():
            name = str(row[name_col]).strip()[:80] if pd.notna(row[name_col]) else ""
            phone = normalise_phone(str(row[phone_col])) if pd.notna(row[phone_col]) else ""
            email = str(row[email_col]).strip() if email_col and pd.notna(row[email_col]) else ""
            if email and not valid_email(email):
                email = ""
            if not name or len(phone) < 10:
                skipped += 1
                continue
            try:
                c.execute("""INSERT INTO customers (name, phone, email, customer_number, source, unsubscribe_token)
                             VALUES (?, ?, ?, ?, 'import', ?)""",
                          (name, phone, email, next_customer_number(c), secrets.token_urlsafe(16)))
                imported += 1
            except sqlite3.IntegrityError:
                skipped += 1
    audit(request, "customers_imported", f"{imported} imported, {skipped} skipped from {file.filename}")
    return {"imported": imported, "skipped": skipped, "total_rows": len(df)}


@app.get("/api/export/customers")
def export_customers(request: Request):
    import pandas as pd
    with db() as conn:
        df = pd.read_sql_query("""SELECT customer_number, name, phone, email, postcode, shop, remaining_minutes,
                                         package_type, package_expires, visits, loyalty, marketing_email, marketing_sms, created_at
                                  FROM customers ORDER BY id""", conn)
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    audit(request, "customers_exported", f"{len(df)} customers")
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=customers_backup.xlsx"})


# ---------------------------------------------------------------------------
# Email and SMS campaigns (admin) - only to customers who opted in
# ---------------------------------------------------------------------------
@app.get("/api/email/settings")
def get_email_settings():
    with db() as conn:
        row = conn.execute("SELECT smtp_host, smtp_port, smtp_user, from_email, from_name, (smtp_password IS NOT NULL AND smtp_password != '') AS has_password FROM email_settings WHERE id = 1").fetchone()
    d = dict(row) if row else {}
    d["password_from_env"] = bool(os.environ.get("SMTP_PASSWORD"))
    return d


@app.post("/api/email/settings")
def save_email_settings(settings: EmailSettings, request: Request):
    with db() as conn:
        old = conn.execute("SELECT smtp_password FROM email_settings WHERE id = 1").fetchone()
        password = settings.smtp_password or (old["smtp_password"] if old else "")
        conn.execute("""INSERT OR REPLACE INTO email_settings (id, smtp_host, smtp_port, smtp_user, smtp_password, from_email, from_name)
                        VALUES (1, ?, ?, ?, ?, ?, ?)""",
                     (settings.smtp_host.strip(), settings.smtp_port, settings.smtp_user.strip(), password,
                      settings.from_email.strip(), settings.from_name.strip()))
    audit(request, "email_settings_changed")
    return {"message": "Email settings saved"}


def campaign_recipients(conn, channel: str, ids: Optional[List[int]]):
    field, contact = ("marketing_email", "email") if channel == "email" else ("marketing_sms", "phone")
    sql = f"SELECT id, name, {contact} AS contact, unsubscribe_token FROM customers WHERE {field} = 1 AND {contact} IS NOT NULL AND {contact} != ''"
    params = []
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params = ids
    return conn.execute(sql, params).fetchall()


@app.get("/api/campaign/counts")
def campaign_counts():
    with db() as conn:
        email = len(campaign_recipients(conn, "email", None))
        sms = len(campaign_recipients(conn, "sms", None))
    return {"email": email, "sms": sms}


@app.post("/api/email/campaign")
def send_campaign(campaign: CampaignEmail, request: Request):
    with db() as conn:
        settings = conn.execute("SELECT * FROM email_settings WHERE id = 1").fetchone()
        if not settings or not settings["smtp_host"]:
            raise HTTPException(400, "Email settings not set up yet. Go to Settings first.")
        customers = campaign_recipients(conn, "email", campaign.customer_ids)
    if not customers:
        raise HTTPException(400, "No customers have opted in to marketing emails yet")

    password = os.environ.get("SMTP_PASSWORD") or settings["smtp_password"]
    base_url = str(request.base_url).rstrip("/")
    sent = failed = 0
    errors = []
    try:
        server = smtplib.SMTP(settings["smtp_host"], settings["smtp_port"] or 587, timeout=20)
        server.starttls()
        server.login(settings["smtp_user"], password)
        for cust in customers:
            try:
                msg = MIMEMultipart()
                msg["From"] = f"{settings['from_name']} <{settings['from_email']}>"
                msg["To"] = cust["contact"]
                msg["Subject"] = campaign.subject
                unsub = f"{base_url}/unsubscribe?t={cust['unsubscribe_token']}"
                msg["List-Unsubscribe"] = f"<{unsub}>"
                body = campaign.body.replace("{name}", html.escape(cust["name"]))
                footer = f"""
                <br><br><hr style="border:none;border-top:1px solid #ddd;margin:20px 0">
                <small style="color:#666"><strong>The Tanning Co. Coventry</strong><br>
                Wyken: 50 Torcross Ave, CV2 3NQ &bull; 024 7665 9966<br>
                Earlsdon: 116 Albany Rd, CV5 6NG &bull; 024 7667 4922<br>
                www.thetanningco.co.uk<br><br>
                You're getting this because you asked for offers from us.
                <a href="{unsub}">Unsubscribe</a></small>"""
                msg.attach(MIMEText(body + footer, "html"))
                server.send_message(msg)
                sent += 1
            except Exception as e:
                failed += 1
                errors.append(f"{cust['contact']}: {e}")
        server.quit()
    except Exception as e:
        raise HTTPException(500, f"Could not connect to the email server: {e}")
    audit(request, "email_campaign_sent", f"'{campaign.subject}' to {sent} ({failed} failed)")
    return {"sent": sent, "failed": failed, "errors": errors[:5]}


@app.get("/api/sms/settings")
def get_sms_settings():
    with db() as conn:
        row = conn.execute("SELECT account_sid, from_number, (auth_token IS NOT NULL AND auth_token != '') AS has_token FROM sms_settings WHERE id = 1").fetchone()
    if row:
        return {"account_sid": row["account_sid"], "from_number": row["from_number"],
                "has_token": bool(row["has_token"]) or bool(os.environ.get("TWILIO_AUTH_TOKEN")), "configured": True}
    return {"configured": False, "has_token": bool(os.environ.get("TWILIO_AUTH_TOKEN"))}


@app.post("/api/sms/settings")
def save_sms_settings(settings: SMSSettings, request: Request):
    with db() as conn:
        old = conn.execute("SELECT auth_token FROM sms_settings WHERE id = 1").fetchone()
        token = settings.auth_token or (old["auth_token"] if old else "")
        conn.execute("INSERT OR REPLACE INTO sms_settings (id, account_sid, auth_token, from_number) VALUES (1, ?, ?, ?)",
                     (settings.account_sid.strip(), token, settings.from_number.strip()))
    audit(request, "sms_settings_changed")
    return {"message": "SMS settings saved"}


@app.post("/api/sms/campaign")
def send_sms_campaign(campaign: CampaignSMS, request: Request):
    from twilio.rest import Client as TwilioClient
    with db() as conn:
        settings = conn.execute("SELECT * FROM sms_settings WHERE id = 1").fetchone()
        if not settings or not settings["account_sid"]:
            raise HTTPException(400, "SMS settings not set up yet. Go to Settings first.")
        customers = campaign_recipients(conn, "sms", campaign.customer_ids)
    if not customers:
        raise HTTPException(400, "No customers have opted in to marketing texts yet")

    message = campaign.message.strip()
    if "stop" not in message.lower():
        message += " Reply STOP to opt out."
    client = TwilioClient(settings["account_sid"], os.environ.get("TWILIO_AUTH_TOKEN") or settings["auth_token"])
    sent = failed = 0
    errors = []
    for cust in customers:
        try:
            phone = cust["contact"].strip()
            phone = "+44" + phone[1:] if phone.startswith("0") else (phone if phone.startswith("+") else "+44" + phone)
            client.messages.create(body=message.replace("{name}", cust["name"]), from_=settings["from_number"], to=phone)
            sent += 1
        except Exception as e:
            failed += 1
            errors.append(f"{cust['contact']}: {e}")
    audit(request, "sms_campaign_sent", f"to {sent} ({failed} failed)")
    return {"sent": sent, "failed": failed, "errors": errors[:5]}


# ---------------------------------------------------------------------------
# Public: customer sign-up and "check my minutes"
# ---------------------------------------------------------------------------
@app.post("/api/public/register")
def public_register(data: PublicRegister, request: Request):
    ip = client_ip(request)
    if not signup_limiter.hit(ip):
        raise HTTPException(429, "Too many sign-ups from this connection. Please ask at the desk.")
    if data.website:   # honeypot field filled in = a bot
        raise HTTPException(400, "Sign-up failed")

    name = re.sub(r"\s+", " ", data.name).strip()
    if len(name) < 2 or len(name) > 80:
        raise HTTPException(400, "Please enter your full name")
    phone = normalise_phone(data.phone)
    if not valid_uk_mobile(phone):
        raise HTTPException(400, "Please enter a UK mobile number starting 07")
    email = (data.email or "").strip()
    if email and not valid_email(email):
        raise HTTPException(400, "That email address doesn't look right")
    if data.marketing_email and not email:
        raise HTTPException(400, "Add an email address to get offers by email")
    postcode = data.postcode.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}", postcode):
        raise HTTPException(400, "Please enter a valid UK postcode")
    try:
        dob = date.fromisoformat(data.dob)
    except ValueError:
        raise HTTPException(400, "Please enter your date of birth")
    age = age_on(dob, date.today())
    if age < 18:
        raise HTTPException(400, "Sorry - by law you must be 18 or over to use a sunbed.")
    if age > 110:
        raise HTTPException(400, "Please check your date of birth")
    if data.shop not in SHOPS:
        raise HTTPException(400, "Please choose your salon")
    if data.skin_type not in SKIN_TYPES:
        raise HTTPException(400, "Please choose your skin type")
    if not data.declaration or not data.terms:
        raise HTTPException(400, "Please tick the declaration and the terms to continue")
    check_pin(data.pin)

    try:
        with db() as conn:
            c = conn.cursor()
            c.execute("""INSERT INTO customers (name, phone, email, postcode, shop, customer_number, dob, skin_type,
                                                 declaration_at, marketing_email, marketing_sms, consent_updated_at,
                                                 source, pin_hash, unsubscribe_token)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'self', ?, ?)""",
                      (name, phone, email, postcode, data.shop, next_customer_number(c), dob.isoformat(), data.skin_type,
                       now_str(), 1 if data.marketing_email else 0, 1 if data.marketing_sms else 0, now_str(),
                       hash_secret(data.pin), secrets.token_urlsafe(16)))
            number = c.execute("SELECT customer_number FROM customers WHERE id = ?", (c.lastrowid,)).fetchone()[0]
            conn.execute("INSERT INTO audit_log (username, action, detail, ip) VALUES (NULL, 'customer_self_signup', ?, ?)",
                         (f"#{c.lastrowid} {name}", ip))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That mobile number is already registered. Please ask at the desk if you need help.")
    return {"ok": True, "customer_number": number, "skin_type_warning": data.skin_type == "1"}


def load_customer_session(request: Request):
    token = request.cookies.get(CUSTOMER_COOKIE)
    if not token:
        return None
    with db() as conn:
        row = conn.execute("SELECT customer_id, expires_at FROM customer_sessions WHERE token_hash = ?", (token_hash(token),)).fetchone()
    if not row or row["expires_at"] < now_str():
        return None
    return row["customer_id"]


@app.post("/api/customer/login")
def customer_login(data: CustomerLogin, request: Request):
    phone = normalise_phone(data.phone)
    ip = client_ip(request)
    key = f"{ip}|{phone}"
    phone_key = f"phone|{phone}"
    wait = customer_login_limiter.is_locked(key) or customer_login_limiter.is_locked(phone_key) or customer_login_ip_limiter.is_locked(ip)
    if wait:
        raise HTTPException(429, f"Too many attempts. Try again in {max(1, wait // 60)} minute(s) or ask at the desk.")
    with db() as conn:
        row = conn.execute("SELECT id, pin_hash FROM customers WHERE phone = ?", (phone,)).fetchone()
        if row and not row["pin_hash"]:
            customer_login_ip_limiter.fail(ip)
            raise HTTPException(400, "You haven't set a PIN yet. Please ask at the desk and we'll set one up for you.")
        if not row or not verify_secret(data.pin, row["pin_hash"]):
            customer_login_limiter.fail(key)
            customer_login_limiter.fail(phone_key)
            customer_login_ip_limiter.fail(ip)
            raise HTTPException(401, "Wrong phone number or PIN")
        customer_login_limiter.reset(key)
        token = new_token()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=CUSTOMER_SESSION_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO customer_sessions (token_hash, customer_id, expires_at) VALUES (?, ?, ?)",
                     (token_hash(token), row["id"], expires))
    resp = JSONResponse({"ok": True})
    set_cookie(request, resp, CUSTOMER_COOKIE, token, CUSTOMER_SESSION_MINUTES * 60, same_site="lax")
    return resp


@app.post("/api/customer/logout")
def customer_logout(request: Request):
    token = request.cookies.get(CUSTOMER_COOKIE)
    if token:
        with db() as conn:
            conn.execute("DELETE FROM customer_sessions WHERE token_hash = ?", (token_hash(token),))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(CUSTOMER_COOKIE, path="/")
    return resp


@app.get("/api/customer/me")
def customer_me(request: Request):
    cid = load_customer_session(request)
    if not cid:
        raise HTTPException(401, "Please log in")
    with db() as conn:
        row = conn.execute("""SELECT name, customer_number, remaining_minutes, visits, loyalty, package_type, package_expires,
                                     marketing_email, marketing_sms, shop FROM customers WHERE id = ?""", (cid,)).fetchone()
    if not row:
        raise HTTPException(401, "Please log in")
    return dict(row)


@app.get("/api/customer/my-history")
def customer_my_history(request: Request):
    cid = load_customer_session(request)
    if not cid:
        raise HTTPException(401, "Please log in")
    with db() as conn:
        rows = conn.execute("SELECT items, total, created_at FROM orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20", (cid,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/unsubscribe")
def unsubscribe(t: str = ""):
    ok = False
    if t:
        with db() as conn:
            c = conn.execute("UPDATE customers SET marketing_email = 0, consent_updated_at = ? WHERE unsubscribe_token = ?", (now_str(), t))
            ok = c.rowcount > 0
    msg = "You've been unsubscribed from our marketing emails." if ok else "That unsubscribe link isn't valid. Please contact us and we'll remove you."
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Unsubscribe</title>
<link rel="stylesheet" href="/static/css/customer.css"></head>
<body><main class="wrap"><div class="brand"><span class="sun"></span>The Tanning Co.</div>
<div class="card"><h1>Email preferences</h1><p>{html.escape(msg)}</p></div></main></body></html>""")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/robots.txt")
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(os.path.join(STATIC_DIR, "favicon.svg"), media_type="image/svg+xml")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def serve_staff_app():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/login")
def serve_login(request: Request):
    if load_staff_user(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))


@app.get("/customer")
def serve_customer():
    return FileResponse(os.path.join(FRONTEND_DIR, "customer.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    # proxy_headers lets us see the real visitor IP and https behind Render's proxy
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
