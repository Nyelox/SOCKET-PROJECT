import threading
import queue
import time

from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QMainWindow, QMessageBox, QLabel
from PyQt5.uic import loadUi
from PyQt5.QtCore import Qt, QObject, pyqtSignal
from Client.client_config import send_request
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
                if task_name == "signup":
                    self.handle_signup(data)
            except Exception as e:
                print(f"Error in worker: {e}")

    def handle_signup(self, data):
        username, password = data

        try:
            # הצפנת הנתונים עם AES לפני שליחה
            encrypted = crypto_utils.aes_encrypt({"username": username, "password": password})
            response = send_request("signup",
                                    {"session_token": crypto_utils.SESSION_TOKEN,
                                     "encrypted_data": encrypted},
                                    timeout=30)
            status = response.get("status", "")

            if status == "Sign Up successful":
                self.signals.success.emit(status)
            else:
                self.signals.error.emit(status or "Sign Up failed")

        except Exception as e:
            self.signals.error.emit(f"Error: {str(e)}")


class WorkerSignals(QObject):
    success = pyqtSignal(str)
    error = pyqtSignal(str)


class Signup(QMainWindow):
    def __init__(self):
        super(Signup, self).__init__()
        loadUi('signup.ui', self)

        self.task_queue = queue.Queue()

        self.signals = WorkerSignals()
        self.signals.success.connect(self.on_signup_success)
        self.signals.error.connect(self.show_message)

        self.worker = WorkerThread(self.task_queue, self.signals)
        self.worker.start()

        self.pushButton_signUp.clicked.connect(self.signup_function)
        self.lineEdit_password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.lineEdit_confirmPassword.setEchoMode(QtWidgets.QLineEdit.Password)
        self.checkBox_showPassword.toggled.connect(self.toggle_password)

        self.label_login = QLabel('<a href="#" style="text-decoration: none; color: #0066cc;">Click here!</a>', self)
        self.label_login.setTextFormat(Qt.RichText)
        self.label_login.linkActivated.connect(self.go_to_login)
        self.label_login.setGeometry(193, 223, 141, 16)

    def toggle_password(self, checked):
        mode = QtWidgets.QLineEdit.Normal if checked else QtWidgets.QLineEdit.Password
        self.lineEdit_password.setEchoMode(mode)
        self.lineEdit_confirmPassword.setEchoMode(mode)

    def signup_function(self):
        username = self.lineEdit_userName.text().strip()
        password = self.lineEdit_password.text()
        confirm_password = self.lineEdit_confirmPassword.text()

        if not username or not password or not confirm_password:
            self.show_message("All fields are required")
            return

        if password != confirm_password:
            self.show_message("Passwords do not match")
            return

        self.task_queue.put(("signup", (username, password)))

    def on_signup_success(self, message):
        self.show_message(message)
        self.go_to_login()

    def show_message(self, text):
        QMessageBox.information(self, "Signup", text)

    def go_to_login(self):
            from Login import Login
            self.login_window = Login()
            self.login_window.show()
            self.close()
