
import time
import asyncio
from typing import Dict, Optional, Any, Tuple
from logger import bot_logger

class CacheManager:
    def __init__(self):
        self.cache: Dict[str, Tuple[Any, float]] = {}  # key: (value, timestamp)
        self.default_ttl = 30  # секунд
        self.ticker_cache_ttl = 10  # кеш тикеров на 10 секунд
        self.price_cache_ttl = 5   # кеш цен на 5 секунд

    def _is_expired(self, timestamp: float, ttl: int) -> bool:
        """Проверяет, истек ли кеш"""
        return time.time() - timestamp > ttl

    def get(self, key: str, ttl: Optional[int] = None) -> Optional[Any]:
        """Получает значение из кеша"""
        if key not in self.cache:
            return None
            
        value, timestamp = self.cache[key]
        cache_ttl = ttl or self.default_ttl
        
        if self._is_expired(timestamp, cache_ttl):
            del self.cache[key]
            return None
            
        return value

    def set(self, key: str, value: Any) -> None:
        """Сохраняет значение в кеш"""
        self.cache[key] = (value, time.time())

    def get_ticker_cache(self, symbol: str) -> Optional[Dict]:
        """Получает тикер из кеша"""
        return self.get(f"ticker_{symbol}", self.ticker_cache_ttl)

    def set_ticker_cache(self, symbol: str, data: Dict) -> None:
        """Сохраняет тикер в кеш"""
        self.set(f"ticker_{symbol}", data)

    def get_price_cache(self, symbol: str) -> Optional[float]:
        """Получает цену из кеша"""
        return self.get(f"price_{symbol}", self.price_cache_ttl)

    def set_price_cache(self, symbol: str, price: float) -> None:
        """Сохраняет цену в кеш"""
        self.set(f"price_{symbol}", price)

    def clear_expired(self) -> None:
        """Очищает устаревшие записи"""
        current_time = time.time()
        expired_keys = []
        
        for key, (_, timestamp) in self.cache.items():
            if self._is_expired(timestamp, self.default_ttl):
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.cache[key]
            
        if expired_keys:
            bot_logger.debug(f"Очищено {len(expired_keys)} устаревших записей кеша")

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику кеша"""
        return {
            'total_entries': len(self.cache),
            'memory_usage_kb': len(str(self.cache)) / 1024
        }

# Глобальный экземпляр кеша
cache_manager = CacheManager()
