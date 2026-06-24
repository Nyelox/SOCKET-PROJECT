import os
import base64

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QMainWindow, QFileDialog, QListWidgetItem, QMessageBox, QCompleter
)
from PyQt5.uic import loadUi
from Client.client_config import SERVER_URL, send_request
from Client import Client_crypto as crypto_utils


# תהליכי רקע לביצוע פעולות ללא תקיעת הממשק

# מעדכן את השרת שהמשתמש עדיין מחובר (Online)
class HeartbeatThread(QThread):
    def __init__(self, server_url, username):
        super().__init__()
        self.server_url = server_url
        self.username   = username

    # הרצת תהליך עדכון הנוכחות מול השרת
    def run(self):
        try:
            send_request("user_online", {"username": self.username}, timeout=3)
        except Exception:
            pass


# טוען את רשימת המשתמשים מהשרת
class UsersRefreshThread(QThread):
    done = pyqtSignal(list)   # רשימה של שמות משתמשים (תצוגה ושם פנימי)

    def __init__(self, server_url, current_user):
        super().__init__()
        self.server_url   = server_url
        self.current_user = current_user

    # משיכת רשימת כל המשתמשים ובדיקה מי מהם מחובר כרגע
    def run(self):
        try:
            all_users = send_request("all_users", {}, timeout=5).get("users", [])
        except Exception:
            all_users = []

        try:
            online_set = set(send_request("online_users", {}, timeout=3).get("online", []))
        except Exception:
            online_set = set()

        others       = [u for u in all_users if u != self.current_user]
        online_first = [u for u in others if u in online_set]
        offline_rest = [u for u in others if u not in online_set]

        result = [(u, u) for u in online_first] + [(u, u) for u in offline_rest]
        self.done.emit(result)


# מושך רשימת קבצים שנשלחו למשתמש
class IncomingFilesThread(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, server_url, receiver):
        super().__init__()
        self.server_url = server_url
        self.receiver   = receiver

    # ביצוע הבקשה לשרת לקבלת קבצים שמחכים למשתמש
    def run(self):
        try:
            data = send_request("incoming_files", {"receiver": self.receiver}, timeout=8)
            if data.get("status") == "OK":
                self.done.emit(data.get("files", []))
            else:
                self.error.emit(data.get("status", "Error"))
        except Exception as e:
            self.error.emit(str(e))


# מעלה קובץ חדש לשרת
class SendFileThread(QThread):
    success = pyqtSignal()
    error   = pyqtSignal(str)

    def __init__(self, server_url, payload):
        super().__init__()
        self.server_url = server_url
        self.payload    = payload

    # ביצוע העלאת הקובץ והנתונים לשרת
    def run(self):
        try:
            data = send_request("upload_file", self.payload, timeout=30)
            if data.get("status") == "OK":
                self.success.emit()
            else:
                self.error.emit(data.get("status", "Upload failed"))
        except Exception as e:
            self.error.emit(str(e))


# מוריד קובץ מהשרת למחשב המקומי
class DownloadFileThread(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, server_url, receiver, file_id):
        super().__init__()
        self.server_url = server_url
        self.receiver   = receiver
        self.file_id    = file_id

    # הורדת תוכן הקובץ מהשרת (בפורמט Base64)
    def run(self):
        try:
            data = send_request("get_file",
                                 {"receiver": self.receiver, "file_id": self.file_id},
                                 timeout=30)
            if data.get("status") == "OK":
                self.done.emit(data)
            else:
                self.error.emit(data.get("status", "Error"))
        except Exception as e:
            self.error.emit(str(e))


