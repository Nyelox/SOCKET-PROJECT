import os
import threading
import queue
from PyQt5 import QtWidgets
from PyQt5.uic import loadUi
from PyQt5.QtWidgets import QMainWindow, QLabel, QMessageBox
from PyQt5.QtCore import Qt, QObject, pyqtSignal
from Client.client_config import SERVER_URL, send_request
from Client import Client_crypto as crypto_utils


class WorkerThread(threading.Thread):
    def __init__(self, task_queue, signals):
        super().__init__(daemon=True)
        self.task_queue = task_queue
        self.signals = signals

    def run(self):
        while True:
            try:
                task_name, data = self.task_queue.get()
                if task_name == "login":
                    self.handle_login(data)
            except Exception as e:
                print(f"Error in worker: {e}")

    def handle_login(self, data):
        username, password = data
        try:
            # הצפנת הנתונים עם AES לפני שליחה
            encrypted = crypto_utils.aes_encrypt({"username": username, "password": password})
            response = send_request("login",
                                    {"session_token": crypto_utils.SESSION_TOKEN,
                                     "encrypted_data": encrypted},
                                    timeout=30)

            if response.get("status") == "Login successful":
                self.signals.success.emit(username, response.get("is_admin", False))
            else:
                self.signals.error.emit(response.get("status", "Login failed"))

        except Exception as e:
            self.signals.error.emit(f"Error: {str(e)}")


class WorkerSignals(QObject):
    success = pyqtSignal(str, bool)
    error = pyqtSignal(str)


class Login(QMainWindow):
    def __init__(self):
        super(Login, self).__init__()
        ui_path = os.path.join(os.path.dirname(__file__), "login.ui")
        loadUi(ui_path, self)

        self.current_user = None

        self.task_queue = queue.Queue()

        self.signals = WorkerSignals()
        self.signals.success.connect(self.on_login_success)
        self.signals.error.connect(self.on_login_failed)

        self.worker = WorkerThread(self.task_queue, self.signals)
        self.worker.start()

        self.pushButton_login.clicked.connect(self.login_function)
        self.lineEdit_password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.checkBox_showPassword.toggled.connect(self.toggle_password)

        self.label_signup = QLabel('<a href="#" style="text-decoration: none; color: #0066cc;">Register</a>', self)
        self.label_signup.setTextFormat(Qt.RichText)
        self.label_signup.linkActivated.connect(self.open_signup)
        self.label_signup.setGeometry(225, 211, 141, 16)

        self.home_window = None

    def toggle_password(self, checked):
        mode = QtWidgets.QLineEdit.Normal if checked else QtWidgets.QLineEdit.Password
        self.lineEdit_password.setEchoMode(mode)

    def login_function(self):
        username = self.lineEdit_userName.text().strip()
        password = self.lineEdit_password.text()

        if not username or not password:
            self.show_message("All fields are required", QMessageBox.Warning)
            return

        self.task_queue.put(("login", (username, password)))

    def on_login_success(self, username, is_admin):
        self.current_user = username
        self.show_message(f"Welcome, {username}!", QMessageBox.Information)
        self.open_home_window(username, is_admin)

    def open_home_window(self, username, is_admin=False):
        from Client.home import Home

        # מביא את רשימת המשתמשים מהשרת
        try:
            data = send_request("all_users", {}, timeout=5)
            if data.get("status") == "OK":
                users_list = data.get("users", [])
            else:
                users_list = [username]
        except Exception:
            users_list = [username]

        self.home_window = Home(
            current_user=username,
            users_list=users_list,
            server_url=SERVER_URL,
            is_admin=is_admin
        )
        self.home_window.show()
        self.close()

    def on_login_failed(self, message):
        self.show_message(message, QMessageBox.Critical)

    def show_message(self, message, icon=QMessageBox.Information):
        msg = QMessageBox(self)
        msg.setIcon(icon)
        msg.setText(message)
        msg.setWindowTitle("Login")
        msg.exec_()

    def open_signup(self):
        from signup import Signup
        self.signup_window = Signup()
        self.signup_window.show()
        self.close()