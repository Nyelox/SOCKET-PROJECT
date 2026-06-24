import json
import os
import socket
import struct

_config_path = os.path.join(os.path.dirname(__file__), "client.config.json")

try:
    with open(_config_path, "r") as f:
        _cfg = json.load(f)
    SERVER_IP = _cfg.get("server_ip", "127.0.0.1")
    SERVER_PORT = int(_cfg.get("server_port", 5000))
except Exception:
    SERVER_IP = "127.0.0.1"
    SERVER_PORT = 5000

# נשמר לתאימות לאחור (חלק מהקבצים עדיין מייבאים את זה)
SERVER_URL = f"http://{SERVER_IP}:{SERVER_PORT}"


# ============================================================
#  פרוטוקול התקשורת מבוסס סוקטים (צד הלקוח)
# ------------------------------------------------------------
#  כל הודעה:  [4 בתים = אורך] + [גוף JSON ב-UTF-8]
#  שולחים:    {"command": "<שם>", "data": {...}}
#  מקבלים:    {"status": "...", ...}
# ============================================================

# שליחת מילון דרך הסוקט (אורך 4 בתים + גוף JSON)
def _send_all(sock, obj):
    body = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


# קריאת בדיוק n בתים מהסוקט
def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by server")
        buf += chunk
    return buf


# קבלת מילון מהסוקט לפי הפרוטוקול
def _recv_msg(sock):
    header = _recv_exact(sock, 4)
    length = struct.unpack(">I", header)[0]
    return json.loads(_recv_exact(sock, length).decode("utf-8"))


def send_request(command, data=None, timeout=30):
    """
    פותח חיבור סוקט חדש לשרת, שולח פקודה אחת, ומחזיר את תשובת השרת (מילון).
    מחליף את הקריאות הישנות של requests (HTTP) בתקשורת ישירה מבוססת סוקטים.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((SERVER_IP, SERVER_PORT))
        _send_all(sock, {"command": command, "data": data or {}})
        return _recv_msg(sock)
    finally:
        sock.close()
