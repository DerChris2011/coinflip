"""User accounts: registration, login, profiles and search."""

from app import auth, config, db


class LoginBlocked(Exception):
    pass


PUBLIC_FIELDS = ("id", "email", "display_name", "role", "created_at")


def register(email: str, password: str, display_name: str) -> int:
    if len(password) < 12:
        raise ValueError("password must have at least 12 characters")
    return db.execute(
        "INSERT INTO users (email, password_hash, display_name, role) VALUES (?, ?, ?, 'customer')",
        (email.lower(), auth.hash_password(password), display_name),
    )


def login(email: str, password: str) -> str:
    user = db.fetch_one("SELECT * FROM users WHERE email = ?", (email.lower(),))
    if user is None:
        raise auth.AuthError("unknown user or wrong password")
    if user["failed_logins"] >= config.MAX_LOGIN_ATTEMPTS:
        raise LoginBlocked("too many failed attempts, reset your password")
    if not auth.verify_password(password, user["password_hash"]):
        db.execute("UPDATE users SET failed_logins = failed_logins + 1 WHERE id = ?", (user["id"],))
        raise auth.AuthError("unknown user or wrong password")
    db.execute("UPDATE users SET failed_logins = 0 WHERE id = ?", (user["id"],))
    return auth.create_token(user["id"], user["role"])


def profile(user_id: int) -> dict:
    user = db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        raise KeyError(user_id)
    return {field: user[field] for field in PUBLIC_FIELDS}


def search(term: str, limit: int) -> list[dict]:
    rows = db.fetch_all(
        "SELECT id, display_name FROM users WHERE display_name LIKE ? ORDER BY display_name LIMIT ?",
        (f"%{term}%", limit),
    )
    return rows


def deactivate(user_id: int) -> None:
    profile(user_id)  # raises KeyError for unknown users
    db.execute("UPDATE users SET active = 0, reset_code = NULL WHERE id = ?", (user_id,))


def start_password_reset(email: str) -> str | None:
    user = db.fetch_one("SELECT id FROM users WHERE email = ?", (email.lower(),))
    if user is None:
        return None
    code = auth.new_reset_code()
    db.execute("UPDATE users SET reset_code = ? WHERE id = ?", (code, user["id"]))
    return code
