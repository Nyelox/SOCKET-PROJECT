import os
import base64
import uuid
import json
import struct
import socket
import threading
from datetime import datetime, timedelta

import pymysql
from werkzeug.utils import secure_filename

from Server.Database_connection import handle_login, handle_signup
from supabase import create_client, Client
from Server.server_crypto import generate_rsa_keys, export_public_key, rsa_decrypt, aes_decrypt

SUPABASE_URL = "https://trgaimvzokzrtapgkxsd.supabase.co"
# מפתח הגישה ל-Supabase Storage
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRyZ2FpbXZ6b2t6cnRhcGdreHNkIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NTQ3MDQ2NSwiZXhwIjoyMDgxMDQ2NDY1fQ.8VFdJPQEmCsMqnnAUBGYFuG0tUtPzOroSx6hnKEF2og"
SUPABASE_BUCKET = "Files" # שם ה-Bucket בשרת

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

APP_HOST = "0.0.0.0"
APP_PORT = 5000

UPLOAD_DIR = "uploaded_files"
MAX_FILE_MB = 50

online_users = {}
ONLINE_TIMEOUT_SECONDS = 20

DB_CONFIG = dict(
    host='localhost',
    user='root',
    password='1234',
    database='userdata',
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

# ייצור מפתחות RSA לשרת
_rsa_private, _rsa_public = generate_rsa_keys()
print("RSA Key generated.")

# מילון שמירת מפתחות AES של כל לקוח מחובר (token -> aes_key)
_sessions = {}


# ============================================================
#  פרוטוקול התקשורת (מבוסס סוקטים, תכנון התלמיד)
# ------------------------------------------------------------
#  כל הודעה נשלחת כך:  [4 בתים = אורך ההודעה] + [גוף ההודעה ב-JSON]
#  הלקוח שולח:  {"command": "<שם הפקודה>", "data": {...}}
#  השרת מחזיר:  {"status": "...", ...}  (מילון שהומר ל-JSON)
# ============================================================

# שליחת מילון פייתון דרך הסוקט (אורך 4 בתים + גוף JSON)
def send_message(sock, obj):
    body = json.dumps(obj).encode("utf-8")
    header = struct.pack(">I", len(body))   # ">I" = מספר שלם של 4 בתים, big-endian
    sock.sendall(header + body)


# קריאת בדיוק n בתים מהסוקט (כי recv יכול להחזיר פחות ממה שביקשנו)
def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf += chunk
    return buf


# קבלת מילון פייתון מהסוקט לפי הפרוטוקול (קוראים אורך, ואז את הגוף)
def recv_message(sock):
    header = _recv_exact(sock, 4)
    length = struct.unpack(">I", header)[0]
    body = _recv_exact(sock, length)
    return json.loads(body.decode("utf-8"))


# התחברות לבסיס הנתונים MySQL
def get_db():
    return pymysql.connect(**DB_CONFIG)


# יצירת טבלאות בסיס הנתונים במידה והן לא קיימות
def init_db():
    con = get_db()
    cur = con.cursor()

    # טבלת קבצים משותפים
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shared_files (
            id INT AUTO_INCREMENT PRIMARY KEY,
            file_uid VARCHAR(64) NOT NULL,
            sender VARCHAR(255) NOT NULL,
            receiver VARCHAR(255) NOT NULL,
            filename VARCHAR(255) NOT NULL,
            path VARCHAR(500) NOT NULL,
            expires_at DATETIME NOT NULL,
            max_downloads INT DEFAULT 1,
            download_count INT DEFAULT 0,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # בדיקה והוספת עמודות במידה וצריך (עבור עדכוני גרסה)
    cur.execute("SHOW COLUMNS FROM shared_files LIKE 'max_downloads'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE shared_files ADD COLUMN max_downloads INT DEFAULT 1")

    cur.execute("SHOW COLUMNS FROM shared_files LIKE 'download_count'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE shared_files ADD COLUMN download_count INT DEFAULT 0")

    # טבלת היסטוריית פעולות
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(255) NOT NULL,
            action VARCHAR(255) NOT NULL,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # טבלת הגדרות מערכת
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            setting_key VARCHAR(50) PRIMARY KEY,
            setting_value VARCHAR(255)
        );
    """)

    # הכנסת הגדרת ברירת מחדל להורדות אם לא קיימת
    cur.execute("SELECT setting_value FROM system_settings WHERE setting_key='global_max_downloads'")
    if not cur.fetchone():
        cur.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES ('global_max_downloads', '5')")

    # טבלת עבודות הדפסה
    cur.execute("""
        CREATE TABLE IF NOT EXISTS print_jobs (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            file_id       INT NOT NULL,
            sender        VARCHAR(255) NOT NULL,
            filename      VARCHAR(255) NOT NULL,
            file_type     VARCHAR(20) NOT NULL,
            print_allowed BOOLEAN DEFAULT 0,
            print_status  ENUM('pending','printed') DEFAULT 'pending',
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # פונקציית עזר להוספת עמודות לטבלאות קיימות
    def add_column(table, col, defi):
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defi}")
        except Exception:
            pass # העמודה כנראה כבר קיימת

    add_column("data", "is_blocked", "BOOLEAN DEFAULT 0")
    add_column("data", "is_admin", "BOOLEAN DEFAULT 0")
    add_column("shared_files", "message", "TEXT")
    add_column("shared_files", "encrypted_aes_key", "TEXT")  # מפתח AES המוצפן של כל קובץ

    con.commit()
    con.close()


# רישום פעולה להיסטוריית המערכת
def log_history(username, action, details=""):
    try:
        con = get_db()
        cur = con.cursor()
        cur.execute("INSERT INTO history (username, action, details) VALUES (%s, %s, %s)",
                    (username, action, details))
        con.commit()
        con.close()
    except Exception as e:
        print(f"Failed to log history: {e}")


# ניקוי קבצים שפג תוקפם מהשרת ומבסיס הנתונים
def cleanup_expired_files():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT id, path FROM shared_files WHERE expires_at < NOW()")
    expired = cur.fetchall()

    for row in expired:
        # הסרה מ-Supabase Storage
        try:
            supabase.storage.from_(SUPABASE_BUCKET).remove([row["path"]])
        except Exception as e:
            print(f"Error removing file from Supabase: {e}")

        # ניסיון ניקוי מקומי (למקרה של קבצים ישנים)
        try:
            if os.path.exists(row["path"]):
                os.remove(row["path"])
        except Exception:
            pass

        cur.execute("DELETE FROM shared_files WHERE id=%s", (row["id"],))

    con.commit()
    con.close()


# מחיקת קובץ מהשרת ומבסיס הנתונים באופן פנימי
def _delete_file_internal(file_id):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT path FROM shared_files WHERE id=%s", (file_id,))
    row = cur.fetchone()
    if row:
        path = row["path"]
        # מחיקה מבסיס הנתונים
        cur.execute("DELETE FROM shared_files WHERE id=%s", (file_id,))
        # בדיקה האם יש קבצים אחרים שמשתמשים באותו נתיב לפני מחיקה מהאחסון
        cur.execute("SELECT COUNT(*) as count FROM shared_files WHERE path=%s", (path,))
        if cur.fetchone()["count"] == 0:
            try:
                supabase.storage.from_(SUPABASE_BUCKET).remove([path])
            except:
                pass
    con.commit()
    con.close()


# בדיקה האם המשתמש הוא מנהל מערכת (Admin)
def is_admin(username):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT is_admin FROM data WHERE username=%s", (username,))
    res = cur.fetchone()
    con.close()
    return res and res["is_admin"]


# ============================================================
#  הפקודות (מה שהיו פעם נקודות הקצה של Flask)
#  כל פקודה מקבלת מילון data ומחזירה מילון תשובה
# ============================================================

# שליחת המפתח הציבורי RSA ללקוח
def cmd_public_key(data):
    return {"public_key": export_public_key(_rsa_public)}


# קבלת מפתח AES מוצפן מהלקוח ושמירתו בזיכרון
def cmd_session_key(data):
    encrypted_aes = data.get("encrypted_key", "")
    try:
        aes_key = rsa_decrypt(_rsa_private, encrypted_aes)
        token = uuid.uuid4().hex
        _sessions[token] = aes_key
        print(f"AES key established for session {token[:8]}...")
        return {"session_token": token}
    except Exception:
        return {"status": "Key exchange failed"}


# רישום משתמש חדש (הנתונים מוצפנים ב-AES)
def cmd_signup(data):
    token = data.get("session_token", "")
    if token not in _sessions:
        return {"status": "Invalid session"}
    try:
        dec = aes_decrypt(_sessions[token], data.get("encrypted_data", ""))
    except Exception:
        return {"status": "Decryption failed"}

    username = dec.get("username", "").strip()
    password = dec.get("password", "").strip()
    if not username or not password:
        return {"status": "Missing fields"}

    res = handle_signup(username, password)
    if res.get("status") == "success":
        log_history(username, "SIGNUP", "User created account")
        return {"status": res["message"]}

    return {"status": res.get("message", "Error")}


# התחברות משתמש (הנתונים מוצפנים ב-AES)
def cmd_login(data):
    token = data.get("session_token", "")
    if token not in _sessions:
        return {"status": "Invalid session"}
    try:
        dec = aes_decrypt(_sessions[token], data.get("encrypted_data", ""))
    except Exception:
        return {"status": "Decryption failed"}

    username = dec.get("username", "").strip()
    password = dec.get("password", "").strip()
    if not username or not password:
        return {"status": "Missing fields"}

    res = handle_login(username, password)

    if res.get("status") == "success":
        log_history(username, "LOGIN", "User logged in")
        return {
            "status": res["message"],
            "is_admin": res.get("is_admin", False),
            "is_blocked": res.get("is_blocked", False)
        }

    return {"status": res.get("message", "Login failed")}


# העלאת קובץ חדש ושיתופו עם משתמשים אחרים
def cmd_upload_file(data):
    sender = data.get("sender", "").strip()

    # תמיכה גם בשם בודד וגם ברשימת נמענים
    receivers = data.get("receivers", [])
    if not isinstance(receivers, list):
        receivers = [str(receivers).strip()]

    single_receiver = data.get("receiver", "").strip()
    if single_receiver and single_receiver not in receivers:
        receivers.append(single_receiver)

    filename = data.get("filename", "").strip()
    filedata_b64 = data.get("filedata", "")
    encrypted_aes_key = data.get("encrypted_aes_key", "")
    minutes = int(data.get("minutes", 10))
    max_downloads = int(data.get("max_downloads", 1))

    message = data.get("message", "").strip()

    if not all([sender, receivers, filename, encrypted_aes_key]) or "filedata" not in data:
        return {"status": "Missing fields"}

    safe_name = secure_filename(filename)
    if not safe_name:
        return {"status": "Bad filename"}

    try:
        raw_bytes = base64.b64decode(filedata_b64)
    except Exception:
        return {"status": "Invalid base64"}

    # בדיקת גודל הקובץ בצד השרת (גיבוי אבטחתי - לא סומכים רק על הלקוח)
    if len(raw_bytes) > MAX_FILE_MB * 1024 * 1024:
        return {"status": f"File too large (max {MAX_FILE_MB} MB)"}

    file_uid = uuid.uuid4().hex
    server_filename = f"{file_uid}_{safe_name}"
    path = server_filename

    con = get_db()
    cur = con.cursor()

    # בדיקה שכל הנמענים קיימים במערכת
    invalid_receivers = []
    for rcv in receivers:
        rcv = rcv.strip()
        if not rcv: continue
        cur.execute("SELECT username FROM data WHERE username=%s", (rcv,))
        if not cur.fetchone():
            invalid_receivers.append(rcv)

    if invalid_receivers:
        con.close()
        return {"status": f"User(s) not found: {', '.join(invalid_receivers)}"}

    # העלאה ל-Supabase
    try:
        supabase.storage.from_(SUPABASE_BUCKET).upload(path, raw_bytes, {"content-type": "application/octet-stream"})
    except Exception as e:
        con.close()
        print(f"Supabase upload error: {e}")
        return {"status": "Storage Error"}

    expires_at = datetime.now() + timedelta(minutes=minutes)

    for rcv in receivers:
        rcv = rcv.strip()
        if not rcv:
            continue
        cur.execute("""
            INSERT INTO shared_files(file_uid, sender, receiver, filename, path, expires_at, max_downloads, download_count, message, encrypted_aes_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s)
        """, (file_uid, sender, rcv, safe_name, path, expires_at, max_downloads, message, encrypted_aes_key))
        log_history(sender, "UPLOAD", f"Sent file '{safe_name}' to {rcv} (Max downloads: {max_downloads})")

    con.commit()
    con.close()

    return {"status": "OK"}


# שליפת רשימת הקבצים שמחכים למשתמש מסוים
def cmd_incoming_files(data):
    receiver = data.get("receiver", "").strip()
    if not receiver:
        return {"status": "Missing receiver"}

    con = get_db()
    cur = con.cursor()
    # שליפת קבצים שעדיין לא פג תוקפם ולא הגיעו למקסימום הורדות
    cur.execute("""
        SELECT id, sender, filename, uploaded_at, expires_at, max_downloads, download_count, message
        FROM shared_files
        WHERE receiver=%s AND expires_at >= NOW() AND download_count < max_downloads
        ORDER BY uploaded_at DESC
    """, (receiver,))
    rows = cur.fetchall()
    con.close()

    # המרת תאריכים לטקסט עבור JSON
    for r in rows:
        if r.get("uploaded_at"):
            r["uploaded_at"] = str(r["uploaded_at"])
        if r.get("expires_at"):
            r["expires_at"] = str(r["expires_at"])

    return {"status": "OK", "files": rows}


# הורדת תוכן קובץ (Base64) ועדכון מונה הורדות
def cmd_get_file(data):
    receiver = data.get("receiver", "").strip()
    file_id = data.get("file_id")

    if not receiver or not file_id:
        return {"status": "Missing fields"}

    con = get_db()
    cur = con.cursor()

    # שליפת פרטי הקובץ כולל המפתח המוצפן
    cur.execute("""
        SELECT filename, path, expires_at, encrypted_aes_key
        FROM shared_files
        WHERE id=%s AND receiver=%s
    """, (file_id, receiver))
    row = cur.fetchone()

    if not row:
        con.close()
        return {"status": "Not found"}

    if datetime.now() > row["expires_at"]:
        con.close()
        return {"status": "File expired"}

    cur.execute("UPDATE shared_files SET download_count = download_count + 1 WHERE id=%s", (file_id,))
    con.commit()

    # בדיקה האם הגענו למקסימום הורדות
    cur.execute("SELECT download_count, max_downloads, path FROM shared_files WHERE id=%s", (file_id,))
    limit_check = cur.fetchone()

    should_delete = False
    if limit_check and limit_check["download_count"] >= limit_check["max_downloads"]:
        should_delete = True

    con.close()

    # הורדת הקובץ מ-Supabase
    try:
        response = supabase.storage.from_(SUPABASE_BUCKET).download(row["path"])
        raw = response
    except Exception as e:
        print(f"Supabase download error: {e}")
        if os.path.exists(row["path"]):
            with open(row["path"], "rb") as f:
                raw = f.read()
        else:
            return {"status": "File not found"}

    # ניקוי סופי אם עברנו את הגבלת ההורדות
    if should_delete:
        try:
            _delete_file_internal(file_id)
        except:
            pass

    encoded = base64.b64encode(raw).decode()

    # פענוח מפתח ה-AES של הקובץ עם RSA הפרטי של השרת
    file_aes_key = rsa_decrypt(_rsa_private, row["encrypted_aes_key"])
    file_aes_key_b64 = base64.b64encode(file_aes_key).decode()

    log_history(receiver, "DOWNLOAD", f"Downloaded file '{row['filename']}' ({limit_check['download_count']}/{limit_check['max_downloads']})")

    return {
        "status": "OK",
        "filename": row["filename"],
        "filedata": encoded,
        "file_aes_key": file_aes_key_b64
    }


# שליפת כל הקבצים ששותפו במערכת (עבור המפעיל/אדמין)
def cmd_all_sent_files(data):
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT id, sender, receiver, filename, path, uploaded_at, expires_at, max_downloads, download_count, message
        FROM shared_files
        ORDER BY uploaded_at DESC
    """)
    rows = cur.fetchall()
    con.close()

    # המרת תאריכים לטקסט עבור פורמט JSON
    for r in rows:
        if r.get("uploaded_at"):
            r["uploaded_at"] = str(r["uploaded_at"])
        if r.get("expires_at"):
            r["expires_at"] = str(r["expires_at"])

    return {"status": "OK", "files": rows}


# בקשת קובץ להדפסה ויצירת "עבודת הדפסה" חדשה
def cmd_request_print(data):
    file_id = data.get("file_id")
    operator = data.get("operator", "operator").strip()

    if not file_id:
        return {"status": "Missing file_id"}

    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT id, sender, filename, path, encrypted_aes_key
        FROM shared_files
        WHERE id=%s
    """, (file_id,))
    row = cur.fetchone()

    if not row:
        con.close()
        return {"status": "File not found"}

    filename = row["filename"]
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    print_allowed = ext in ("pdf", "docx")

    # הורדה מ-Supabase
    try:
        raw = supabase.storage.from_(SUPABASE_BUCKET).download(row["path"])
    except Exception as e:
        con.close()
        print(f"Supabase download error: {e}")
        return {"status": "Storage error"}

    # הכנסת עבודת הדפסה במצב ממתין (Pending)
    cur.execute("""
        INSERT INTO print_jobs (file_id, sender, filename, file_type, print_allowed, print_status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
    """, (file_id, row["sender"], filename, ext, int(print_allowed)))
    job_id = cur.lastrowid
    con.commit()
    con.close()

    encoded = base64.b64encode(raw).decode()

    # פענוח מפתח ה-AES של הקובץ עם RSA הפרטי של השרת (כדי שהלקוח יוכל לפענח אותו להדפסה)
    file_aes_key = rsa_decrypt(_rsa_private, row["encrypted_aes_key"])
    file_aes_key_b64 = base64.b64encode(file_aes_key).decode()

    log_history(operator, "PRINT_REQUEST", f"Requested print of '{filename}' (job #{job_id})")

    return {
        "status": "OK",
        "job_id": job_id,
        "filename": filename,
        "file_type": ext,
        "filedata": encoded,
        "file_aes_key": file_aes_key_b64
    }


