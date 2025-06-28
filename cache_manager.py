import time
import asyncio
from typing import Dict, Optional, Any, Tuple
from logger import bot_logger
import sys

class CacheManager:
    def __init__(self, default_ttl: int = 8):  # Увеличиваем TTL
        self.default_ttl = default_ttl
        self.caches = {
            'ticker': {},
            'price': {},
            'trades': {},
            'book_ticker': {}  # Добавляем кеш для book_ticker
        }
        self.cache_stats = {
            'hits': 0,
            'misses': 0,
            'cleanups': 0
        }
        self.last_cleanup = time.time()
        self.cleanup_interval = 30  # Очистка каждые 30 секунд

    def get_ticker_cache(self, symbol: str) -> Optional[Dict]:
        """Получает данные тикера из кеша"""
        self._auto_cleanup()
        cache_key = f"{symbol}_ticker"
        if cache_key in self.caches['ticker']:
            entry = self.caches['ticker'][cache_key]
            if time.time() - entry['timestamp'] < self.default_ttl:
                self.cache_stats['hits'] += 1
                return entry['data']
            else:
                del self.caches['ticker'][cache_key]
                self.cache_stats['misses'] += 1
                return None

        self.cache_stats['misses'] += 1
        return None

    def set_ticker_cache(self, symbol: str, data: Dict) -> None:
        """Сохраняет тикер в кеш"""
        cache_key = f"{symbol}_ticker"
        self.caches['ticker'][cache_key] = {
            'data': data,
            'timestamp': time.time()
        }

    def get_price_cache(self, symbol: str) -> Optional[float]:
        """Получает цену из кеша"""
        self._auto_cleanup()
        cache_key = f"{symbol}_price"
        if cache_key in self.caches['price']:
            entry = self.caches['price'][cache_key]
            if time.time() - entry['timestamp'] < self.default_ttl:
                self.cache_stats['hits'] += 1
                return entry['data']
            else:
                del self.caches['price'][cache_key]

        self.cache_stats['misses'] += 1
        return None

    def set_price_cache(self, symbol: str, price: float) -> None:
        """Сохраняет цену в кеш"""
        cache_key = f"{symbol}_price"
        self.caches['price'][cache_key] = {
            'data': price,
            'timestamp': time.time()
        }

    def get_volume_cache(self, symbol: str) -> Optional[float]:
        """Получает кешированный объём"""
        return None

    def set_volume_cache(self, symbol: str, volume: float):
        """Кеширует объём"""
        pass

    def get_trades_cache(self, symbol: str) -> Optional[int]:
        """Получает кешированное количество сделок"""
        self._auto_cleanup()
        cache_key = f"{symbol}_trades"
        if cache_key in self.caches['trades']:
            entry = self.caches['trades'][cache_key]
            if time.time() - entry['timestamp'] < self.default_ttl:
                self.cache_stats['hits'] += 1
                return entry['data']
            else:
                del self.caches['trades'][cache_key]

        self.cache_stats['misses'] += 1
        return None

    def set_trades_cache(self, symbol: str, trades: int):
        """Кеширует количество сделок"""
        cache_key = f"{symbol}_trades"
        self.caches['trades'][cache_key] = {
            'data': trades,
            'timestamp': time.time()
        }
    def get_book_ticker_cache(self, symbol: str) -> Optional[Dict]:
        """Получает book ticker из кеша"""
        self._auto_cleanup()
        cache_key = f"{symbol}_book"
        if cache_key in self.caches['book_ticker']:
            entry = self.caches['book_ticker'][cache_key]
            if time.time() - entry['timestamp'] < self.default_ttl:
                self.cache_stats['hits'] += 1
                return entry['data']
            else:
                del self.caches['book_ticker'][cache_key]

        self.cache_stats['misses'] += 1
        return None

    def set_book_ticker_cache(self, symbol: str, data: Dict):
        """Сохраняет book ticker в кеш"""
        cache_key = f"{symbol}_book"
        self.caches['book_ticker'][cache_key] = {
            'data': data,
            'timestamp': time.time()
        }

    def _auto_cleanup(self):
        """Автоматическая очистка устаревших записей"""
        current_time = time.time()
        if current_time - self.last_cleanup > self.cleanup_interval:
            self._cleanup_expired()
            self.last_cleanup = current_time

    def _cleanup_expired(self):
        """Очищает устаревшие записи из всех кешей"""
        current_time = time.time()
        cleaned_count = 0

        for cache_name, cache in self.caches.items():
            expired_keys = []
            for key, entry in cache.items():
                if current_time - entry['timestamp'] > self.default_ttl * 2:  # Удаляем через 2*TTL
                    expired_keys.append(key)

            for key in expired_keys:
                del cache[key]
                cleaned_count += 1

        if cleaned_count > 0:
            self.cache_stats['cleanups'] += 1
            bot_logger.debug(f"🧹 Очищено {cleaned_count} устаревших записей кеша")

    def get_cache_efficiency(self) -> float:
        """Возвращает эффективность кеша в процентах"""
        total = self.cache_stats['hits'] + self.cache_stats['misses']
        return (self.cache_stats['hits'] / total * 100) if total > 0 else 0

    def clear_all(self):
        """Очищает все кеши"""
        for cache in self.caches.values():
            cache.clear()
        self.cache_stats = {'hits': 0, 'misses': 0, 'cleanups': 0}
        bot_logger.debug("🧹 Все кеши очищены")

    def clear_expired(self):
        """Очищает устаревшие записи из кеша"""
        current_time = time.time()
        cleaned_count = 0

        for cache_name, cache in self.caches.items():
            expired_keys = []
            for key, entry in cache.items():
                if current_time - entry['timestamp'] > self.default_ttl:
                    expired_keys.append(key)

            for key in expired_keys:
                del cache[key]
                cleaned_count += 1

        if cleaned_count > 0:
            self.cache_stats['cleanups'] += 1
            bot_logger.debug(f"🧹 Очищено {cleaned_count} устаревших записей кеша")

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику кеша"""
        current_time = time.time()
        total_entries = sum(len(cache) for cache in self.caches.values())

        return {
            'total_entries': total_entries,
            'cache_hits': self.cache_stats['hits'],
            'cache_misses': self.cache_stats['misses'],
            'cache_cleanups': self.cache_stats['cleanups'],
            'cache_efficiency': self.get_cache_efficiency(),
            'ticker_cache_size': len(self.caches['ticker']),
            'price_cache_size': len(self.caches['price']),
            'trades_cache_size': len(self.caches['trades']),
            'book_ticker_size': len(self.caches['book_ticker']),
            'memory_usage_kb': (
                sys.getsizeof(self.caches['ticker']) +
                sys.getsizeof(self.caches['price']) +
                sys.getsizeof(self.caches['trades']) +
                sys.getsizeof(self.caches['book_ticker'])
            ) / 1024
        }

# Глобальный экземпляр кеша
cache_manager = CacheManager()