
import time
import threading
from typing import Dict, Optional, Any, List

class CacheManager:
    def __init__(self):
        self.ticker_cache: Dict[str, Dict] = {}
        self.book_ticker_cache: Dict[str, Dict] = {}
        self.klines_cache: Dict[str, Dict] = {}
        self.price_cache: Dict[str, float] = {}
        self.all_tickers_cache: Optional[List] = None
        self.all_tickers_timestamp = 0
        
        # Timestamps для TTL
        self.ticker_timestamps: Dict[str, float] = {}
        self.book_ticker_timestamps: Dict[str, float] = {}
        self.klines_timestamps: Dict[str, float] = {}
        self.price_timestamps: Dict[str, float] = {}
        
        self.lock = threading.RLock()
        self.default_ttl = 60  # 60 секунд по умолчанию

    def set_ticker_cache(self, symbol: str, data: Dict, ttl: int = None):
        """Кеширует данные тикера"""
        with self.lock:
            self.ticker_cache[symbol] = data
            self.ticker_timestamps[symbol] = time.time() + (ttl or self.default_ttl)

    def get_ticker_cache(self, symbol: str) -> Optional[Dict]:
        """Получает данные тикера из кеша"""
        with self.lock:
            if symbol in self.ticker_cache:
                if time.time() < self.ticker_timestamps.get(symbol, 0):
                    return self.ticker_cache[symbol]
                else:
                    # Удаляем устаревшие данные
                    self.ticker_cache.pop(symbol, None)
                    self.ticker_timestamps.pop(symbol, None)
            return None

    def set_book_ticker_cache(self, symbol: str, data: Dict, ttl: int = None):
        """Кеширует данные book ticker"""
        with self.lock:
            self.book_ticker_cache[symbol] = data
            self.book_ticker_timestamps[symbol] = time.time() + (ttl or 30)

    def get_book_ticker_cache(self, symbol: str) -> Optional[Dict]:
        """Получает данные book ticker из кеша"""
        with self.lock:
            if symbol in self.book_ticker_cache:
                if time.time() < self.book_ticker_timestamps.get(symbol, 0):
                    return self.book_ticker_cache[symbol]
                else:
                    self.book_ticker_cache.pop(symbol, None)
                    self.book_ticker_timestamps.pop(symbol, None)
            return None

    def set_klines_cache(self, symbol: str, data: List, ttl: int = None):
        """Кеширует данные klines"""
        with self.lock:
            self.klines_cache[symbol] = data
            self.klines_timestamps[symbol] = time.time() + (ttl or 30)

    def get_klines_cache(self, symbol: str) -> Optional[List]:
        """Получает данные klines из кеша"""
        with self.lock:
            if symbol in self.klines_cache:
                if time.time() < self.klines_timestamps.get(symbol, 0):
                    return self.klines_cache[symbol]
                else:
                    self.klines_cache.pop(symbol, None)
                    self.klines_timestamps.pop(symbol, None)
            return None

    def set_price_cache(self, symbol: str, price: float, ttl: int = None):
        """Кеширует цену"""
        with self.lock:
            self.price_cache[symbol] = price
            self.price_timestamps[symbol] = time.time() + (ttl or 30)

    def get_price_cache(self, symbol: str) -> Optional[float]:
        """Получает цену из кеша"""
        with self.lock:
            if symbol in self.price_cache:
                if time.time() < self.price_timestamps.get(symbol, 0):
                    return self.price_cache[symbol]
                else:
                    self.price_cache.pop(symbol, None)
                    self.price_timestamps.pop(symbol, None)
            return None

    def set_all_tickers_cache(self, tickers: List, ttl: int = None):
        """Кеширует все тикеры"""
        with self.lock:
            self.all_tickers_cache = tickers
            self.all_tickers_timestamp = time.time() + (ttl or 30)

    def get_all_tickers_cache(self) -> Optional[List]:
        """Получает все тикеры из кеша"""
        with self.lock:
            if self.all_tickers_cache and time.time() < self.all_tickers_timestamp:
                return self.all_tickers_cache
            else:
                self.all_tickers_cache = None
                self.all_tickers_timestamp = 0
                return None

    def clear_expired(self):
        """Очищает устаревшие записи"""
        current_time = time.time()
        
        with self.lock:
            # Очищаем тикеры
            expired_tickers = [
                symbol for symbol, timestamp in self.ticker_timestamps.items()
                if current_time > timestamp
            ]
            for symbol in expired_tickers:
                self.ticker_cache.pop(symbol, None)
                self.ticker_timestamps.pop(symbol, None)

            # Очищаем book tickers
            expired_book = [
                symbol for symbol, timestamp in self.book_ticker_timestamps.items()
                if current_time > timestamp
            ]
            for symbol in expired_book:
                self.book_ticker_cache.pop(symbol, None)
                self.book_ticker_timestamps.pop(symbol, None)

            # Очищаем klines
            expired_klines = [
                symbol for symbol, timestamp in self.klines_timestamps.items()
                if current_time > timestamp
            ]
            for symbol in expired_klines:
                self.klines_cache.pop(symbol, None)
                self.klines_timestamps.pop(symbol, None)

            # Очищаем цены
            expired_prices = [
                symbol for symbol, timestamp in self.price_timestamps.items()
                if current_time > timestamp
            ]
            for symbol in expired_prices:
                self.price_cache.pop(symbol, None)
                self.price_timestamps.pop(symbol, None)

            # Очищаем все тикеры
            if current_time > self.all_tickers_timestamp:
                self.all_tickers_cache = None
                self.all_tickers_timestamp = 0

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику кеша"""
        with self.lock:
            return {
                'ticker_entries': len(self.ticker_cache),
                'book_ticker_entries': len(self.book_ticker_cache),
                'klines_entries': len(self.klines_cache),
                'price_entries': len(self.price_cache),
                'total_entries': len(self.ticker_cache) + len(self.book_ticker_cache) + 
                               len(self.klines_cache) + len(self.price_cache),
                'memory_usage_kb': self._estimate_memory_usage() / 1024,
                'has_all_tickers': self.all_tickers_cache is not None
            }

    def _estimate_memory_usage(self) -> int:
        """Оценивает использование памяти в байтах"""
        try:
            import sys
            total_size = 0
            
            # Примерная оценка размера структур данных
            total_size += sys.getsizeof(self.ticker_cache)
            total_size += sys.getsizeof(self.book_ticker_cache)
            total_size += sys.getsizeof(self.klines_cache)
            total_size += sys.getsizeof(self.price_cache)
            
            if self.all_tickers_cache:
                total_size += sys.getsizeof(self.all_tickers_cache)
            
            return total_size
        except:
            return 0

    def clear_all(self):
        """Очищает весь кеш"""
        with self.lock:
            self.ticker_cache.clear()
            self.book_ticker_cache.clear()
            self.klines_cache.clear()
            self.price_cache.clear()
            self.ticker_timestamps.clear()
            self.book_ticker_timestamps.clear()
            self.klines_timestamps.clear()
            self.price_timestamps.clear()
            self.all_tickers_cache = None
            self.all_tickers_timestamp = 0

# Глобальный экземпляр
cache_manager = CacheManager()