# עדכון סטטוס הדפסה (ממתין -> הודפס)
def cmd_update_print_status(data):
    job_id = data.get("job_id")
    status = data.get("status", "printed").strip()

    if not job_id or status not in ("pending", "printed"):
        return {"status": "Missing or invalid fields"}

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE print_jobs SET print_status=%s WHERE id=%s", (status, job_id))
    con.commit()
    con.close()

    return {"status": "OK"}


# שליפת רשימת כל שמות המשתמשים במערכת
def cmd_all_users(data):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT username FROM data ORDER BY username")
    rows = cur.fetchall()
    con.close()

    users = [r["username"] for r in rows]
    return {"status": "OK", "users": users}


# עדכון נוכחות המשתמש כ-Online
def cmd_user_online(data):
    username = data.get("username", "").strip()
    if not username:
        return {"status": "Missing username"}

    online_users[username] = datetime.now()
    return {"status": "OK"}


# שליפת רשימת המשתמשים שנראו לאחרונה
def cmd_online_users(data):
    now = datetime.now()
    active = [
        u for u, t in online_users.items()
        if (now - t).total_seconds() < ONLINE_TIMEOUT_SECONDS
    ]
    return {"status": "OK", "online": active}


# ניהול משתמשים - שליפת כל המשתמשים עם סטטוס חסימה (לאדמין)
def cmd_admin_users(data):
    admin_user = data.get("admin_user", "")

    if not is_admin(admin_user):
        return {"status": "Forbidden"}

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT username, is_blocked, is_admin FROM data ORDER BY username")
    rows = cur.fetchall()
    con.close()

    for r in rows:
        r["is_blocked"] = bool(r["is_blocked"])
        r["is_admin"] = bool(r["is_admin"])

    return {"status": "OK", "users": rows}


