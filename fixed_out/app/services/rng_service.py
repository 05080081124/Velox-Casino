"""
app/services/rng_service.py — Криптографически стойкий RNG для игр.

Все результаты игр должны генерироваться ТОЛЬКО через этот сервис —
это даёт аудируемость и возможность добавить provably fair в будущем.
"""
import hmac, hashlib, os, secrets, struct
from typing import Optional


class RNGService:
    """
    Детерминированный RNG на основе HMAC-SHA256.
    При желании можно сделать provably fair: раскрыть server_seed после раунда.
    """

    # ── Внутренний метод ────────────────────────────────────────────────────

    @staticmethod
    def _float_from_seed(server_seed: str, client_seed: str, nonce: int) -> float:
        """Возвращает float [0, 1) из комбинации seeds."""
        msg = f"{client_seed}:{nonce}".encode()
        key = server_seed.encode()
        digest = hmac.new(key, msg, hashlib.sha256).digest()
        # Берём первые 4 байта → uint32 → нормализуем
        value = struct.unpack('>I', digest[:4])[0]
        return value / 0xFFFFFFFF

    # ── Публичные методы ────────────────────────────────────────────────────

    @staticmethod
    def generate_server_seed() -> str:
        """Новый случайный server seed (64 hex-символа)."""
        return secrets.token_hex(32)

    @staticmethod
    def random_float(server_seed: Optional[str] = None,
                     client_seed: Optional[str] = None,
                     nonce: int = 0) -> float:
        """Возвращает float [0, 1)."""
        if not server_seed:
            return secrets.randbelow(0xFFFFFFFF) / 0xFFFFFFFF
        cs = client_seed or secrets.token_hex(8)
        return RNGService._float_from_seed(server_seed, cs, nonce)

    @staticmethod
    def crash_point(house_edge: float = 0.03,
                    server_seed: Optional[str] = None,
                    client_seed: Optional[str] = None,
                    nonce: int = 0) -> float:
        """
        Генерирует точку краша по формуле provably fair:
            h = (1 - house_edge) / (1 - r)
        Гарантирует house edge ≈ house_edge (обычно 3–5%).
        """
        r = RNGService.random_float(server_seed, client_seed, nonce)
        # r < house_edge → немедленный краш (house win)
        if r < house_edge:
            return 1.00
        result = (1.0 - house_edge) / (1.0 - r)
        return round(min(result, 1000.0), 2)

    @staticmethod
    def weighted_choice(items: list, weights: list,
                        server_seed: Optional[str] = None,
                        client_seed: Optional[str] = None,
                        nonce: int = 0):
        """Взвешенный случайный выбор из списка."""
        r = RNGService.random_float(server_seed, client_seed, nonce)
        total = sum(weights)
        cumulative = 0.0
        for item, w in zip(items, weights):
            cumulative += w / total
            if r <= cumulative:
                return item
        return items[-1]

    @staticmethod
    def mines_board(total_cells: int, mine_count: int,
                    server_seed: Optional[str] = None,
                    client_seed: Optional[str] = None,
                    nonce: int = 0) -> list[int]:
        """
        Возвращает список индексов мин (длина = mine_count).
        Использует Fisher-Yates shuffle на основе seed.
        """
        indices = list(range(total_cells))
        for i in range(total_cells - 1, total_cells - mine_count - 1, -1):
            r = RNGService.random_float(server_seed, client_seed, nonce + i)
            j = int(r * (i + 1))
            indices[i], indices[j] = indices[j], indices[i]
        return sorted(indices[total_cells - mine_count:])

    @staticmethod
    def roulette_spin(server_seed: Optional[str] = None,
                      client_seed: Optional[str] = None,
                      nonce: int = 0) -> int:
        """Возвращает число от 0 до 36."""
        r = RNGService.random_float(server_seed, client_seed, nonce)
        return int(r * 37)

    @staticmethod
    def slots_reels(reel_count: int, symbol_count: int,
                    server_seed: Optional[str] = None,
                    client_seed: Optional[str] = None,
                    nonce: int = 0) -> list[int]:
        """Возвращает список символов для каждого барабана."""
        return [
            int(RNGService.random_float(server_seed, client_seed, nonce + i) * symbol_count)
            for i in range(reel_count)
        ]


# Глобальный экземпляр
rng = RNGService()
