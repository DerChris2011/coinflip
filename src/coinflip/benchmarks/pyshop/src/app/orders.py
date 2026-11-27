"""Order lifecycle: create, list, cancel and pay."""

import logging

from app import config, db, payments, pricing

log = logging.getLogger(__name__)


class Forbidden(Exception):
    pass


class InvalidState(Exception):
    pass


def create_order(user_id: int, items: list[dict], discount_percent: int = 0) -> int:
    totals = pricing.cart_total(items, discount_percent)
    order_id = db.execute(
        "INSERT INTO orders (user_id, status, total) VALUES (?, 'open', ?)",
        (user_id, str(totals["total"])),
    )
    for item in items:
        db.execute(
            "INSERT INTO order_items (order_id, sku, quantity, unit_price) VALUES (?, ?, ?, ?)",
            (order_id, item["sku"], item["quantity"], str(item["unit_price"])),
        )
    return order_id


def list_orders(user_id: int, page: int = 1, page_size: int = config.DEFAULT_PAGE_SIZE) -> list[dict]:
    if page < 1:
        raise ValueError("page starts at 1")
    page_size = min(page_size, config.MAX_PAGE_SIZE)
    offset = (page - 1) * page_size
    return db.fetch_all(
        "SELECT id, status, total, created_at FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (user_id, page_size, offset),
    )


def _load(order_id: int) -> dict:
    order = db.fetch_one("SELECT * FROM orders WHERE id = ?", (order_id,))
    if order is None:
        raise KeyError(order_id)
    return order


def cancel_order(order_id: int, user: dict) -> None:
    order = _load(order_id)
    if order["user_id"] != user["sub"] and user["role"] != "admin":
        raise Forbidden("not your order")
    if order["status"] not in ("open", "paid"):
        raise InvalidState(f"cannot cancel an order that is {order['status']}")
    if order["status"] == "paid":
        payments.refund(order_id, pricing.money(order["total"]))
    db.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))


def pay_order(order_id: int, user: dict, card_token: str) -> str:
    order = _load(order_id)
    if order["user_id"] != user["sub"]:
        raise Forbidden("not your order")
    if order["status"] != "open":
        raise InvalidState("order is not open")
    try:
        payments.charge(order_id, pricing.money(order["total"]), card_token)
    except payments.PaymentError:
        log.warning("payment failed for order %s", order_id)
        db.execute("UPDATE orders SET status = 'payment_failed' WHERE id = ?", (order_id,))
        raise
    db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
    return "paid"