# החלון המרכזי לשליחת וקבלת קבצים
class SendFileWindow(QMainWindow):
    closed = pyqtSignal()   # סיגנל שמשודר כשהחלון נסגר - מחזיר לחלון הבית

    # אתחול החלון, הגדרת כפתורים וטיימרים
    def __init__(self, current_user, users_list, server_url=SERVER_URL):
        super().__init__()

        ui_path = os.path.join(os.path.dirname(__file__), "sendfile_window.ui")
        loadUi(ui_path, self)

        self.current_user     = current_user
        self.server_url       = server_url
        self._all_users_cache = []
        self._threads: list   = []   # שמירת הפניות לתהליכים כדי שלא יימחקו מהזיכרון
        self.selected_file_path = None
        self.incoming_raw       = []

        self.label_current_user.setText(f"Connected as: {current_user}")

        # הגדרת כפתורים
        self.btn_select.clicked.connect(self.select_file)
        self.btn_send.clicked.connect(self.send_file)
        self.btn_refresh.clicked.connect(self.refresh_incoming)
        self.btn_download.clicked.connect(self.download_selected)
        self.input_filter.textChanged.connect(self.apply_filter)

        # כפתור "חזור" (מוגדר ב-.ui) - חוזר לחלון הבית
        self.btn_back.clicked.connect(self.close)

        # השלמה אוטומטית עבור נמענים
        self.completer = QCompleter([], self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.input_receivers.setCompleter(self.completer)

        # טיימר "אות חיים" - מעדכן נוכחות בלי לתקוע את הממשק
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.timeout.connect(self._send_heartbeat)
        self.heartbeat_timer.start(8000)

        # טיימר לרענון רשימת המשתמשים המחוברים
        self.users_refresh_timer = QTimer(self)
        self.users_refresh_timer.timeout.connect(self.refresh_users_online)
        self.users_refresh_timer.start(15000)

        # טעינה ראשונית של נתונים
        self.refresh_incoming()
        self.refresh_users_online()

    # עצירת הטיימרים והודעה לחלון הבית כשהחלון נסגר
    def closeEvent(self, event):
        self.heartbeat_timer.stop()
        self.users_refresh_timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    # פתיחת דיאלוג לבחירת קובץ מהמחשב
    def select_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select File")
        if path:
            self.selected_file_path = path
            self.lbl_file.setText(os.path.basename(path))
            self.btn_send.setEnabled(True)

    # איסוף נתוני השליחה (נמענים, הודעה, הגבלות) ושליחה לשרת
    def send_file(self):
        if not self.selected_file_path:
            QMessageBox.warning(self, "Error", "No file selected")
            return

        # קבלת שמות הנמענים מתיבת הטקסט (מופרדים בפסיקים)
        raw_recipients = self.input_receivers.text().split(",")
        receivers = [r.strip() for r in raw_recipients if r.strip()]

        if not receivers:
            QMessageBox.warning(self, "Error", "Write a name to send")
            return

        filename = os.path.basename(self.selected_file_path)
        with open(self.selected_file_path, "rb") as f:
            raw = f.read()

        # === הצפנה היברידית של הקובץ ===
        # 1. יצירת מפתח AES חדש ייחודי לקובץ הזה
        file_aes_key = crypto_utils.generate_aes_key()
        # 2. הצפנת תוכן הקובץ עם AES
        encrypted_file = crypto_utils.aes_encrypt_bytes(file_aes_key, raw)
        # 3. הצפנת מפתח ה-AES עם המפתח הציבורי RSA של השרת
        encrypted_aes_key = crypto_utils.rsa_encrypt(crypto_utils.SERVER_PUBLIC_KEY, file_aes_key)

        payload = {
            "sender":    self.current_user,
            "receivers": receivers,
            "filename":  filename,
            "filedata":  base64.b64encode(encrypted_file).decode(),
            "encrypted_aes_key": encrypted_aes_key,
            "minutes":   int(self.spin_minutes.value()),
            "max_downloads": int(self.spin_downloads.value()),
            "message":   self.input_file_message.text().strip(),
        }

        self.btn_send.setEnabled(False)
        self.btn_send.setText("Sending...")

        t = SendFileThread(self.server_url, payload)
        t.success.connect(self._on_send_success)
        t.error.connect(self._on_send_error)
        self._threads.append(t)
        t.start()

    # מנקה את הטופס לאחר שליחה מוצלחת
    def _on_send_success(self):
        self.btn_send.setText("Send")
        self.btn_send.setEnabled(True)
        QMessageBox.information(self, "Success", "File sent successfully to all the users")
        self.lbl_file.setText("No file selected")
        self.input_file_message.clear()
        self.input_receivers.clear()
        self.selected_file_path = None

    # מציג הודעת שגיאה אם השליחה נכשלה
    def _on_send_error(self, msg):
        self.btn_send.setText("Send")
        self.btn_send.setEnabled(True)
        QMessageBox.warning(self, "Error", msg)

    # שליחת אות חיים לשרת בכל כמה שניות
    def _send_heartbeat(self):
        t = HeartbeatThread(self.server_url, self.current_user)
        self._threads.append(t)
        t.start()

    # רענון רשימת הקבצים שחכו למשתמש
    def refresh_incoming(self):
        t = IncomingFilesThread(self.server_url, self.current_user)
        t.done.connect(self._on_incoming_loaded)
        t.error.connect(lambda _: None)   # התעלמות משגיאות ברענון אוטומטי ברקע
        self._threads.append(t)
        t.start()

    # עיבוד רשימת הקבצים הנכנסים והצגתם ברשימה
    def _on_incoming_loaded(self, files):
        self.list_incoming.clear()
        self.incoming_raw = files
        for f in files:
            msg_part = f" | Msg: {f['message']}" if f.get("message") else ""
            dl_part = f" | Downloads: {f.get('download_count', 0)}/{f.get('max_downloads', 1)}"
            txt  = (f"{f['filename']} | From: {f['sender']} "
                    f"| Expires: {f['expires_at']}{dl_part}{msg_part}")
            item = QListWidgetItem(txt)
            item.setData(Qt.UserRole, f["id"])
            self.list_incoming.addItem(item)
        self.apply_filter()

    # בקשת רשימת המשתמשים המחוברים כרגע
    def refresh_users_online(self):
        t = UsersRefreshThread(self.server_url, self.current_user)
        t.done.connect(self._on_users_loaded)
        self._threads.append(t)
        t.start()

    # שמירת רשימת המשתמשים ועדכון הממשק
    def _on_users_loaded(self, users):
        self._all_users_cache = users
        self.rebuild_receivers()

    # עדכון רשימת המשתמשים בתיבת הבחירה של הנמענים
    def rebuild_receivers(self):
        # עדכון המשלים האוטומטי (Completer) עם שמות המשתמשים
        all_usernames = [u for _, u in self._all_users_cache]
        
        # יצירת מודל חדש עבור רשימת ההשלמה
        from PyQt5.QtCore import QStringListModel
        model = QStringListModel(all_usernames)
        self.completer.setModel(model)

    # רענון רשימת המשלים האוטומטי
    def filter_users_combo(self):
        self.rebuild_receivers()

    # סינון רשימת הקבצים לפי טקסט החיפוש
    def apply_filter(self):
        filter_text = self.input_filter.text().lower()
        for i in range(self.list_incoming.count()):
            item = self.list_incoming.item(i)
            item.setHidden(filter_text not in item.text().lower())

    # הורדת הקובץ שנבחר מהרשימה ושמירתו במחשב
    def download_selected(self):
        item = self.list_incoming.currentItem()
        if not item:
            QMessageBox.information(self, "No Selection",
                                    "Please select a file from the list")
            return

        file_id = item.data(Qt.UserRole)
        self.btn_download.setEnabled(False)
        self.btn_download.setText("Downloading...")

        t = DownloadFileThread(self.server_url, self.current_user, file_id)
        t.done.connect(self._on_download_done)
        t.error.connect(self._on_download_error)
        self._threads.append(t)
        t.start()

    # עיבוד הקובץ שהורד ושמירתו במחשב המשתמש
    def _on_download_done(self, data):
        self.btn_download.setEnabled(True)
        self.btn_download.setText("Download")

        # === פענוח היברידי של הקובץ ===
        # 1. הקובץ המוצפן וה-AES key מהשרת
        encrypted_file = base64.b64decode(data["filedata"])
        file_aes_key = base64.b64decode(data["file_aes_key"])
        # 2. פענוח תוכן הקובץ עם AES
        raw = crypto_utils.aes_decrypt_bytes(file_aes_key, encrypted_file)

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save File", data["filename"])
        if save_path:
            with open(save_path, "wb") as f:
                f.write(raw)
            QMessageBox.information(self, "Success",
                                    "File saved successfully")

    # הודעה למשתמש אם ההורדה נכשלה
    def _on_download_error(self, msg):
        self.btn_download.setEnabled(True)
        self.btn_download.setText("Download")
        QMessageBox.warning(self, "Error", msg)
