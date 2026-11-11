from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import pandas as pd
import io
from twilio.rest import Client as TwilioClient

app = FastAPI(title="The Tanning Co. Coventry API")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
STATIC_DIR = os.path.join(PROJECT_ROOT, "static")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "glowboard.db"))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Customers
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
        pin TEXT DEFAULT '1234',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Rooms
    c.execute('''CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT,
        status TEXT DEFAULT 'available',
        customer_name TEXT,
        remaining_minutes INTEGER DEFAULT 0,
        started_at TIMESTAMP
    )''')
    
    # Products
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        category TEXT,
        image TEXT
    )''')
    
    # Orders
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        total REAL,
        discount INTEGER DEFAULT 0,
        payment_method TEXT,
        items TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')
    
    # Sessions log for daily reports
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        room_name TEXT,
        shop TEXT,
        minutes INTEGER,
        amount REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Queue
    c.execute('''CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        customer_name TEXT,
        items TEXT,
        total REAL,
        payment_method TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')
    
    # SMS settings
    c.execute('''CREATE TABLE IF NOT EXISTS sms_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        account_sid TEXT,
        auth_token TEXT,
        from_number TEXT
    )''')
    
    conn.commit()
    
    # Seed or update rooms to match real shop layout (only if wrong)
    c.execute("SELECT COUNT(*) FROM rooms")
    room_count = c.fetchone()[0]
    if room_count != 12:
        c.execute("DELETE FROM rooms")
        # Wyken: Stand-up 1-3, Lie-down 4-6
        for i in range(1, 4):
            c.execute("INSERT INTO rooms (id, name, type, status) VALUES (?, ?, ?, ?)",
                     (i, f"Wyken Stand-up {i}", "Stand-up", "available"))
        for i in range(4, 7):
            c.execute("INSERT INTO rooms (id, name, type, status) VALUES (?, ?, ?, ?)",
                     (i, f"Wyken Lie-down {i}", "Lie-down", "available"))
        # Earlsdon: Stand-up 1-3, Lie-down 4-6
        for i in range(7, 10):
            c.execute("INSERT INTO rooms (id, name, type, status) VALUES (?, ?, ?, ?)",
                     (i, f"Earlsdon Stand-up {i-6}", "Stand-up", "available"))
        for i in range(10, 13):
            c.execute("INSERT INTO rooms (id, name, type, status) VALUES (?, ?, ?, ?)",
                     (i, f"Earlsdon Lie-down {i-6}", "Lie-down", "available"))
    
    # Always refresh products to match current price list (removes lotions)
    c.execute("DELETE FROM products")
    products = [
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
    ]
    c.executemany("INSERT INTO products (name, price, category, image) VALUES (?, ?, ?, ?)", products)
    
    # Add postcode column if missing
    try:
        c.execute("ALTER TABLE customers ADD COLUMN postcode TEXT")
    except:
        pass
    
    # Add package columns if missing
    try:
        c.execute("ALTER TABLE customers ADD COLUMN package_type TEXT")
    except:
        pass
    try:
        c.execute("ALTER TABLE customers ADD COLUMN package_expires TEXT")
    except:
        pass
    
    # Seed sample customers if empty
    c.execute("SELECT COUNT(*) FROM customers")
    if c.fetchone()[0] == 0:
        customers = [
            ("Lucie Stolbova", "0791234567", "lucie@example.cz", 12, 33, 8.5, 75, 10),
            ("Jennifer lizonova", "0779876543", "jen@example.com", 5, 8, 6.2, 20, 5),
            ("Test Customer", "0711111111", "test@test.com", 1, 1, 0.9, 0, 0),
        ]
        c.executemany("""INSERT INTO customers 
                        (name, phone, email, orders, visits, avg_spend, loyalty, remaining_minutes) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", customers)
    
    conn.commit()
    conn.close()

init_db()

# Models
class CustomerCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = ""
    postcode: Optional[str] = ""
    shop: Optional[str] = None

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
    total: float
    shop: Optional[str] = None

class AssignRoom(BaseModel):
    queue_id: int
    room_id: int

class CustomerLogin(BaseModel):
    phone: str
    pin: str = "1234"

class UpdateMinutes(BaseModel):
    minutes: int

class EmailSettings(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    from_email: str
    from_name: str = "The Tanning Co. Coventry"

class CampaignSMS(BaseModel):
    message: str
    customer_ids: Optional[List[int]] = None  # None = all with phone

class SMSSettings(BaseModel):
    account_sid: str
    auth_token: str
    from_number: str

# Routes
@app.get("/api/rooms")
def get_rooms():
    conn = get_db()
    rooms = conn.execute("SELECT * FROM rooms ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rooms]

@app.get("/api/customers")
def search_customers(q: str = ""):
    conn = get_db()
    if q:
        customers = conn.execute(
            "SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? OR email LIKE ? LIMIT 50",
            (f"%{q}%", f"%{q}%", f"%{q}%")
        ).fetchall()
    else:
        customers = conn.execute("SELECT * FROM customers ORDER BY name LIMIT 200").fetchall()
    conn.close()
    return [dict(c) for c in customers]

@app.get("/api/customers/{customer_id}")
def get_customer(customer_id: int):
    conn = get_db()
    customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    conn.close()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return dict(customer)

@app.post("/api/customers")
def create_customer(customer: CustomerCreate):
    conn = get_db()
    try:
        c = conn.cursor()
        # Ensure columns exist
        try:
            c.execute("ALTER TABLE customers ADD COLUMN postcode TEXT")
        except:
            pass
        try:
            c.execute("ALTER TABLE customers ADD COLUMN shop TEXT")
        except:
            pass
        try:
            c.execute("ALTER TABLE customers ADD COLUMN customer_number INTEGER")
        except:
            pass
        
        # Get next sequential number
        row = c.execute("SELECT COALESCE(MAX(customer_number), 0) + 1 as next_num FROM customers").fetchone()
        next_num = row["next_num"] if row else 1
        
        c.execute("INSERT INTO customers (name, phone, email, postcode, shop, customer_number) VALUES (?, ?, ?, ?, ?, ?)",
                  (customer.name, customer.phone or "", customer.email or "", customer.postcode or "", customer.shop or "", next_num))
        conn.commit()
        new_id = c.lastrowid
        conn.close()
        return {"id": new_id, "message": "Customer created"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/customers/{customer_id}")
def update_customer(customer_id: int, customer: CustomerCreate):
    conn = get_db()
    c = conn.cursor()
    c.execute("""UPDATE customers 
                 SET name = ?, phone = ?, email = ?, postcode = ?, shop = ?
                 WHERE id = ?""",
              (customer.name, customer.phone, customer.email, customer.postcode, customer.shop, customer_id))
    if c.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Customer not found")
    conn.commit()
    conn.close()
    return {"message": "Customer updated"}

@app.delete("/api/customers/{customer_id}")
def delete_customer(customer_id: int):
    conn = get_db()
    conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    conn.commit()
    conn.close()
    return {"message": "Customer deleted"}

@app.get("/api/products")
def get_products():
    conn = get_db()
    products = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    return [dict(p) for p in products]

@app.post("/api/orders")
def create_order(order: OrderCreate):
    conn = get_db()
    c = conn.cursor()
    
    # Ensure shop column exists on orders
    try:
        c.execute("ALTER TABLE orders ADD COLUMN shop TEXT")
    except:
        pass
    
    items_str = ", ".join([f"{i.qty}x {i.name}" for i in order.items])
    c.execute("""INSERT INTO orders (customer_id, total, discount, payment_method, items, shop)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (order.customer_id, order.total, order.discount, order.payment_method, items_str, order.shop or "Unknown"))
    order_id = c.lastrowid
    
    # Get customer name
    customer = c.execute("SELECT name FROM customers WHERE id = ?", (order.customer_id,)).fetchone()
    customer_name = customer["name"] if customer else "Unknown"
    
    # Only add to queue if it's a session (not a pure package purchase)
    is_package_only = all(
        "pack" in i.name.lower() or "unlimited" in i.name.lower() or "min" in i.name.lower()
        for i in order.items
    )
    if not is_package_only:
        c.execute("""INSERT INTO queue (customer_id, customer_name, items, total, payment_method)
                     VALUES (?, ?, ?, ?, ?)""",
                  (order.customer_id, customer_name, items_str, order.total, order.payment_method))
    
    # Update customer stats
    c.execute("""UPDATE customers 
                 SET orders = orders + 1, 
                     visits = visits + 1,
                     loyalty = loyalty + ?,
                     avg_spend = (avg_spend * (orders - 1) + ?) / orders
                 WHERE id = ?""",
              (int(order.total), order.total, order.customer_id))
    
    # Handle minutes packs, single sessions, and unlimited packages
    for item in order.items:
        name = item.name.lower()
        if "min pack" in name:
            mins = 0
            for m in [120, 100, 60, 30]:
                if str(m) in name:
                    mins = m
                    break
            if mins > 0:
                c.execute("UPDATE customers SET remaining_minutes = remaining_minutes + ? WHERE id = ?",
                          (mins * item.qty, order.customer_id))
        elif " min" in name and "pack" not in name and "unlimited" not in name:
            # Single session: 3 MIN, 6 MIN, etc.
            mins = 0
            for m in [15, 12, 9, 6, 3]:
                if str(m) in name:
                    mins = m
                    break
            if mins > 0:
                c.execute("UPDATE customers SET remaining_minutes = remaining_minutes + ? WHERE id = ?",
                          (mins * item.qty, order.customer_id))
        elif "unlimited weekly" in name:
            from datetime import datetime, timedelta
            expires = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            # 15 min max per day × 7 days
            c.execute("""UPDATE customers 
                         SET package_type = 'weekly', package_expires = ?, 
                             remaining_minutes = remaining_minutes + 105 
                         WHERE id = ?""",
                      (expires, order.customer_id))
        elif "unlimited monthly" in name:
            from datetime import datetime, timedelta
            expires = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            # 15 min max per day × 30 days
            c.execute("""UPDATE customers 
                         SET package_type = 'monthly', package_expires = ?, 
                             remaining_minutes = remaining_minutes + 450 
                         WHERE id = ?""",
                      (expires, order.customer_id))
    
    conn.commit()
    conn.close()
    return {"order_id": order_id, "message": "Order created and added to queue"}

