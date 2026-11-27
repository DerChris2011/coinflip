"""Payment provider integration (charges and refunds)."""

from decimal import Decimal

from app import db


class PaymentError(Exception):
    pass


class _Provider:
    """Stand-in for the external payment provider SDK."""

    def charge(self, reference: str, amount_cents: int, card_token: str) -> str:
        raise NotImplementedError

    def refund(self, charge_id: str, amount_cents: int) -> None:
        raise NotImplementedError


provider = _Provider()


def _cents(amount: Decimal) -> int:
    return int(amount * 100)


def charge(order_id: int, amount: Decimal, card_token: str) -> None:
    if amount <= 0:
        raise PaymentError("amount must be positive")
    charge_id = provider.charge(f"order-{order_id}", _cents(amount), card_token)
    db.execute(
        "INSERT INTO payments (order_id, charge_id, amount, refunded) VALUES (?, ?, ?, '0.00')",
        (order_id, charge_id, str(amount)),
    )


def refund(order_id: int, amount: Decimal) -> None:
    payment = db.fetch_one("SELECT * FROM payments WHERE order_id = ?", (order_id,))
    if payment is None:
        raise PaymentError("order was never paid")
    already = Decimal(payment["refunded"])
    if amount <= 0 or already + amount > Decimal(payment["amount"]):
        raise PaymentError("refund exceeds the paid amount")
    provider.refund(payment["charge_id"], _cents(amount))
    db.execute(
        "UPDATE payments SET refunded = ? WHERE order_id = ?",
        (str(already + amount), order_id),
    )
