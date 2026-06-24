import os
from PyQt5.QtWidgets import QMainWindow, QLabel, QPushButton
from PyQt5.uic import loadUi
from PyQt5.QtCore import Qt
from Client.client_config import SERVER_URL

class Home(QMainWindow):
    def __init__(self, current_user="", users_list=None, server_url=SERVER_URL, is_admin=False):
        super().__init__()
        ui_path = os.path.join(os.path.dirname(__file__), "home.ui")
        loadUi(ui_path, self)

        self.current_user = current_user or ""
        self.users_list = users_list or []
        self.server_url = server_url
        self.is_admin = is_admin

        self.button_sendfile.clicked.connect(self.open_send_file)
        self.button_sendedfile.clicked.connect(self.open_print_window)
        self.button_sendedfile.setText("Print files")

        self.label_logout = QLabel(
            '<a href="#" style="text-decoration: none; color: #0066cc;">Logout</a>',
            self
        )
        self.label_logout.setTextFormat(Qt.RichText)
        self.label_logout.linkActivated.connect(self.go_to_login)
        self.label_logout.setGeometry(340, 10, 47, 16)
        
        # Add Admin Button if user is admin
        if self.is_admin:
            self.btn_admin = QPushButton("Admin Panel", self.centralwidget)
            self.btn_admin.setGeometry(100, 270, 200, 45)
            self.btn_admin.setStyleSheet("""
                QPushButton {
                  background-color: #ff4444;
                  color: white;
                  border-radius: 10px;
                  font-size: 12pt;
                }
                QPushButton:hover {
                  background-color: #ff6666;
                }
            """)
            self.btn_admin.clicked.connect(self.open_admin_window)
            
            # Increase window height slightly to fit the button
            self.resize(400, 350)
            if self.height() < 350: 
               self.resize(400, 350) 
            # Actually default height is 350 in UI, so 270+45 = 315 fits.

        self.sendfile_window = None
        self.login_window = None
        self.admin_window = None

    def open_send_file(self):
        from Client.sendfile_window import SendFileWindow

        users = self.users_list if self.users_list else [self.current_user]

        self.sendfile_window = SendFileWindow(
            current_user=self.current_user,
            users_list=users,
            server_url=self.server_url
        )

        self.sendfile_window.closed.connect(self._return_from_sendfile)
        self.sendfile_window.show()
        self.hide()

    def _return_from_sendfile(self):
        self.show()

    def _return_home(self):
        self.show()

    # פתיחת חלון ההדפסה וניהול הקבצים (לשעבר קבצים שנשלחו)
    def open_print_window(self):
        from Client.printwindow import PrintWindow
        self.print_window = PrintWindow(
            current_user=self.current_user,
            server_url=self.server_url
        )
        self.print_window.closed.connect(self._return_home)
        self.print_window.show()
        self.hide()

    def open_admin_window(self):
        from Client.admin_window import AdminWindow
        self.admin_window = AdminWindow(self.current_user, self.server_url)
        self.admin_window.closed.connect(self._return_home)
        self.admin_window.show()
        self.hide()

    def go_to_login(self):
        from Login import Login
        self.login_window = Login()
        self.login_window.show()
        self.close()