# חסימה או שחרור של משתמש מהמערכת (לאדמין)
def cmd_admin_block_user(data):
    admin_user = data.get("admin_user", "")
    target_user = data.get("target_user", "")
    block = data.get("block", False)

    if not is_admin(admin_user):
        return {"status": "Forbidden"}

    if admin_user == target_user:
        return {"status": "Cannot block self"}

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE data SET is_blocked=%s WHERE username=%s", (1 if block else 0, target_user))
    con.commit()
    con.close()

    action = "BLOCKED" if block else "UNBLOCKED"
    log_history(admin_user, "ADMIN_ACTION", f"{action} user {target_user}")

    return {"status": "OK"}


# שליפת היסטוריית הפעולות במערכת (לאדמין)
def cmd_admin_history(data):
    admin_user = data.get("admin_user", "")
    target_user = data.get("target_user", None)

    if not is_admin(admin_user):
        return {"status": "Forbidden"}

    con = get_db()
    cur = con.cursor()

    if target_user:
        cur.execute("SELECT * FROM history WHERE username=%s ORDER BY timestamp DESC", (target_user,))
    else:
        cur.execute("SELECT * FROM history ORDER BY timestamp DESC")

    rows = cur.fetchall()
    con.close()

    # המרת תאריכים לטקסט עבור JSON
    for r in rows:
        if r.get("timestamp"):
            r["timestamp"] = str(r["timestamp"])

    return {"status": "OK", "history": rows}


