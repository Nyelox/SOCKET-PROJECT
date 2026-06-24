import sys
from PyQt5.QtWidgets import QApplication
from Client.Login import Login
from Client.client_config import send_request
from Client import Client_crypto as crypto_utils


def main():
    # שלב 1: בקשת המפתח הציבורי RSA מהשרת (דרך סוקט)
    resp = send_request("public_key", {}, timeout=5)
    pem = resp["public_key"]
    public_key = crypto_utils.import_public_key(pem)

    # שלב 2: יצירת מפתח AES, הצפנתו עם RSA, ושליחתו לשרת
    aes_key = crypto_utils.generate_aes_key()
    encrypted_key = crypto_utils.rsa_encrypt(public_key, aes_key)

    resp2 = send_request("session_key", {"encrypted_key": encrypted_key}, timeout=5)
    session_token = resp2["session_token"]

    crypto_utils.AES_KEY = aes_key
    crypto_utils.SESSION_TOKEN = session_token
    crypto_utils.SERVER_PUBLIC_KEY = public_key
    print("Encrypted session established with server")


    app = QApplication(sys.argv)
    login_window = Login()
    login_window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
