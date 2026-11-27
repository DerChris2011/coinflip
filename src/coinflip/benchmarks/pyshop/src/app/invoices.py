"""Invoice PDFs stored on disk."""

from pathlib import Path

from app import config, db


class NotFound(Exception):
    pass


def invoice_path(user_id: int, filename: str) -> Path:
    base = (Path(config.INVOICE_DIR) / str(user_id)).resolve()
    path = (base / filename).resolve()
    if not path.is_relative_to(base):
        raise NotFound(filename)
    return path


def read_invoice(user_id: int, filename: str) -> bytes:
    path = invoice_path(user_id, filename)
    if not path.is_file():
        raise NotFound(filename)
    with path.open("rb") as fh:
        return fh.read()


def list_invoices(user_id: int) -> list[str]:
    rows = db.fetch_all("SELECT filename FROM invoices WHERE user_id = ? ORDER BY created_at", (user_id,))
    return [row["filename"] for row in rows]
