
import base64
import os

from Client.client_config import SERVER_URL, send_request
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QMessageBox, QSizePolicy,
    QFileDialog
)
from PyQt5.uic import loadUi

PRINTABLE_EXTS = {"pdf", "docx", "doc"}

ICON_MAP = {
    "pdf": "PDF", "docx": "DOC", "doc": "DOC",
    "png": "IMG", "jpg": "IMG", "jpeg": "IMG",
    "mp4": "VID", "zip": "ZIP", "xlsx": "XLS", "txt": "TXT",
}



# תהליכי רקע לביצוע פעולות ללא תקיעת הממשק

# תהליך לשליפת רשימת הקבצים מהשרת
class FetchFilesThread(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, server_url):
        super().__init__()
        self.server_url = server_url

    # הרצת השליפה מהשרת
    def run(self):
        try:
            data = send_request("all_sent_files", {}, timeout=10)
            if data.get("status") == "OK":
                self.done.emit(data.get("files", []))
            else:
                self.error.emit(data.get("status", "Unknown error"))
        except Exception as e:
            self.error.emit(str(e))


# תהליך לבקשת הדפסה מהשרת (מוריד את הקובץ ומתכונן להדפסה)
class PrintRequestThread(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, server_url, file_id, operator):
        super().__init__()
        self.server_url = server_url
        self.file_id    = file_id
        self.operator   = operator

    # ביצוע בקשת ההדפסה לשרת (מוריד את הקובץ מה-Storage)
    def run(self):
        try:
            data = send_request("request_print",
                                 {"file_id": self.file_id, "operator": self.operator},
                                 timeout=30)
            if data.get("status") == "OK":
                self.done.emit(data)
            else:
                self.error.emit(data.get("status", "Unknown error"))
        except Exception as e:
            self.error.emit(str(e))

# החלון המרכזי

