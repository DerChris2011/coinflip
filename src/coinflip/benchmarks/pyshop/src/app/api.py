"""HTTP handlers. Each handler receives the parsed request and returns (status, body)."""

import logging

from app import auth, invoices, orders, users

log = logging.getLogger(__name__)


def _current_user(request: dict) -> dict:
    header = request["headers"].get("Authorization", "")
    if not header.startswith("Bearer "):
        raise auth.AuthError("missing token")
    return auth.read_token(header.removeprefix("Bearer "))


def handle(handler, request: dict) -> tuple[int, dict]:
    try:
        return handler(request)
    except auth.AuthError:
        return 401, {"error": "unauthorized"}
    except (orders.Forbidden, users.LoginBlocked):
        return 403, {"error": "forbidden"}
    except (KeyError, invoices.NotFound):
        return 404, {"error": "not found"}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception:
        log.exception("unhandled error")
        return 500, {"error": "internal server error"}


def post_login(request: dict) -> tuple[int, dict]:
    body = request["json"]
    return 200, {"token": users.login(body["email"], body["password"])}


def get_me(request: dict) -> tuple[int, dict]:
    user = _current_user(request)
    return 200, users.profile(user["sub"])


def get_user_search(request: dict) -> tuple[int, dict]:
    _current_user(request)
    term = request["query"].get("q", "")
    if len(term) < 2:
        raise ValueError("search term too short")
    return 200, {"results": users.search(term, limit=20)}


def get_orders(request: dict) -> tuple[int, dict]:
    user = _current_user(request)
    page = int(request["query"].get("page", 1))
    return 200, {"orders": orders.list_orders(user["sub"], page)}


def post_cancel_order(request: dict) -> tuple[int, dict]:
    user = _current_user(request)
    orders.cancel_order(int(request["path"]["order_id"]), user)
    return 200, {"status": "cancelled"}


def get_invoice(request: dict) -> tuple[int, dict]:
    user = _current_user(request)
    data = invoices.read_invoice(user["sub"], request["path"]["filename"])
    return 200, {"pdf": data.hex()}


def delete_user(request: dict) -> tuple[int, dict]:
    user = _current_user(request)
    if user["role"] != "admin":
        raise orders.Forbidden("admins only")
    target = int(request["path"]["user_id"])
    users.deactivate(target)
    return 200, {"deactivated": target}
