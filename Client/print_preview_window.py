
import os
import socket as _socket

from Client.client_config import send_request
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QPainter
from PyQt5.QtPrintSupport import QPrinter, QPrinterInfo
from PyQt5.QtWidgets import (
    QDialog, QLabel, QFrame, QMessageBox
)
from PyQt5.uic import loadUi

DPI_PREVIEW = 120
DPI_PRINT   = 300





class SocketPrintThread(QThread):
    success  = pyqtSignal()
    error    = pyqtSignal(str)
    progress = pyqtSignal(int, int)   # sent, total

    def __init__(self, host: str, port: int, pdf_bytes: bytes):
        super().__init__()
        self.host      = host
        self.port      = port
        self.pdf_bytes = pdf_bytes

    # הפונקציה המרכזית שמריצה את שליחת הנתונים למדפסת ברקע
    def run(self):
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.settimeout(15)
            sock.connect((self.host, self.port))

            total      = len(self.pdf_bytes)
            chunk_size = 8192
            sent       = 0
            while sent < total:
                chunk = self.pdf_bytes[sent:sent + chunk_size]
                sock.sendall(chunk)
                sent += len(chunk)
                self.progress.emit(sent, total)

            sock.close()
            self.success.emit()
        except Exception as e:
            self.error.emit(str(e))