@app.get("/api/queue")
def get_queue():
    conn = get_db()
    queue = conn.execute("SELECT * FROM queue ORDER BY created_at").fetchall()
    conn.close()
    return [dict(q) for q in queue]

@app.post("/api/queue/{queue_id}/assign")
def assign_room(queue_id: int, data: AssignRoom):
    conn = get_db()
    c = conn.cursor()
    
    queue_item = c.execute("SELECT * FROM queue WHERE id = ?", (queue_id,)).fetchone()
    if not queue_item:
        conn.close()
        raise HTTPException(status_code=404, detail="Queue item not found")
    
    room = c.execute("SELECT * FROM rooms WHERE id = ?", (data.room_id,)).fetchone()
    if not room or room["status"] != "available":
        conn.close()
        raise HTTPException(status_code=400, detail="Room not available")
    
    items = queue_item["items"].lower()
    # Parse minutes from product names like "3 MIN", "6 MIN", "15 MIN", etc.
    minutes = 10
    for m in [15, 12, 9, 6, 3, 30, 60, 100, 120]:
        if str(m) in items or f"{m} min" in items:
            minutes = m
            break
    
    c.execute("""UPDATE rooms 
                 SET status = 'occupied', 
                     customer_name = ?, 
                     remaining_minutes = ?,
                     started_at = CURRENT_TIMESTAMP
                 WHERE id = ?""",
              (queue_item["customer_name"], minutes, data.room_id))
    
    c.execute("DELETE FROM queue WHERE id = ?", (queue_id,))
    
    conn.commit()
    conn.close()
    return {"message": "Room assigned", "minutes": minutes}

