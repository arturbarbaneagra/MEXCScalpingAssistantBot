import time
import asyncio
from typing import Dict, Optional, Any, Tuple
from logger import bot_logger
import sys

class CacheManager:
    def __init__(self):
        self.ticker_cache: Dict[str, Dict] = {}
        self.price_cache: Dict[str, float] = {}
        self.volume_cache: Dict[str, float] = {}
        self.trades_cache: Dict[str, int] = {}
        self.cache_timestamps: Dict[str, float] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self.ttl = 5  # Time to live в секундах

    def get_ticker_cache(self, symbol: str) -> Optional[Dict]:
        """Получает данные тикера из кеша"""
        cache_key = f"ticker_{symbol}"
        if symbol in self.ticker_cache:
            if time.time() - self.cache_timestamps.get(cache_key, 0) < self.ttl:
                self.cache_hits += 1
                return self.ticker_cache[symbol]
            else:
                # Удаляем устаревшие данные
                del self.ticker_cache[symbol]
                if cache_key in self.cache_timestamps:
                    del self.cache_timestamps[cache_key]

        self.cache_misses += 1
        return None

    def set_ticker_cache(self, symbol: str, data: Dict) -> None:
        """Сохраняет тикер в кеш"""
        self.ticker_cache[symbol] = data
        self.cache_timestamps[f"ticker_{symbol}"] = time.time()

    def get_price_cache(self, symbol: str) -> Optional[float]:
        """Получает цену из кеша"""
        if symbol in self.price_cache:
            if time.time() - self.cache_timestamps.get(f"price_{symbol}", 0) < self.ttl:
                self.cache_hits += 1
                return self.price_cache[symbol]
            else:
                del self.price_cache[symbol]
                if f"price_{symbol}" in self.cache_timestamps:
                    del self.cache_timestamps[f"price_{symbol}"]

        self.cache_misses += 1
        return None

    def set_price_cache(self, symbol: str, price: float) -> None:
        """Сохраняет цену в кеш"""
        self.price_cache[symbol] = price
        self.cache_timestamps[f"price_{symbol}"] = time.time()

    def get_volume_cache(self, symbol: str) -> Optional[float]:
        """Получает кешированный объём"""
        cache_key = f"volume_{symbol}"
        if symbol in self.volume_cache:
            if time.time() - self.cache_timestamps.get(cache_key, 0) < self.ttl:
                self.cache_hits += 1
                return self.volume_cache[symbol]
            else:
                del self.volume_cache[symbol]
                if cache_key in self.cache_timestamps:
                    del self.cache_timestamps[cache_key]

        self.cache_misses += 1
        return None

    def set_volume_cache(self, symbol: str, volume: float):
        """Кеширует объём"""
        self.volume_cache[symbol] = volume
        self.cache_timestamps[f"volume_{symbol}"] = time.time()

    def get_trades_cache(self, symbol: str) -> Optional[int]:
        """Получает кешированное количество сделок"""
        cache_key = f"trades_{symbol}"
        if symbol in self.trades_cache:
            if time.time() - self.cache_timestamps.get(cache_key, 0) < self.ttl:
                self.cache_hits += 1
                return self.trades_cache[symbol]
            else:
                del self.trades_cache[symbol]
                if cache_key in self.cache_timestamps:
                    del self.cache_timestamps[cache_key]

        self.cache_misses += 1
        return None

    def set_trades_cache(self, symbol: str, trades: int):
        """Кеширует количество сделок"""
        self.trades_cache[symbol] = trades
        self.cache_timestamps[f"trades_{symbol}"] = time.time()

    def get_cache_efficiency(self) -> float:
        """Возвращает эффективность кеша в процентах"""
        total = self.cache_hits + self.cache_misses
        return (self.cache_hits / total * 100) if total > 0 else 0

    def clear_expired(self):
        """Очищает устаревшие записи из кеша"""
        current_time = time.time()
        expired_keys = []
        
        # Собираем устаревшие ключи
        for key, timestamp in self.cache_timestamps.items():
            if current_time - timestamp > self.ttl:
                expired_keys.append(key)
        
        # Удаляем устаревшие записи
        for key in expired_keys:
            del self.cache_timestamps[key]
            
            # Определяем тип кеша и удаляем соответствующую запись
            if key.startswith('ticker_'):
                symbol = key.replace('ticker_', '')
                if symbol in self.ticker_cache:
                    del self.ticker_cache[symbol]
            elif key.startswith('price_'):
                symbol = key.replace('price_', '')
                if symbol in self.price_cache:
                    del self.price_cache[symbol]
            elif key.startswith('volume_'):
                symbol = key.replace('volume_', '')
                if symbol in self.volume_cache:
                    del self.volume_cache[symbol]
            elif key.startswith('trades_'):
                symbol = key.replace('trades_', '')
                if symbol in self.trades_cache:
                    del self.trades_cache[symbol]
        
        if expired_keys:
            bot_logger.debug(f"Очищено {len(expired_keys)} устаревших записей кеша")

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику кеша"""
        current_time = time.time()
        valid_entries = 0
        expired_entries = 0

        for key, timestamp in self.cache_timestamps.items():
            if current_time - timestamp < self.ttl:
                valid_entries += 1
            else:
                expired_entries += 1

        return {
            'total_entries': len(self.cache_timestamps),
            'valid_entries': valid_entries,
            'expired_entries': expired_entries,
            'ticker_cache_size': len(self.ticker_cache),
            'price_cache_size': len(self.price_cache),
            'volume_cache_size': len(self.volume_cache),
            'trades_cache_size': len(self.trades_cache),
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'cache_efficiency': self.get_cache_efficiency(),
            'memory_usage_kb': (
                sys.getsizeof(self.ticker_cache) + 
                sys.getsizeof(self.price_cache) +
                sys.getsizeof(self.volume_cache) +
                sys.getsizeof(self.trades_cache) +
                sys.getsizeof(self.cache_timestamps)
            ) / 1024
        }

# Глобальный экземпляр кеша
cache_manager = CacheManager()