# ------------------------------------------------------------
#  טבלת הפקודות: שם הפקודה -> הפונקציה שמטפלת בה
# ------------------------------------------------------------
COMMANDS = {
    "public_key":          cmd_public_key,
    "session_key":         cmd_session_key,
    "signup":              cmd_signup,
    "login":               cmd_login,
    "upload_file":         cmd_upload_file,
    "incoming_files":      cmd_incoming_files,
    "get_file":            cmd_get_file,
    "all_sent_files":      cmd_all_sent_files,
    "request_print":       cmd_request_print,
    "update_print_status": cmd_update_print_status,
    "all_users":           cmd_all_users,
    "user_online":         cmd_user_online,
    "online_users":        cmd_online_users,
    "admin/users":         cmd_admin_users,
    "admin/block_user":    cmd_admin_block_user,
    "admin/history":       cmd_admin_history,
}


# טיפול בלקוח יחיד - רץ ב-Thread נפרד עבור כל לקוח שמתחבר (שרת מרובה לקוחות)
def handle_client(client_sock, addr):
    try:
        # ניקוי קבצים שפג תוקפם (כמו שהיה לפני כל בקשה)
        cleanup_expired_files()

        request = recv_message(client_sock)          # קריאת הבקשה מהלקוח
        command = request.get("command", "")
        data = request.get("data", {}) or {}

        handler = COMMANDS.get(command)
        if handler is None:
            response = {"status": f"Unknown command: {command}"}
        else:
            response = handler(data)                  # הפעלת הפונקציה המתאימה

        send_message(client_sock, response)           # שליחת התשובה ללקוח
    except Exception as e:
        print(f"Error handling client {addr}: {e}")
        try:
            send_message(client_sock, {"status": f"Server error: {e}"})
        except Exception:
            pass
    finally:
        client_sock.close()


# לולאת השרת הראשית - מאזינה לחיבורים ופותחת Thread לכל לקוח
def start_server():
    init_db()  # אתחול בסיס הנתונים

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # מאפשר להפעיל מחדש את השרת מיד בלי שגיאת "כתובת תפוסה"
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((APP_HOST, APP_PORT))
    server_sock.listen()
    print(f"Socket server listening on {APP_HOST}:{APP_PORT}")

    while True:
        client_sock, addr = server_sock.accept()      # ממתין ללקוח חדש
        # פתיחת Thread נפרד לכל לקוח -> מאפשר טיפול במספר לקוחות במקביל
        t = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True)
        t.start()


if __name__ == "__main__":
    start_server()