@app.delete("/api/queue/{queue_id}")
def cancel_queue(queue_id: int):
    conn = get_db()
    conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))
    conn.commit()
    conn.close()
    return {"message": "Removed from queue"}

@app.post("/api/customer/login")
def customer_login(data: CustomerLogin):
    conn = get_db()
    customer = conn.execute(
        "SELECT * FROM customers WHERE phone = ? AND (pin = ? OR pin IS NULL)",
        (data.phone.strip(), data.pin)
    ).fetchone()
    conn.close()
    if not customer:
        raise HTTPException(status_code=401, detail="Invalid phone number or PIN")
    return {
        "id": customer["id"],
        "name": customer["name"],
        "phone": customer["phone"],
        "email": customer["email"],
        "remaining_minutes": customer["remaining_minutes"],
        "loyalty": customer["loyalty"],
        "visits": customer["visits"],
        "orders": customer["orders"]
    }

@app.get("/api/customer/{customer_id}/history")
def customer_history(customer_id: int):
    conn = get_db()
    orders = conn.execute(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20",
        (customer_id,)
    ).fetchall()
    conn.close()
    return [dict(o) for o in orders]

@app.post("/api/rooms/{room_id}/clean")
def mark_clean(room_id: int):
    conn = get_db()
    conn.execute("UPDATE rooms SET status = 'available' WHERE id = ?", (room_id,))
    conn.commit()
    conn.close()
    return {"message": "Room available"}