class PrinterScanThread(QThread):

    found_printer = pyqtSignal(str)   # emits IP
    finished      = pyqtSignal()
    def run(self):
        try:
            import socket
            from concurrent.futures import ThreadPoolExecutor

            local_ip = socket.gethostbyname(socket.gethostname())
            
            prefix = ".".join(local_ip.split(".")[:-1])
            common_ports = [9100, 515, 631, 80, 443]

            def check_ip(ip):
                for port in common_ports:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1.0) # Maximum patience
                        res = sock.connect_ex((ip, port))
                        sock.close()
                        if res == 0:
                            return ip
                    except:
                        pass
                return None

            # Scan all 254 IPs in parallel with more workers
            with ThreadPoolExecutor(max_workers=100) as executor:
                ips_to_check = [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" != local_ip]
                results = executor.map(check_ip, ips_to_check)
                for ip in results:
                    if ip:
                        # Skip common gateway IPs (likely routers)
                        if not (ip.endswith(".1") or ip.endswith(".254")):
                            self.found_printer.emit(ip)
            
            self.finished.emit()
        except Exception as e:
            print(f"Scan error: {e}")
            self.finished.emit()


class PrintPreviewWindow(QDialog):
    # פונקציית האתחול של החלון - מגדירה משתנים וטוענת את העיצוב
    def __init__(self, pdf_bytes: bytes, filename: str,
                 job_id: int, server_url: str, operator: str,
                 parent=None):
        super().__init__(parent)
        self.pdf_bytes  = pdf_bytes
        self.filename   = filename
        self.job_id     = job_id
        self.server_url = server_url
        self.operator   = operator
        self._threads: list = []

        ui_path = os.path.join(os.path.dirname(__file__), "print_preview_window.ui")
        loadUi(ui_path, self)


        self.setWindowTitle(f"Print Preview — {filename}")
        self.setModal(True)

        self._setup_ui()
        self._render_pages()

    # ── initial setup ────────────────────────────────────────────────────────

    # הגדרת כפתורים, חיבור אירועים וטעינת רשימת מדפסות ה-Windows
    def _setup_ui(self):
        self.label_title.setText(self.filename)

        # populate Windows printers
        self.combo_printers.clear()
        for info in QPrinterInfo.availablePrinters():
            self.combo_printers.addItem(info.printerName())
        default = QPrinterInfo.defaultPrinter()
        if default and not default.isNull():
            idx = self.combo_printers.findText(default.printerName())
            if idx >= 0:
                self.combo_printers.setCurrentIndex(idx)

        # radio button logic
        self.radio_windows.toggled.connect(self._on_radio_changed)
        self.radio_network.toggled.connect(self._on_radio_changed)
        self._on_radio_changed()

        # buttons
        self.btn_close.clicked.connect(self.reject)
        self.btn_print.clicked.connect(self._do_print)
        self.btn_scan.clicked.connect(self._scan_network)
        
        # Auto-find IPs of installed printers
        self._find_installed_printer_ips()

    # חיפוש כתובות IP של מדפסות שכבר מותקנות על המחשב (כגיבוי)
    def _find_installed_printer_ips(self):
        """Tries to find IPs of printers already installed on this Windows machine."""
        try:
            import subprocess
            # Use wmic to get printer port names (often contain IPs)
            cmd = "wmic printer get name,portname"
            out = subprocess.check_output(cmd, shell=True, universal_newlines=True)
            
            import re
            ip_pattern = re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')
            
            found_ips = set()
            for line in out.splitlines():
                matches = ip_pattern.findall(line)
                for ip in matches:
                    if ip != "127.0.0.1" and not (ip.endswith(".1") or ip.endswith(".254")):
                        found_ips.add(ip)
            
            for ip in found_ips:
                if self.combo_network_printers.findText(ip) < 0:
                    self.combo_network_printers.addItem(ip)
        except:
            pass

    # עדכון הממשק (הצגת/הסתרת שדות) כאשר המשתמש בוחר סוג מדפסת
    def _on_radio_changed(self):
        win_mode = self.radio_windows.isChecked()
        self.combo_printers.setEnabled(win_mode)
        self.combo_network_printers.setEnabled(not win_mode)
        self.btn_scan.setEnabled(not win_mode)
        if hasattr(self, 'input_manual_ip'):
            self.input_manual_ip.setEnabled(not win_mode)

    # הפעלת תהליך סריקת הרשת למציאת מדפסות חדשות
    def _scan_network(self):
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("Scanning...")
        self.combo_network_printers.clear()
        
        t = PrinterScanThread()
        t.found_printer.connect(lambda ip: self.combo_network_printers.addItem(ip))
        t.finished.connect(self._on_scan_finished)
        self._threads.append(t)
        t.start()

    def _on_scan_finished(self):
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("Scan Network")
        if self.combo_network_printers.count() == 0:
            QMessageBox.information(self, "Scan Result", "No network printers found.")
        else:
            self.combo_network_printers.showPopup()

    # ── PDF rendering ────────────────────────────────────────────────────────

    # הפיכת דפי ה-PDF לתמונות והצגתם בחלון התצוגה המקדימה
    def _render_pages(self):
        try:
            import fitz
            fitz.TOOLS.mupdf_display_errors(False)  # השתקת אזהרות MuPDF הלא-מזיקות
        except ImportError:
            self.label_pages.setText("⚠  Install PyMuPDF for preview: pip install PyMuPDF")
            return

        layout = self.scroll_content.layout()
        doc  = fitz.open(stream=self.pdf_bytes, filetype="pdf")
        zoom = DPI_PREVIEW / 72.0
        mat  = fitz.Matrix(zoom, zoom)
        n    = len(doc)
        self.label_pages.setText(f"{n} page(s)  ·  PDF preview")

        for page in doc:
            pix   = page.get_pixmap(matrix=mat, alpha=False)
            img   = QImage(pix.samples, pix.width, pix.height,
                           pix.stride, QImage.Format_RGB888)
            qpix  = QPixmap.fromImage(img)

            frame = QFrame()
            from PyQt5.QtWidgets import QVBoxLayout as _VBox
            fl = _VBox(frame)
            fl.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel()
            lbl.setPixmap(qpix.scaledToWidth(
                min(qpix.width(), 640), Qt.SmoothTransformation))
            lbl.setAlignment(Qt.AlignCenter)
            fl.addWidget(lbl)

            # insert before the spacer
            layout.insertWidget(layout.count() - 1, frame,
                                alignment=Qt.AlignHCenter)
        doc.close()


    # הפונקציה המרכזית שמתחילה את תהליך ההדפסה לפי הבחירה
    def _do_print(self):
        self.btn_print.setEnabled(False)

        if self.radio_windows.isChecked():
            printer_name = self.combo_printers.currentText()
            if not printer_name:
                QMessageBox.warning(self, "No Printer",
                                    "No Windows printer is available on this machine.")
                self.btn_print.setEnabled(True)
                return
            self._print_windows(printer_name)
        else:
            # 1. Try combo first
            ip = self.combo_network_printers.currentText().strip()
            # 2. Try manual entry if combo is empty
            if not ip:
                ip = self.input_manual_ip.text().strip()
            
            if not ip:
                QMessageBox.warning(self, "No Printer",
                                    "Please scan and select a network printer or enter IP manually.")
                self.btn_print.setEnabled(True)
                return
            self._print_socket(ip, 9100)

    # ── Windows printer ───────────────────────────────────────────────────────

    # ביצוע הדפסה דרך הדרייברים של Windows
    def _print_windows(self, printer_name: str):
        self.btn_print.setText("Printing…")
        try:
            # Find the QPrinterInfo for this name
            target = None
            for info in QPrinterInfo.availablePrinters():
                if info.printerName() == printer_name:
                    target = info
                    break

            printer = QPrinter(target, QPrinter.HighResolution) if target \
                      else QPrinter(QPrinter.HighResolution)
            printer.setPrinterName(printer_name)
            printer.setDocName(self.filename)

            self._render_to_printer(printer)
            self._mark_printed()
            QMessageBox.information(self, "Print",
                                    f"Document sent to '{printer_name}'.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Print Error", str(e))
        finally:
            self.btn_print.setEnabled(True)
            self.btn_print.setText("Print")

    def _render_to_printer(self, printer: QPrinter):
        try:
            import fitz
            fitz.TOOLS.mupdf_display_errors(False)  # השתקת אזהרות MuPDF הלא-מזיקות
        except ImportError:
            raise RuntimeError("PyMuPDF required — pip install PyMuPDF")

        doc  = fitz.open(stream=self.pdf_bytes, filetype="pdf")
        zoom = DPI_PRINT / 72.0
        mat  = fitz.Matrix(zoom, zoom)

        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError("Failed to start painter on printer device.")

        for i, page in enumerate(doc):
            if i > 0:
                printer.newPage()
            pix   = page.get_pixmap(matrix=mat, alpha=False)
            img   = QImage(pix.samples, pix.width, pix.height,
                           pix.stride, QImage.Format_RGB888)
            qpix  = QPixmap.fromImage(img)
            rect  = painter.viewport()
            scaled = qpix.scaled(rect.size(), Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
            x = (rect.width()  - scaled.width())  // 2
            y = (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)

        painter.end()
        doc.close()


    # ביצוע הדפסה ישירה למדפסת רשת (TCP)
    def _print_socket(self, host: str, port: int):
        self.btn_print.setText(f"Connecting to {host}:{port}…")

        t = SocketPrintThread(host, port, self.pdf_bytes)
        t.progress.connect(self._on_socket_progress)
        t.success.connect(self._on_socket_success)
        t.error.connect(self._on_socket_error)
        self._threads.append(t)
        t.start()

    def _on_socket_progress(self, sent: int, total: int):
        pct = int(sent / total * 100)
        self.btn_print.setText(f"Sending… {pct}%")

    def _on_socket_success(self):
        self.btn_print.setEnabled(True)
        self.btn_print.setText("Print")
        self._mark_printed()
        QMessageBox.information(self, "Print",
                                "Document sent to printer via network socket.")
        self.accept()

    def _on_socket_error(self, msg: str):
        self.btn_print.setEnabled(True)
        self.btn_print.setText("Print")
        QMessageBox.critical(self, "Socket Error",
                             f"Could not connect to printer:\n{msg}")

    # ── status update ─────────────────────────────────────────────────────────

    # עדכון שרת הענן שההדפסה הסתיימה בהצלחה
    def _mark_printed(self):
        if self.job_id is None:
            return
        try:
            send_request("update_print_status",
                         {"job_id": self.job_id, "status": "printed"},
                         timeout=5)
        except Exception:
            pass