# מחלקת חלון ההדפסה (לשעבר חלון קבצים שנשלחו)
class PrintWindow(QMainWindow):
    closed = pyqtSignal()

    def __init__(self, current_user="operator", server_url=SERVER_URL):
        super().__init__()
        self.current_user = current_user
        self.server_url   = server_url
        self._threads: list = []
        self._card_index  = 0

        ui_path = os.path.join(os.path.dirname(__file__), "printwindow.ui")
        loadUi(ui_path, self)

        # טעינת המבנה של רשימת הקבצים
        self._cards_layout = self.scroll_content.layout()

        self.btn_back.clicked.connect(self.close)
        self.btn_refresh.clicked.connect(self._load_files)
        self.btn_upload_print.clicked.connect(self._on_upload_print)
        self.input_filter.textChanged.connect(self._apply_filter)
        self._load_files()

    # העלאת קובץ מקומי להדפסה מהירה
    def _on_upload_print(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select File to Print",
            "", "Printable Files (*.pdf *.docx *.doc);;All Files (*)"
        )
        if not path:
            return

        filename = os.path.basename(path)
        ext = os.path.splitext(filename)[1].lower().lstrip(".")

        if ext not in PRINTABLE_EXTS:
            QMessageBox.warning(
                self, "Unsupported File",
                "Only PDF, DOCX and DOC files can be printed."
            )
            return

        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not read file:\n{e}")
            return

        if ext in ("docx", "doc"):
            try:
                raw = _docx_to_pdf_bytes(raw)
            except Exception as e:
                QMessageBox.critical(
                    self, "Conversion Error",
                    f"Word → PDF conversion failed:\n{e}"
                )
                return

        from Client.print_preview_window import PrintPreviewWindow
        dlg = PrintPreviewWindow(
            pdf_bytes  = raw,
            filename   = filename,
            job_id     = None,
            server_url = self.server_url,
            operator   = self.current_user,
            parent     = self,
        )
        dlg.exec_()

    # טעינת נתונים מהשרת

    # טעינת רשימת הקבצים מהשרת (מתבצע ברקע)
    def _load_files(self):
        self.btn_refresh.setEnabled(False)
        self.label_status.setText("Loading…")
        self._clear_cards()

        t = FetchFilesThread(self.server_url)
        t.done.connect(self._on_files_loaded)
        t.error.connect(self._on_load_error)
        t.finished.connect(lambda: self.btn_refresh.setEnabled(True))
        self._threads.append(t)
        t.start()

    # עיבוד רשימת הקבצים לאחר שהיא נטענה מהשרת
    def _on_files_loaded(self, files):
        self._clear_cards()
        if not files:
            self.label_status.setText("No files found.")
            return
        self.label_status.setText(f"{len(files)} file(s) found")
        for f in files:
            self._add_file_card(f)

    # פונקציית עזר המציגה שגיאה אם טעינת הקבצים נכשלה
    def _on_load_error(self, msg):
        self.label_status.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Connection Error",
                             f"Could not fetch sent files:\n{msg}")

    # ניקוי כל כרטיסי הקבצים מהמסך
    def _clear_cards(self):
        layout = self._cards_layout
        # מסיר הכל חוץ מהרווח הריק שבסוף (הפריט האחרון)
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._card_index = 0

    # בניית כרטיסי הקבצים

    # הוספת "כרטיס" קובץ לתצוגה בתוך הרשימה
    def _add_file_card(self, f):
        filename = f.get("filename", "unknown")
        sender   = f.get("sender",   "unknown")
        receiver = f.get("receiver", "?")
        ts       = str(f.get("uploaded_at", ""))[:16]
        file_id  = f.get("id")
        ext      = os.path.splitext(filename)[1].lower().lstrip(".")

        card = QFrame(objectName=f"card_{self._card_index}")
        self._card_index += 1
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        # אייקון סוג קובץ
        icon_lbl = QLabel(ICON_MAP.get(ext, "FILE"))
        icon_lbl.setFont(QFont("Segoe UI Emoji", 22))
        icon_lbl.setFixedWidth(36)
        row.addWidget(icon_lbl)

        # טקסט ופרטי הקובץ
        col = QVBoxLayout()
        col.setSpacing(3)
        name_lbl = QLabel(filename)
        name_lbl.setFont(QFont("Segoe UI", 12, QFont.Bold))
        name_lbl.setStyleSheet("color: #222;")
        dl_count = f.get("download_count", 0)
        dl_max   = f.get("max_downloads", 1)
        meta_lbl = QLabel(
            f"From: <b>{sender}</b>  →  To: <b>{receiver}</b>"
            + (f"   <span style='color:#999;'>{ts}</span>" if ts else "")
            + f"   <span style='color:#0077ff;'>[Downloads: {dl_count}/{dl_max}]</span>"
        )
        meta_lbl.setTextFormat(Qt.RichText)
        meta_lbl.setStyleSheet("color: #555; font-size: 11px;")
        col.addWidget(name_lbl)
        col.addWidget(meta_lbl)
        row.addLayout(col, stretch=1)

        # כפתור הדפסה (רק ל-PDF ו-Word)
        if ext in PRINTABLE_EXTS:
            btn = QPushButton("Print")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedWidth(100)
            btn.clicked.connect(
                lambda _, fid=file_id, fn=filename, fe=ext, b=btn:
                    self._on_print_clicked(fid, fn, fe, b)
            )
            row.addWidget(btn)

        # הכנסה לרשימה לפני הרווח הריק שבסוף
        self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

    # סינון הרשימה

    # סינון רשימת הקבצים לפי טקסט החיפוש
    def _apply_filter(self):
        filter_text = self.input_filter.text().lower()
        layout = self._cards_layout
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            if w and isinstance(w, QFrame) and w.objectName().startswith("card_"):
                # בדיקת כל התוויות בתוך הכרטיס
                labels = w.findChildren(QLabel)
                text = " ".join(lbl.text() for lbl in labels).lower()
                w.setVisible(filter_text in text)

    # תהליך ההדפסה

    # פונקציה המופעלת כשלוחצים על כפתור הדפסה בכרטיס קובץ
    def _on_print_clicked(self, file_id, filename, ext, btn: QPushButton):
        btn.setEnabled(False)
        btn.setText("…")

        t = PrintRequestThread(self.server_url, file_id, self.current_user)
        t.done.connect(
            lambda data, b=btn, fn=filename, fe=ext:
                self._on_print_ready(data, b, fn, fe)
        )
        t.error.connect(
            lambda msg, b=btn: self._on_print_error(msg, b)
        )
        self._threads.append(t)
        t.start()

    # פתיחת חלון התצוגה המקדימה לאחר שהקובץ מוכן להדפסה
    def _on_print_ready(self, data, btn: QPushButton, filename, ext):
        btn.setEnabled(True)
        btn.setText("Print")

        raw    = base64.b64decode(data["filedata"])
        job_id = data.get("job_id")

        if ext in ("docx", "doc"):
            try:
                raw = _docx_to_pdf_bytes(raw)
            except Exception as e:
                QMessageBox.critical(self, "Conversion Error",
                                     f"Word → PDF failed:\n{e}")
                return

        from Client.print_preview_window import PrintPreviewWindow
        dlg = PrintPreviewWindow(
            pdf_bytes  = raw,
            filename   = filename,
            job_id     = job_id,
            server_url = self.server_url,
            operator   = self.current_user,
            parent     = self,
        )
        dlg.exec_()

    # מציג הודעת שגיאה אם הבקשה להדפסה נכשלה
    def _on_print_error(self, msg, btn: QPushButton):
        btn.setEnabled(True)
        btn.setText("Print")
        QMessageBox.critical(self, "Print Error",
                             f"Could not fetch file for printing:\n{msg}")

    # סגירת החלון — שולח אות חזרה למסך הבית
    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