@app.post("/api/rooms/{room_id}/finish")
def finish_session(room_id: int):
    """Deduct minutes and free the room"""
    conn = get_db()
    c = conn.cursor()
    room = c.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if not room or room["status"] != "occupied":
        conn.close()
        raise HTTPException(status_code=400, detail="Room is not occupied")
    
    minutes_used = room["remaining_minutes"] or 0
    customer_name = room["customer_name"]
    shop = "Wyken" if "Wyken" in (room["name"] or "") else "Earlsdon" if "Earlsdon" in (room["name"] or "") else "Unknown"
    
    # Deduct from customer if we can match by name
    if customer_name and minutes_used > 0:
        c.execute("""UPDATE customers 
                     SET remaining_minutes = MAX(0, remaining_minutes - ?)
                     WHERE name = ?""", (minutes_used, customer_name))
    
    # Log the session for the daily report
    c.execute("""INSERT INTO sessions (customer_name, room_name, shop, minutes)
                 VALUES (?, ?, ?, ?)""",
              (customer_name, room["name"], shop, minutes_used))
    
    c.execute("""UPDATE rooms 
                 SET status = 'available', customer_name = NULL, remaining_minutes = 0 
                 WHERE id = ?""", (room_id,))
    conn.commit()
    conn.close()
    return {"message": "Session finished", "minutes_deducted": minutes_used}

