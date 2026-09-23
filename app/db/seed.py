"""Idempotently load the fictional fixture data (accounts, orders, tickets) and demo users."""
from __future__ import annotations

import json

from app.config import get_settings
from app.db.models import Account, Order, Ticket, User
from app.db.session import session_scope

# Demo identities. In production user_id would come from an auth token (JWT/session);
# here the mapping user -> account plays the role of the authenticated identity.
DEMO_USERS = [
    {"user_id": "user_maya", "display_name": "Maya Hassan", "account_id": "acc_1001"},
    {"user_id": "user_omar", "display_name": "Omar Saleh", "account_id": "acc_1002"},
    {"user_id": "user_nour", "display_name": "Nour Ali", "account_id": "acc_1003"},
    {"user_id": "user_guest", "display_name": "Guest (no account)", "account_id": None},
]


def seed_fixtures() -> dict:
    data_dir = get_settings().data_dir
    accounts = json.loads((data_dir / "accounts.json").read_text(encoding="utf-8"))
    orders = json.loads((data_dir / "orders.json").read_text(encoding="utf-8"))
    tickets = json.loads((data_dir / "support_tickets.json").read_text(encoding="utf-8"))
    counts = {"accounts": 0, "orders": 0, "tickets": 0, "users": 0}
    with session_scope() as s:
        for a in accounts:
            if not s.get(Account, a["account_id"]):
                s.add(Account(**a)); counts["accounts"] += 1
        s.flush()
        for o in orders:
            if not s.get(Order, o["order_id"]):
                s.add(Order(**o)); counts["orders"] += 1
        for t in tickets:
            if not s.get(Ticket, t["ticket_id"]):
                s.add(Ticket(**t)); counts["tickets"] += 1
        for u in DEMO_USERS:
            if not s.get(User, u["user_id"]):
                s.add(User(**u)); counts["users"] += 1
    return counts


if __name__ == "__main__":
    from app.db.session import init_db
    init_db()
    print(seed_fixtures())