# המרת קבצי Word ל-PDF
# שיטה ראשונה: docx2pdf (משתמש ב-Word מותקן - תמיכה מושלמת בעברית)
# שיטה חלופית: reportlab (בסיסי, אם אין Word מותקן)

def _docx_to_pdf_bytes(docx_bytes: bytes) -> bytes:
    import os
    import tempfile

    # ניסיון להשתמש ב-Word מותקן (הכי טוב לעברית)
    try:
        from docx2pdf import convert
        import pythoncom
        pythoncom.CoInitialize() 

        suffix = ".docx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(docx_bytes)
            docx_path = tmp.name

        pdf_path = docx_path[:-len(suffix)] + ".pdf"
        try:
            convert(docx_path, pdf_path)
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    return f.read()
        finally:
            try: os.unlink(docx_path)
            except: pass
            try: os.unlink(pdf_path)
            except: pass
    except Exception as e:
        print(f"docx2pdf failed: {e}")

    # שיטה חלופית אם אין Word מותקן
    import io
    from docx import Document
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # רישום פונט אריאל לתמיכה בעברית
    font_name = "Helvetica"
    arial_path = r"C:\Windows\Fonts\arial.ttf"
    if os.path.exists(arial_path):
        try:
            pdfmetrics.registerFont(TTFont('Arial', arial_path))
            font_name = "Arial"
        except:
            pass

    # ייבוא תמיכה בכיוון כתיבה מימין לשמאל (RTL)
    try:
        from bidi.algorithm import get_display
        HAS_BIDI = True
    except ImportError:
        HAS_BIDI = False

    doc_obj = Document(io.BytesIO(docx_bytes))
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    # alignment=2 is Right
    body_style = ParagraphStyle("Body", parent=styles["Normal"],
                                fontName=font_name, fontSize=11, leading=16, 
                                spaceAfter=6, alignment=2)
    h1_style = ParagraphStyle("H1", parent=styles["Heading1"],
                                fontName=font_name, fontSize=16, spaceAfter=10, alignment=2)
    
    def fix_hebrew_manual(text):
        # שיטה פשוטה להפוך טקסט בעברית ולטפל במספרים/טקסט מעורב
        import re
        # חיפוש קטעי טקסט בעברית
        hebrew_pattern = re.compile(r'[\u0590-\u05FF]+')
        
        # אם יש עברית, נהפוך את סדר האותיות (Bidi ויזואלי פשוט)
        if hebrew_pattern.search(text):
            words = text.split()
            fixed_words = []
            for w in words:
                if hebrew_pattern.search(w):
                    fixed_words.append(w[::-1]) # היפוך אותיות
                else:
                    fixed_words.append(w)
            # היפוך סדר המילים עבור כיוון מימין לשמאל
            return " ".join(fixed_words[::-1])
        return text

    story = []
    for para in doc_obj.paragraphs:
        if not para.text.strip():
            story.append(Spacer(1, 6))
            continue
        
        pname = para.style.name if para.style else ""
        ps = h1_style if "Heading 1" in pname else body_style
        
        # הפעלת היפוך אותיות ידני עבור עברית
        text = fix_hebrew_manual(para.text)

        txt = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        story.append(Paragraph(txt, ps))
        
    pdf.build(story)
    return buf.getvalue()