# ===== Excel Import =====
@app.post("/api/import/excel")
async def import_excel(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Please upload an Excel file (.xlsx or .xls)")
    
    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {str(e)}")
    
    # Normalize column names
    df.columns = [str(c).strip().lower() for c in df.columns]
    
    # Map common column names
    name_col = next((c for c in df.columns if c in ['name', 'customer', 'full name', 'customer name']), None)
    phone_col = next((c for c in df.columns if c in ['phone', 'mobile', 'telephone', 'phone number', 'mobile number']), None)
    email_col = next((c for c in df.columns if c in ['email', 'e-mail', 'email address']), None)
    
    if not name_col or not phone_col:
        raise HTTPException(status_code=400, detail="Excel must have at least 'Name' and 'Phone' columns")
    
    conn = get_db()
    c = conn.cursor()
    imported = 0
    skipped = 0
    
    for _, row in df.iterrows():
        name = str(row[name_col]).strip() if pd.notna(row[name_col]) else ""
        phone = str(row[phone_col]).strip() if pd.notna(row[phone_col]) else ""
        email = str(row[email_col]).strip() if email_col and pd.notna(row[email_col]) else ""
        
        if not name or not phone:
            skipped += 1
            continue
        
        try:
            c.execute("INSERT INTO customers (name, phone, email) VALUES (?, ?, ?)",
                      (name, phone, email))
            imported += 1
        except sqlite3.IntegrityError:
            skipped += 1  # duplicate phone
    
    conn.commit()
    conn.close()
    
    return {"imported": imported, "skipped": skipped, "total_rows": len(df)}

# ===== Email Campaigns =====
@app.get("/api/email/settings")
def get_email_settings():
    conn = get_db()
    row = conn.execute("SELECT smtp_host, smtp_port, smtp_user, from_email, from_name FROM email_settings WHERE id = 1").fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

@app.post("/api/email/settings")
def save_email_settings(settings: EmailSettings):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO email_settings 
                 (id, smtp_host, smtp_port, smtp_user, smtp_password, from_email, from_name)
                 VALUES (1, ?, ?, ?, ?, ?, ?)""",
              (settings.smtp_host, settings.smtp_port, settings.smtp_user, 
               settings.smtp_password, settings.from_email, settings.from_name))
    conn.commit()
    conn.close()
    return {"message": "Email settings saved"}

@app.post("/api/email/campaign")
def send_campaign(campaign: CampaignEmail):
    conn = get_db()
    settings = conn.execute("SELECT * FROM email_settings WHERE id = 1").fetchone()
    if not settings or not settings["smtp_host"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Email settings not configured. Go to Email Settings first.")
    
    # Get recipients
    if campaign.customer_ids:
        placeholders = ",".join("?" * len(campaign.customer_ids))
        customers = conn.execute(
            f"SELECT name, email FROM customers WHERE id IN ({placeholders}) AND email != '' AND email IS NOT NULL",
            campaign.customer_ids
        ).fetchall()
    else:
        customers = conn.execute(
            "SELECT name, email FROM customers WHERE email != '' AND email IS NOT NULL"
        ).fetchall()
    
    conn.close()
    
    if not customers:
        raise HTTPException(status_code=400, detail="No customers with email addresses found")
    
    sent = 0
    failed = 0
    errors = []
    
    try:
        server = smtplib.SMTP(settings["smtp_host"], settings["smtp_port"])
        server.starttls()
        server.login(settings["smtp_user"], settings["smtp_password"])
        
        for cust in customers:
            try:
                msg = MIMEMultipart()
                msg["From"] = f"{settings['from_name']} <{settings['from_email']}>"
                msg["To"] = cust["email"]
                msg["Subject"] = campaign.subject
                
                # Personalize with name + footer
                body = campaign.body.replace("{name}", cust["name"])
                footer = """
                <br><br>
                <hr style="border:none;border-top:1px solid #ddd;margin:20px 0">
                <small style="color:#666">
                <strong>The Tanning Co. Coventry</strong><br>
                📍 Wyken: 50 Torcross Ave, CV2 3NQ • 024 7665 9966<br>
                📍 Earlsdon: 116 Albany Rd, CV5 6NG • 024 7667 4922<br>
                www.thetanningco.co.uk
                </small>
                """
                msg.attach(MIMEText(body + footer, "html"))
                
                server.send_message(msg)
                sent += 1
            except Exception as e:
                failed += 1
                errors.append(f"{cust['email']}: {str(e)}")
        
        server.quit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SMTP connection failed: {str(e)}")
    
    return {"sent": sent, "failed": failed, "errors": errors[:5]}

# ===== SMS Campaigns (Twilio) =====
@app.get("/api/sms/settings")
def get_sms_settings():
    conn = get_db()
    row = conn.execute("SELECT account_sid, from_number FROM sms_settings WHERE id = 1").fetchone()
    conn.close()
    if row:
        return {"account_sid": row["account_sid"], "from_number": row["from_number"], "configured": True}
    return {"configured": False}

@app.post("/api/sms/settings")
def save_sms_settings(settings: SMSSettings):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO sms_settings 
                 (id, account_sid, auth_token, from_number)
                 VALUES (1, ?, ?, ?)""",
              (settings.account_sid, settings.auth_token, settings.from_number))
    conn.commit()
    conn.close()
    return {"message": "SMS settings saved"}

