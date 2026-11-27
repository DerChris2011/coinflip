"""Application settings, read from the environment."""

import os

SECRET_KEY = os.environ["SHOP_SECRET_KEY"]
DATABASE_PATH = os.environ.get("SHOP_DATABASE", "shop.db")
INVOICE_DIR = os.environ.get("SHOP_INVOICE_DIR", "/var/lib/shop/invoices")

TOKEN_TTL_SECONDS = 3600
PASSWORD_ITERATIONS = 310_000
MAX_LOGIN_ATTEMPTS = 5
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
