"""
admin_window.py  ←  loads admin_window.ui
Admin Dashboard: manage users (block/unblock) and view user activity history.
"""
import os
from Client.client_config import send_request
from PyQt5.QtWidgets import QMainWindow, QTableWidgetItem, QMessageBox, QHeaderView
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.uic import loadUi


class AdminWindow(QMainWindow):
    closed = pyqtSignal()

    def __init__(self, current_user, server_url):
        super().__init__()
        self.current_user = current_user
        self.server_url = server_url

        # Load the .ui file
        ui_path = os.path.join(os.path.dirname(__file__), "admin_window.ui")
        loadUi(ui_path, self)

        # Stretch columns
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # Connect signals
        self.btn_back.clicked.connect(self.close)
        self.btn_block.clicked.connect(self.block_user)
        self.btn_unblock.clicked.connect(self.unblock_user)
        self.btn_refresh_users.clicked.connect(self.load_users)
        self.btn_refresh_history.clicked.connect(self.load_history)
        self.combo_users.currentIndexChanged.connect(self.load_history)

        # Initial data load
        self.combo_users.addItem("All Users")
        self.load_users()
        self.load_history()

    # ── Users ────────────────────────────────────────────────────────────────

    def load_users(self):
        try:
            data = send_request("admin/users", {"admin_user": self.current_user})

            if data.get("status") == "OK":
                users = data.get("users", [])
                self.users_table.setRowCount(0)

                # Keep combo selection while refreshing
                current_combo_text = self.combo_users.currentText()
                self.combo_users.blockSignals(True)
                self.combo_users.clear()
                self.combo_users.addItem("All Users")

                for row_idx, user in enumerate(users):
                    self.users_table.insertRow(row_idx)
                    self.users_table.setItem(row_idx, 0, QTableWidgetItem(user["username"]))

                    blocked_item = QTableWidgetItem("Yes" if user["is_blocked"] else "No")
                    if user["is_blocked"]:
                        blocked_item.setBackground(Qt.red)
                        blocked_item.setForeground(Qt.white)
                    self.users_table.setItem(row_idx, 1, blocked_item)

                    admin_item = QTableWidgetItem("Yes" if user["is_admin"] else "No")
                    if user["is_admin"]:
                        admin_item.setForeground(Qt.blue)
                    self.users_table.setItem(row_idx, 2, admin_item)

                    self.combo_users.addItem(user["username"])

                # Restore combo selection if still present
                index = self.combo_users.findText(current_combo_text)
                self.combo_users.setCurrentIndex(index if index >= 0 else 0)
                self.combo_users.blockSignals(False)

            else:
                QMessageBox.warning(self, "Error", f"Failed to load users: {data.get('status')}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Connection error: {e}")

    # ── History ──────────────────────────────────────────────────────────────

    def load_history(self):
        target = self.combo_users.currentText()
        target_user = None if target == "All Users" else target

        try:
            data = send_request("admin/history",
                                 {"admin_user": self.current_user, "target_user": target_user})

            if data.get("status") == "OK":
                history = data.get("history", [])
                self.history_table.setRowCount(0)
                for row_idx, h in enumerate(history):
                    self.history_table.insertRow(row_idx)
                    self.history_table.setItem(row_idx, 0, QTableWidgetItem(str(h["timestamp"])))
                    self.history_table.setItem(row_idx, 1, QTableWidgetItem(h["username"]))
                    self.history_table.setItem(row_idx, 2, QTableWidgetItem(h["action"]))
                    self.history_table.setItem(row_idx, 3, QTableWidgetItem(h["details"]))
            else:
                QMessageBox.warning(self, "Error", f"Failed to load history: {data.get('status')}")

        except Exception as e:
            print(f"History load error: {e}")

    # ── Block / Unblock ──────────────────────────────────────────────────────

    def block_user(self):
        self._set_block_status(True)

    def unblock_user(self):
        self._set_block_status(False)

    def _set_block_status(self, block: bool):
        row = self.users_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Info", "Please select a user first.")
            return

        username = self.users_table.item(row, 0).text()

        try:
            data = send_request("admin/block_user", {
                "admin_user": self.current_user,
                "target_user": username,
                "block": block,
            })

            if data.get("status") == "OK":
                action = "blocked" if block else "unblocked"
                QMessageBox.information(self, "Success", f"User '{username}' {action} successfully.")
                self.load_users()
            else:
                QMessageBox.warning(self, "Error", data.get("status"))

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