@app.post("/api/sms/campaign")
def send_sms_campaign(campaign: CampaignSMS):
    conn = get_db()
    settings = conn.execute("SELECT * FROM sms_settings WHERE id = 1").fetchone()
    if not settings or not settings["account_sid"]:
        conn.close()
        raise HTTPException(status_code=400, detail="SMS settings not configured. Add your Twilio details first.")
    
    if campaign.customer_ids:
        placeholders = ",".join("?" * len(campaign.customer_ids))
        customers = conn.execute(
            f"SELECT name, phone FROM customers WHERE id IN ({placeholders}) AND phone != '' AND phone IS NOT NULL",
            campaign.customer_ids
        ).fetchall()
    else:
        customers = conn.execute(
            "SELECT name, phone FROM customers WHERE phone != '' AND phone IS NOT NULL"
        ).fetchall()
    conn.close()
    
    if not customers:
        raise HTTPException(status_code=400, detail="No customers with phone numbers found")
    
    client = TwilioClient(settings["account_sid"], settings["auth_token"])
    sent = 0
    failed = 0
    errors = []
    
    for cust in customers:
        try:
            # Ensure phone is in E.164 format for UK
            phone = cust["phone"].strip()
            if phone.startswith("0"):
                phone = "+44" + phone[1:]
            elif not phone.startswith("+"):
                phone = "+44" + phone
            
            msg = campaign.message.replace("{name}", cust["name"])
            client.messages.create(
                body=msg,
                from_=settings["from_number"],
                to=phone
            )
            sent += 1
        except Exception as e:
            failed += 1
            errors.append(f"{cust['phone']}: {str(e)}")
    
    return {"sent": sent, "failed": failed, "errors": errors[:5]}

@app.get("/api/export/customers")
def export_customers():
    """Download all customers as Excel for backup"""
    conn = get_db()
    df = pd.read_sql_query("SELECT name, phone, email, remaining_minutes, package_type, package_expires, visits, loyalty FROM customers", conn)
    conn.close()
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=customers_backup.xlsx"})

@app.get("/api/customer/{customer_id}/bed-usage")
def customer_bed_usage(customer_id: int):
    conn = get_db()
    # Use orders that contain session items as a simple usage log for now
    orders = conn.execute(
        "SELECT items, created_at FROM orders WHERE customer_id = ? AND (items LIKE '%MIN%' OR items LIKE '%min%') ORDER BY created_at DESC LIMIT 10",
        (customer_id,)
    ).fetchall()
    conn.close()
    result = []
    for o in orders:
        result.append({
            "room_name": "Session",
            "minutes": 0,
            "created_at": o["created_at"],
            "items": o["items"]
        })
    return result

# Waiting list table
def ensure_waitlist_table():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS waitlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id INTEGER,
        customer_id INTEGER,
        customer_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

ensure_waitlist_table()

