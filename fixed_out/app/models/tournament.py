"""app/models/tournament.py — сезоны турниров и таблица участников."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Optional

from app.db import DB_ENGINE, get_db


def _p() -> str:
    return "%s" if DB_ENGINE == "postgresql" else "?"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period_duration(period: str) -> timedelta:
    if period == "day":
        return timedelta(days=1)
    if period == "month":
        return timedelta(days=30)
    if period == "all":
        return timedelta(days=3650)
    return timedelta(days=7)


def _parse_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_game_ids(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    text = str(raw).strip()
    if not text:
        return set()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return {str(x).strip() for x in data if str(x).strip()}
    except Exception:
        pass
    return {p.strip() for p in text.split(",") if p.strip()}


def _normalize_tour_row(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    out = dict(row)
    game_ids = sorted(_parse_game_ids(out.get("game_ids")))
    out["game_ids"] = game_ids
    glow = str(out.get("glow_color") or "").strip()
    out["glow_color"] = glow or "#f5c451"
    out["description"] = str(out.get("description") or "").strip()
    return out


class Tournament:
    @staticmethod
    def _finalize_expired() -> None:
        now_iso = _iso_now()
        p = _p()
        with get_db() as db:
            db.execute(
                f"""
                UPDATE tournaments
                SET status='completed', updated_at={p}
                WHERE status='active'
                  AND ends_at <= {p}
                """,
                (now_iso, now_iso),
            )

    @staticmethod
    def _get_relevant(period: str) -> Optional[dict]:
        now_iso = _iso_now()
        p = _p()
        with get_db() as db:
            # 1) active now for selected period
            db.execute(
                f"""
                SELECT *
                FROM tournaments
                WHERE status='active'
                  AND period={p}
                  AND starts_at <= {p}
                  AND ends_at > {p}
                ORDER BY starts_at DESC, id DESC
                LIMIT 1
                """,
                (period, now_iso, now_iso),
            )
            row = db.fetchone()
            if row:
                return _normalize_tour_row(dict(row))

            # 2) any active now
            db.execute(
                f"""
                SELECT *
                FROM tournaments
                WHERE status='active'
                  AND starts_at <= {p}
                  AND ends_at > {p}
                ORDER BY starts_at DESC, id DESC
                LIMIT 1
                """,
                (now_iso, now_iso),
            )
            row = db.fetchone()
            if row:
                return _normalize_tour_row(dict(row))

            # 3) nearest upcoming for selected period
            db.execute(
                f"""
                SELECT *
                FROM tournaments
                WHERE status='active'
                  AND period={p}
                  AND starts_at > {p}
                ORDER BY starts_at ASC, id ASC
                LIMIT 1
                """,
                (period, now_iso),
            )
            row = db.fetchone()
            if row:
                return _normalize_tour_row(dict(row))

            # 4) nearest upcoming any
            db.execute(
                f"""
                SELECT *
                FROM tournaments
                WHERE status='active'
                  AND starts_at > {p}
                ORDER BY starts_at ASC, id ASC
                LIMIT 1
                """,
                (now_iso,),
            )
            row = db.fetchone()
            return _normalize_tour_row(dict(row)) if row else None

    @staticmethod
    def ensure_default_active(period: str) -> dict:
        """Создаёт активный сезон для периода, если его ещё нет."""
        period = (period or "week").strip().lower()
        if period not in {"day", "week", "month", "all"}:
            period = "week"

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        p = _p()
        with get_db() as db:
            db.execute(
                f"""
                SELECT *
                FROM tournaments
                WHERE period = {p}
                  AND status = 'active'
                  AND starts_at <= {p}
                  AND ends_at > {p}
                ORDER BY id DESC
                LIMIT 1
                """,
                (period, now_iso, now_iso),
            )
            existing = db.fetchone()
            if existing:
                return dict(existing)

            starts_at = now
            ends_at = now + _period_duration(period)
            title_map = {
                "day": "Daily Sprint",
                "week": "Weekly Championship",
                "month": "Monthly Masters",
                "all": "Hall of Fame Season",
            }
            prize_map = {"day": 500.0, "week": 2500.0, "month": 10000.0, "all": 50000.0}
            db.execute(
                f"""
                INSERT INTO tournaments
                    (slug, title, period, prize_pool, starts_at, ends_at, status, created_at, updated_at)
                VALUES
                    ({p}, {p}, {p}, {p}, {p}, {p}, 'active', {p}, {p})
                RETURNING *
                """,
                (
                    f"{period}-{int(now.timestamp())}",
                    title_map[period],
                    period,
                    prize_map[period],
                    starts_at.isoformat(),
                    ends_at.isoformat(),
                    now_iso,
                    now_iso,
                ),
            )
            created = db.fetchone()
            return dict(created)

    @staticmethod
    def get_active(period: str) -> Optional[dict]:
        period = (period or "week").strip().lower()
        if period not in {"day", "week", "month", "all"}:
            period = "week"
        row = Tournament.ensure_default_active(period)
        return dict(row) if row else None

    @staticmethod
    def maybe_rollover(period: str) -> dict:
        period = (period or "week").strip().lower()
        if period not in {"day", "week", "month", "all"}:
            period = "week"
        Tournament._finalize_expired()
        tour = Tournament._get_relevant(period)
        if tour:
            return tour
        return Tournament.ensure_default_active(period)

    @staticmethod
    def apply_round(
        user_id: int,
        bet: float,
        win: float,
        created_at_iso: Optional[str] = None,
        game_id: Optional[str] = None,
    ) -> None:
        """Обновляет статистику пользователя в активных турнирах."""
        try:
            created_at = _parse_dt(created_at_iso) if created_at_iso else datetime.now(timezone.utc)
            if not created_at:
                created_at = datetime.now(timezone.utc)
            now_iso = created_at.isoformat()
            p = _p()
            with get_db() as db:
                db.execute(
                    f"""
                    SELECT id, game_ids
                    FROM tournaments
                    WHERE status='active'
                      AND starts_at <= {p}
                      AND ends_at > {p}
                    """,
                    (now_iso, now_iso),
                )
                rows = db.fetchall() or []

            gid = str(game_id or "").strip()
            for row in rows:
                tid = int(row.get("id"))
                allowed_games = _parse_game_ids(row.get("game_ids"))
                if allowed_games and gid not in allowed_games:
                    continue
                with get_db() as db:
                    db.execute(
                        f"""
                        INSERT INTO tournament_entries
                            (tournament_id, user_id, total_bet, total_win, total_profit, rounds, updated_at)
                        VALUES
                            ({p}, {p}, {p}, {p}, {p}, 1, {p})
                        ON CONFLICT(tournament_id, user_id) DO UPDATE SET
                            total_bet = tournament_entries.total_bet + excluded.total_bet,
                            total_win = tournament_entries.total_win + excluded.total_win,
                            total_profit = tournament_entries.total_profit + excluded.total_profit,
                            rounds = tournament_entries.rounds + 1,
                            updated_at = excluded.updated_at
                        """,
                        (tid, user_id, float(bet), float(win), float(win) - float(bet), now_iso),
                    )
        except Exception as e:
            print(f"[tournaments] apply_round skipped: {e}")

    @staticmethod
    def leaderboard(period: str, limit: int = 20) -> tuple[Optional[dict], list[dict]]:
        period = (period or "week").strip().lower()
        if period not in {"day", "week", "month", "all"}:
            period = "week"
        limit = min(max(int(limit or 20), 1), 100)

        Tournament._finalize_expired()
        tour = Tournament._get_relevant(period)
        if not tour:
            return None, []

        now = datetime.now(timezone.utc)
        starts_at = _parse_dt(tour.get("starts_at"))
        ends_at = _parse_dt(tour.get("ends_at"))
        if starts_at and now < starts_at:
            return dict(tour), []
        if ends_at and now >= ends_at:
            return dict(tour), []

        p = _p()
        with get_db() as db:
            db.execute(
                f"""
                SELECT
                    te.user_id AS user_id,
                    u.user_name AS username,
                    u.icon_url AS icon_url,
                    te.total_win AS total_win,
                    te.total_bet AS total_bet,
                    te.total_profit AS total_profit,
                    te.rounds AS rounds
                FROM tournament_entries te
                JOIN users u ON u.id = te.user_id
                WHERE te.tournament_id = {p}
                ORDER BY te.total_win DESC, te.total_profit DESC, te.rounds DESC, te.user_id ASC
                LIMIT {p}
                """,
                (tour["id"], limit),
            )
            rows = db.fetchall() or []
        return dict(tour), [dict(r) for r in rows]
