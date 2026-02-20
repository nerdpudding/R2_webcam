"""Encryption and decryption for NerdCam config storage.

XOR stream cipher with PBKDF2 key derivation. Config is stored as
base64(salt + ciphertext) in config.enc.
"""

import base64
import hashlib
import json
import os


def _derive_key(master: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from master password using PBKDF2."""
    return hashlib.pbkdf2_hmac("sha256", master.encode(), salt, 100_000)


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    """XOR data with repeating key."""
    return bytes(d ^ key[i % len(key)] for i, d in enumerate(data))


def encrypt_config(config: dict, master: str, config_path: str) -> None:
    """Encrypt config dict and save to config.enc."""
    salt = os.urandom(16)
    key = _derive_key(master, salt)
    plaintext = json.dumps(config, indent=4).encode()
    ciphertext = _xor_bytes(plaintext, key)
    payload = base64.b64encode(salt + ciphertext).decode()
    with open(config_path, "w") as f:
        f.write(payload)
    os.chmod(config_path, 0o600)


def decrypt_config(master: str, config_path: str) -> dict:
    """Decrypt config.enc and return config dict, or None on failure."""
    with open(config_path) as f:
        payload = f.read()
    raw = base64.b64decode(payload)
    salt = raw[:16]
    ciphertext = raw[16:]
    key = _derive_key(master, salt)
    plaintext = _xor_bytes(ciphertext, key)
    try:
        return json.loads(plaintext.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