@app.get("/api/waitlist/{room_id}")
def get_waitlist(room_id: int):
    conn = get_db()
    rows = conn.execute("SELECT * FROM waitlist WHERE room_id = ? ORDER BY created_at", (room_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/waitlist")
def add_to_waitlist(data: dict):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO waitlist (room_id, customer_id, customer_name) VALUES (?, ?, ?)",
              (data.get("room_id"), data.get("customer_id"), data.get("customer_name")))
    conn.commit()
    conn.close()
    return {"message": "Added to waitlist"}

@app.delete("/api/waitlist/{wait_id}")
def remove_from_waitlist(wait_id: int):
    conn = get_db()
    conn.execute("DELETE FROM waitlist WHERE id = ?", (wait_id,))
    conn.commit()
    conn.close()
    return {"message": "Removed from waitlist"}

@app.post("/api/use-minutes")
def use_minutes(data: dict):
    """Deduct minutes from customer and assign to a bed"""
    conn = get_db()
    c = conn.cursor()
    customer_id = data.get("customer_id")
    minutes = int(data.get("minutes", 0))
    room_id = data.get("room_id")
    
    customer = c.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not customer:
        conn.close()
        raise HTTPException(status_code=404, detail="Customer not found")
    if (customer["remaining_minutes"] or 0) < minutes:
        conn.close()
        raise HTTPException(status_code=400, detail="Not enough minutes")
    
    room = c.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if not room or room["status"] != "available":
        conn.close()
        raise HTTPException(status_code=400, detail="Bed not available")
    
    c.execute("UPDATE customers SET remaining_minutes = remaining_minutes - ? WHERE id = ?",
              (minutes, customer_id))
    c.execute("""UPDATE rooms 
                 SET status = 'occupied', customer_name = ?, remaining_minutes = ?, started_at = CURRENT_TIMESTAMP
                 WHERE id = ?""",
              (customer["name"], minutes, room_id))
    conn.commit()
    conn.close()
    return {"message": "Bed assigned", "minutes_used": minutes}

@app.get("/api/report/daily")
def daily_report(date: str = None):
    """Daily report: who used, minutes, money — per shop and combined"""
    from datetime import datetime
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    
    conn = get_db()
    c = conn.cursor()
    
    # Ensure sessions table exists
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        room_name TEXT,
        shop TEXT,
        minutes INTEGER,
        amount REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    
    # Sessions for the day
    sessions = c.execute(
        "SELECT * FROM sessions WHERE date(created_at) = ? ORDER BY created_at",
        (date,)
    ).fetchall()
    
    # Money from orders for the day
    try:
        orders = c.execute(
            "SELECT total, shop, items FROM orders WHERE date(created_at) = ?",
            (date,)
        ).fetchall()
    except:
        orders = c.execute(
            "SELECT total, items FROM orders WHERE date(created_at) = ?",
            (date,)
        ).fetchall()
        orders = [dict(o) | {"shop": None} for o in orders]
    
    # Total remaining minutes per shop
    minutes_left = {}
    for shop_name in ["Wyken", "Earlsdon"]:
        try:
            row = c.execute(
                "SELECT COALESCE(SUM(remaining_minutes), 0) as total FROM customers WHERE shop = ?",
                (shop_name,)
            ).fetchone()
            minutes_left[shop_name] = row["total"] if row else 0
        except:
            minutes_left[shop_name] = 0
    
    conn.close()
    
    products_list = [
        "3 MIN", "6 MIN", "9 MIN", "12 MIN", "15 MIN",
        "30 MIN Pack", "60 MIN Pack", "100 MIN Pack", "120 MIN Pack",
        "Unlimited Weekly", "Unlimited Monthly"
    ]
    
    shops = {
        "Wyken": {"sessions": [], "minutes": 0, "money": 0, "minutes_left": minutes_left.get("Wyken", 0), "breakdown": {p: 0 for p in products_list}},
        "Earlsdon": {"sessions": [], "minutes": 0, "money": 0, "minutes_left": minutes_left.get("Earlsdon", 0), "breakdown": {p: 0 for p in products_list}}
    }
    total_minutes = 0
    total_money = 0
    
    for o in orders:
        o = dict(o)
        amt = o.get("total") or 0
        total_money += amt
        shop = o.get("shop") if o.get("shop") in shops else None
        if shop:
            shops[shop]["money"] += amt
            items = (o.get("items") or "").lower()
            for p in products_list:
                if p.lower() in items:
                    shops[shop]["breakdown"][p] += amt
                    break
    
    for s in sessions:
        s = dict(s)
        shop = s.get("shop") if s.get("shop") in shops else "Wyken"
        shops[shop]["sessions"].append(s)
        shops[shop]["minutes"] += s.get("minutes") or 0
        total_minutes += s.get("minutes") or 0
    
    return {
        "date": date,
        "shops": shops,
        "total_minutes": total_minutes,
        "total_money": total_money,
        "order_count": len(orders)
    }

# Serve frontend
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

@app.get("/customer")
def serve_customer():
    return FileResponse(os.path.join(FRONTEND_DIR, "customer.html"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
