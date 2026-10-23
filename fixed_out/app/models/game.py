"""app/models/game.py — История игр и работа с game_history."""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from app.db import get_db, DB_ENGINE
from app.models.tournament import Tournament
import json


def _ph(n: int = 1) -> str:
    """Return n placeholders for current DB engine: ? (sqlite) or %s (pg)."""
    ph = '%s' if DB_ENGINE == 'postgresql' else '?'
    return ', '.join([ph] * n)

def _p() -> str:
    return '%s' if DB_ENGINE == 'postgresql' else '?'


class GameRound:
    def __init__(self, row: dict):
        self._r = row

    @property
    def id(self) -> int:               return self._r['id']
    @property
    def user_id(self) -> int:          return self._r['user_id']
    @property
    def game_id(self) -> str:          return self._r['game_id']
    @property
    def bet(self) -> float:            return float(self._r['bet'])
    @property
    def multiplier(self) -> Optional[float]:
        v = self._r.get('multiplier')
        return float(v) if v is not None else None
    @property
    def win(self) -> float:            return float(self._r.get('win', 0))
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
        meta = self.meta or {}
        return {
            'id': self.id, 'user_id': self.user_id, 'game_id': self.game_id,
            'bet': round(self.bet, 2), 'multiplier': self.multiplier,
            'win': round(self.win, 2), 'meta': meta,
            # currency saved at time of bet — for correct history display
            'currency': meta.get('currency', 'UAH'),
            'created_at': self.created_at,
        }

    # ── CRUD ────────────────────────────────────────────────────────────────
    @staticmethod
    def record(user_id: int, game_id: str, bet: float,
               multiplier: Optional[float] = None, win: float = 0,
               meta: Optional[dict] = None) -> 'GameRound':
        meta_json = json.dumps(meta or {})
        p = _p()

        if DB_ENGINE == 'postgresql':
            with get_db() as db:
                db.execute(
                    f"INSERT INTO game_history (user_id, game_id, bet, multiplier, win, meta, created_at)"
                    f" VALUES ({_ph(6)}, NOW()) RETURNING *",
                    (user_id, game_id, bet, multiplier, win, meta_json)
                )
                row = db.fetchone()
            rec = GameRound(dict(row))
            Tournament.apply_round(
                user_id=user_id,
                bet=bet,
                win=win,
                created_at_iso=rec.created_at,
                game_id=game_id,
            )
            return rec
        else:
            # SQLite
            with get_db() as db:
                db.execute(
                    f"INSERT INTO game_history (user_id, game_id, bet, multiplier, win, meta, created_at)"
                    f" VALUES ({_ph(6)}, datetime('now'))",
                    (user_id, game_id, bet, multiplier, win, meta_json)
                )
            with get_db() as db:
                db.execute(
                    f"SELECT * FROM game_history WHERE user_id={p} AND game_id={p}"
                    f" ORDER BY id DESC LIMIT 1",
                    (user_id, game_id)
                )
                row = db.fetchone()
            rec = GameRound(dict(row) if row else {
                'id': 0, 'user_id': user_id, 'game_id': game_id,
                'bet': bet, 'multiplier': multiplier, 'win': win,
                'meta': meta or {}, 'created_at': ''
            })
            Tournament.apply_round(
                user_id=user_id,
                bet=bet,
                win=win,
                created_at_iso=rec.created_at,
                game_id=game_id,
            )
            return rec

    @staticmethod
    def count_for_user(user_id: int, game_id: str) -> int:
        p = _p()
        with get_db() as db:
            db.execute(
                f'SELECT COUNT(*) AS cnt FROM game_history WHERE user_id={p} AND game_id={p}',
                (user_id, game_id)
            )
            row = db.fetchone()
        return int(row['cnt']) if row else 0

    @staticmethod
    def list_for_user(user_id: int, game_id: Optional[str] = None,
                      limit: int = 20) -> list['GameRound']:
        p = _p()
        if game_id:
            with get_db() as db:
                db.execute(
                    f'SELECT * FROM game_history WHERE user_id={p} AND game_id={p}'
                    f' ORDER BY created_at DESC LIMIT {int(limit)}',
                    (user_id, game_id)
                )
                rows = db.fetchall()
        else:
            with get_db() as db:
                db.execute(
                    f'SELECT * FROM game_history WHERE user_id={p}'
                    f' ORDER BY created_at DESC LIMIT {int(limit)}',
                    (user_id,)
                )
                rows = db.fetchall()
        return [GameRound(dict(r)) for r in rows]
