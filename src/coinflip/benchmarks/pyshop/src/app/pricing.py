"""Cart totals, discounts and taxes. All money is handled as Decimal."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
TAX_RATE = Decimal("0.19")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def line_total(unit_price: Decimal, quantity: int) -> Decimal:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return money(unit_price * quantity)


def apply_discount(subtotal: Decimal, percent: int) -> Decimal:
    if not 0 <= percent <= 100:
        raise ValueError("discount must be between 0 and 100 percent")
    return money(subtotal * (Decimal(100 - percent) / 100))


def cart_total(items: list[dict], discount_percent: int = 0) -> dict:
    subtotal = sum((line_total(i["unit_price"], i["quantity"]) for i in items), Decimal("0"))
    discounted = apply_discount(subtotal, discount_percent)
    tax = money(discounted * TAX_RATE)
    return {"subtotal": subtotal, "discounted": discounted, "tax": tax, "total": discounted + tax}


def add_item(item: dict, cart: list[dict] | None = None) -> list[dict]:
    cart = [] if cart is None else cart
    for existing in cart:
        if existing["sku"] == item["sku"]:
            existing["quantity"] += item["quantity"]
            return cart
    cart.append(dict(item))
    return cart
