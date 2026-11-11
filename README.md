# The Tanning Co. Coventry — Salon Management System

Persistent backend with Excel import and email campaigns.

Branded for **The Tanning Co. Coventry** (www.thetanningco.co.uk)

## New Features

### Excel Import
- Upload an `.xlsx` or `.xls` file
- Required columns: **Name**, **Phone**
- Optional: **Email**
- Duplicates (same phone) are skipped

### Email Campaigns
1. Go to **Email Settings** and enter your SMTP details
2. Go to **Email Campaign**
3. Write subject + body (use `{name}` to personalize)
4. Sends to all customers who have an email address

## SMTP Setup Examples

**Gmail:**
- Host: `smtp.gmail.com`
- Port: `587`
- User: your Gmail address
- Password: [App Password](https://support.google.com/accounts/answer/185833) (not your normal password)

**Outlook / Microsoft 365:**
- Host: `smtp.office365.com`
- Port: `587`

## How to Run

```bash
chmod +x start.sh
./start.sh
```

Then open: **http://localhost:8000**

## Database
All data is stored in `backend/glowboard.db` (SQLite).
You can back this file up at any time.
