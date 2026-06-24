import os
import json
import base64
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# מפתחות הצפנה ששמורים ב-RAM (נקבעים בעלייה של הלקוח ב-main.py)
AES_KEY = None             # מפתח AES של session (להודעות)
SESSION_TOKEN = None       # זיהוי session מול השרת
SERVER_PUBLIC_KEY = None   # מפתח RSA הציבורי של השרת (להצפנת מפתחות AES של קבצים)


def aes_encrypt(payload_dict):
    """מצפין dict עם AES-GCM באמצעות המפתח שב-RAM"""
    plaintext = json.dumps(payload_dict).encode()
    nonce = os.urandom(12)
    aesgcm = AESGCM(AES_KEY)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ciphertext).decode()


def import_public_key(pem_str):
    """מקבל טקסט PEM ומחזיר אובייקט מפתח ציבורי"""
    return serialization.load_pem_public_key(pem_str.encode())


def generate_aes_key():
    """יוצר מפתח AES-256 אקראי (32 בתים)"""
    return os.urandom(32)


def rsa_encrypt(public_key, data_bytes):
    """מצפין נתונים עם מפתח ציבורי RSA, מחזיר base64"""
    encrypted = public_key.encrypt(
        data_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return base64.b64encode(encrypted).decode()


def aes_encrypt_bytes(aes_key, data_bytes):
    """מצפין bytes גולמיים (קובץ) עם AES-GCM. מחזיר bytes מוצפנים."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, data_bytes, None)
    return nonce + ciphertext


def aes_decrypt_bytes(aes_key, encrypted_bytes):
    """מפענח bytes שהוצפנו עם AES-GCM. מחזיר bytes גולמיים."""
    nonce = encrypted_bytes[:12]
    ciphertext = encrypted_bytes[12:]
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(nonce, ciphertext, None)
