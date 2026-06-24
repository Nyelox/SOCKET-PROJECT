import json
import base64
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_rsa_keys():
    """יוצר זוג מפתחות RSA-2048 (פרטי + ציבורי)"""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048
    )
    public_key = private_key.public_key()
    return private_key, public_key


def export_public_key(public_key):
    """ממיר את המפתח הציבורי לטקסט PEM כדי לשלוח אותו ברשת"""
    pem_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem_bytes.decode()


def rsa_decrypt(private_key, encrypted_b64):
    """מפענח טקסט שהוצפן עם המפתח הציבורי RSA"""
    encrypted_bytes = base64.b64decode(encrypted_b64)
    decrypted = private_key.decrypt(
        encrypted_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return decrypted


def aes_decrypt(aes_key, encrypted_b64):
    """מפענח טקסט שהוצפן עם AES-GCM, מחזיר dict"""
    data = base64.b64decode(encrypted_b64)
    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(aes_key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode())
