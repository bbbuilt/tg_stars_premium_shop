"""SQLite helpers for one simple Stars order table."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import aiosqlite
from loguru import logger


class Database:
    """Tiny async wrapper around SQLite.

    The table keeps a couple of legacy-compatible columns because many users
    run demos on top of an old local `stars_bot.db`.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        """Create the orders table and add columns needed by the demo."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    recipient_username TEXT,
                    stars INTEGER NOT NULL,
                    price_ton REAL NOT NULL DEFAULT 0,
                    price_usd REAL NOT NULL DEFAULT 0,
                    commission_ton REAL NOT NULL DEFAULT 0,
                    total_ton REAL NOT NULL DEFAULT 0,
                    total_rub REAL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payment_type TEXT NOT NULL DEFAULT 'ton',
                    payment_address TEXT,
                    payment_comment TEXT,
                    tx_hash TEXT,
                    freekassa_payment_id TEXT,
                    freekassa_payment_url TEXT,
                    fragment_tx_hash TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT NOT NULL,
                    paid_at TEXT,
                    completed_at TEXT
                )
                """
            )
            await self._ensure_columns(
                db,
                {
                    "recipient_username": "TEXT",
                    "price_ton": "REAL NOT NULL DEFAULT 0",
                    "price_usd": "REAL NOT NULL DEFAULT 0",
                    "commission_ton": "REAL NOT NULL DEFAULT 0",
                    "total_ton": "REAL NOT NULL DEFAULT 0",
                    "total_rub": "REAL",
                    "payment_type": "TEXT NOT NULL DEFAULT 'ton'",
                    "payment_address": "TEXT",
                    "payment_comment": "TEXT",
                    "tx_hash": "TEXT",
                    "freekassa_payment_id": "TEXT",
                    "freekassa_payment_url": "TEXT",
                    "fragment_tx_hash": "TEXT",
                    "error_message": "TEXT",
                    "paid_at": "TEXT",
                    "completed_at": "TEXT",
                },
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            await db.commit()
        logger.info("SQLite готова: {}", self.db_path)

    async def _ensure_columns(self, db: aiosqlite.Connection, columns: Dict[str, str]) -> None:
        cursor = await db.execute("PRAGMA table_info(orders)")
        existing = {row[1] for row in await cursor.fetchall()}
        for name, sql_type in columns.items():
            if name not in existing:
                await db.execute(f"ALTER TABLE orders ADD COLUMN {name} {sql_type}")
                logger.info("Добавлена колонка orders.{}", name)

    async def create_order(
        self,
        *,
        user_id: int,
        username: Optional[str],
        recipient_username: str,
        stars: int,
        price_usd: float,
        total_ton: float,
        total_rub: Optional[float],
        payment_type: str,
        payment_address: str,
        payment_comment: str,
        expires_at: datetime,
    ) -> int:
        """Insert a new order and return its numeric id."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO orders (
                    user_id, username, recipient_username, stars, price_ton,
                    price_usd, commission_ton, total_ton, total_rub, status,
                    payment_type, payment_address, payment_comment, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    recipient_username,
                    stars,
                    total_ton,
                    price_usd,
                    0.0,
                    total_ton,
                    total_rub,
                    payment_type,
                    payment_address,
                    payment_comment,
                    expires_at.isoformat(timespec="seconds"),
                ),
            )
            await db.commit()
            order_id = int(cursor.lastrowid)
        logger.info("Создан заказ #{}: {} Stars для {}", order_id, stars, recipient_username)
        return order_id

    async def update_order(self, order_id: int, **fields: Any) -> None:
        """Update selected fields on an order."""
        allowed = {
            "status",
            "payment_comment",
            "tx_hash",
            "freekassa_payment_id",
            "freekassa_payment_url",
            "fragment_tx_hash",
            "error_message",
            "paid_at",
            "completed_at",
            "total_rub",
            "total_ton",
        }
        updates = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Поле orders.{key} не разрешено для update_order")
            updates.append(f"{key} = ?")
            params.append(value)
        if not updates:
            return

        params.append(order_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE orders SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()

    async def set_status(self, order_id: int, status: str, **fields: Any) -> None:
        """Set status and optional timestamps/details."""
        now = datetime.now().isoformat(timespec="seconds")
        payload = {"status": status, **fields}
        if status == "paid":
            payload.setdefault("paid_at", now)
        if status == "completed":
            payload.setdefault("completed_at", now)
        await self.update_order(order_id, **payload)
        logger.info("Заказ #{} -> {}", order_id, status)

    async def acquire_paid_order(self, order_id: int) -> bool:
        """Atomically switch paid order to processing before Fragment purchase."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE orders SET status = 'processing' WHERE id = ? AND status = 'paid'",
                (order_id,),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Return a single order as dict."""
        rows = await self._fetch("SELECT * FROM orders WHERE id = ?", (order_id,))
        return rows[0] if rows else None

    async def get_user_orders(self, user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """Return latest orders for a user."""
        return await self._fetch(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )

    async def get_pending_orders(self) -> List[Dict[str, Any]]:
        """Return orders waiting for user payment."""
        return await self._fetch("SELECT * FROM orders WHERE status = 'pending' ORDER BY id")

    async def get_paid_orders(self) -> List[Dict[str, Any]]:
        """Return paid orders that still need Fragment fulfillment."""
        return await self._fetch("SELECT * FROM orders WHERE status = 'paid' ORDER BY id")

    async def _fetch(self, query: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
