"""app/models/transaction.py — Модель транзакции."""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from app.db import get_db, DB_ENGINE
import json


def _p() -> str:
    return '%s' if DB_ENGINE == 'postgresql' else '?'


class Transaction:
    def __init__(self, row: dict):
        self._r = row

    @property
    def id(self) -> int:          return self._r['id']
    @property
    def user_id(self) -> int:     return self._r['user_id']
    @property
    def type(self) -> str:        return self._r['type']
    @property
    def amount(self) -> float:    return float(self._r['amount'])
    @property
    def status(self) -> str:      return self._r.get('status', 'completed')
    @property
    def meta(self) -> dict:
        raw = self._r.get('meta')
        if isinstance(raw, dict): return raw
        if isinstance(raw, str):
            try: return json.loads(raw)
            except: return {}
        return {}

    @property
    def created_at(self) -> str:
        v = self._r.get('created_at')
        return v.isoformat() if isinstance(v, datetime) else str(v)

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'user_id': self.user_id,
            'type': self.type, 'amount': round(self.amount, 2),
            'status': self.status, 'meta': self.meta, 'created_at': self.created_at,
        }

    # ── CRUD ────────────────────────────────────────────────────────────────
    @staticmethod
    def create(user_id: int, type_: str, amount: float,
               status: str = 'completed', meta: Optional[dict] = None) -> 'Transaction':
        p = _p()
        meta_json = json.dumps(meta or {})
        if DB_ENGINE == 'postgresql':
            with get_db() as db:
                db.execute(
                    f"INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                    f" VALUES ({p},{p},{p},{p},{p}::jsonb, NOW()) RETURNING *",
                    (user_id, type_, amount, status, meta_json)
                )
                row = db.fetchone()
            return Transaction(dict(row))
        else:
            with get_db() as db:
                db.execute(
                    f"INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                    f" VALUES ({p},{p},{p},{p},{p}, datetime('now'))",
                    (user_id, type_, amount, status, meta_json)
                )
            with get_db() as db:
                db.execute(
                    f"SELECT * FROM transactions WHERE user_id={p} ORDER BY id DESC LIMIT 1",
                    (user_id,)
                )
                row = db.fetchone()
            return Transaction(dict(row) if row else {
                'id': 0, 'user_id': user_id, 'type': type_,
                'amount': amount, 'status': status, 'meta': meta or {},
                'created_at': ''
            })

    @staticmethod
    def list_for_user(user_id: int, limit: int = 50,
                      types: Optional[List[str]] = None) -> list['Transaction']:
        """List transactions for user. Pass types=['deposit','withdraw'] to filter."""
        p = _p()
        if types:
            placeholders = ','.join([p] * len(types))
            sql = (f"SELECT * FROM transactions"
                   f" WHERE user_id={p} AND type IN ({placeholders})"
                   f" ORDER BY created_at DESC LIMIT {int(limit)}")
            params = (user_id, *types)
        else:
            sql = (f"SELECT * FROM transactions WHERE user_id={p}"
                   f" ORDER BY created_at DESC LIMIT {int(limit)}")
            params = (user_id,)

        with get_db() as db:
            db.execute(sql, params)
            rows = db.fetchall()
        return [Transaction(dict(r)) for r in rows]
