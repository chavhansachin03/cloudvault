"""
encryption.py
=============
All encryption/decryption happens HERE, on the app server that the user
runs on their own machine (their "client"), BEFORE any bytes are written
to the storage backend (the "storage/" folder in this demo, which stands
in for a real cloud bucket like S3 / Google Cloud Storage / Azure Blob).

This is what "client-side encryption" means in this project:
the cloud/storage layer only ever sees ciphertext. Even if someone
got direct access to the storage/ directory, they could not read any
file content without the user's password.

Algorithm: Fernet (AES-128 in CBC mode + HMAC-SHA256 for authenticity),
with the key derived from the user's password via PBKDF2-HMAC-SHA256
(390,000 iterations, per-user random salt stored in the database).
"""

import base64
import os
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 390_000
SALT_SIZE = 16  # bytes


def generate_salt() -> bytes:
    """Generate a fresh random salt for a new user."""
    return os.urandom(SALT_SIZE)


def derive_key(password: str, salt: bytes) -> bytes:
    """
    Derive a Fernet-compatible 32-byte key from the user's password + salt.
    The same password + salt will always produce the same key, but the key
    itself is never stored anywhere - it only ever lives in server memory
    for the duration of a login session.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    raw_key = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(raw_key)


def encrypt_bytes(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt raw file bytes before they are written to storage."""
    f = Fernet(key)
    return f.encrypt(plaintext)


def decrypt_bytes(key: bytes, ciphertext: bytes) -> bytes:
    """
    Decrypt bytes read from storage back into the original file.
    Raises InvalidToken if the key is wrong or the data was tampered with.
    """
    f = Fernet(key)
    return f.decrypt(ciphertext)


__all__ = [
    "generate_salt",
    "derive_key",
    "encrypt_bytes",
    "decrypt_bytes",
    "InvalidToken",
]
