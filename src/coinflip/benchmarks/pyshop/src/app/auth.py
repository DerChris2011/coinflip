"""Password hashing and signed session tokens."""

import base64
import hashlib
import hmac
import json
import secrets
import time

from app import config


class AuthError(Exception):
    pass


class TokenExpired(AuthError):
    pass


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, config.PASSWORD_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, _ = stored.split("$", 1)
    candidate = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(candidate, stored)


def _sign(payload: bytes) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def create_token(user_id: int, role: str) -> str:
    claims = {
        "sub": user_id,
        "role": role,
        "exp": int(time.time()) + config.TOKEN_TTL_SECONDS,
        "nonce": secrets.token_hex(8),
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode())
    return f"{payload.decode()}.{_sign(payload)}"


def read_token(token: str) -> dict:
    try:
        payload, signature = token.rsplit(".", 1)
    except ValueError:
        raise AuthError("malformed token") from None
    if not hmac.compare_digest(signature, _sign(payload.encode())):
        raise AuthError("bad signature")
    claims = json.loads(base64.urlsafe_b64decode(payload))
    if claims["exp"] < time.time():
        raise TokenExpired("token expired")
    return claims


def new_reset_code() -> str:
    return secrets.token_urlsafe(24)
