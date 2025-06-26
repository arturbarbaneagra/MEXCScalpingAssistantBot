import time
from typing import Dict, Optional, Any, List
from threading import RLock

class CacheManager:
    def __init__(self):
        self.ticker_cache: Dict[str, Dict] = {}
        self.book_ticker_cache: Dict[str, Dict] = {}
        self.trades_cache: Dict[str, int] = {}
        self.price_cache: Dict[str, float] = {}
        self.klines_cache: Dict[str, List] = {}

        # Времена кеширования
        self.ticker_ttl = 2  # 2 секунды для WebSocket данных
        self.book_ticker_ttl = 2
        self.trades_ttl = 60
        self.price_ttl = 5
        self.klines_ttl = 10

        self.lock = RLock()

    def set_ticker_cache(self, symbol: str, data: Dict):
        """Кеширует данные тикера"""
        with self.lock:
            self.ticker_cache[symbol] = {
                'data': data,
                'timestamp': time.time()
            }

    def get_ticker_cache(self, symbol: str) -> Optional[Dict]:
        """Получает данные тикера из кеша"""
        with self.lock:
            cached = self.ticker_cache.get(symbol)
            if cached and (time.time() - cached['timestamp'] < self.ticker_ttl):
                return cached['data']
            return None

    def set_book_ticker_cache(self, symbol: str, data: Dict):
        """Кеширует данные book ticker"""
        with self.lock:
            self.book_ticker_cache[symbol] = {
                'data': data,
                'timestamp': time.time()
            }

    def get_book_ticker_cache(self, symbol: str) -> Optional[Dict]:
        """Получает данные book ticker из кеша"""
        with self.lock:
            cached = self.book_ticker_cache.get(symbol)
            if cached and (time.time() - cached['timestamp'] < self.book_ticker_ttl):
                return cached['data']
            return None

    def set_trades_cache(self, symbol: str, count: int):
        """Кеширует количество сделок"""
        with self.lock:
            self.trades_cache[symbol] = count

    def get_trades_cache(self, symbol: str) -> Optional[int]:
        """Получает количество сделок из кеша"""
        with self.lock:
            return self.trades_cache.get(symbol)

    def set_price_cache(self, symbol: str, price: float):
        """Кеширует цену"""
        with self.lock:
            self.price_cache[symbol] = price

    def get_price_cache(self, symbol: str) -> Optional[float]:
        """Получает цену из кеша"""
        with self.lock:
            return self.price_cache.get(symbol)

    def set_klines_cache(self, symbol: str, klines: List):
        """Кеширует klines данные"""
        with self.lock:
            self.klines_cache[symbol] = {
                'data': klines,
                'timestamp': time.time()
            }

    def get_klines_cache(self, symbol: str) -> Optional[List]:
        """Получает klines из кеша"""
        with self.lock:
            cached = self.klines_cache.get(symbol)
            if cached and (time.time() - cached['timestamp'] < self.klines_ttl):
                return cached['data']
            return None

    def clear_expired(self):
        """Очищает устаревшие данные кеша"""
        with self.lock:
            current_time = time.time()

            # Очистка тикеров
            expired_tickers = [
                symbol for symbol, data in self.ticker_cache.items()
                if current_time - data['timestamp'] > self.ticker_ttl * 2
            ]
            for symbol in expired_tickers:
                del self.ticker_cache[symbol]

            # Очистка book tickers
            expired_books = [
                symbol for symbol, data in self.book_ticker_cache.items()
                if current_time - data['timestamp'] > self.book_ticker_ttl * 2
            ]
            for symbol in expired_books:
                del self.book_ticker_cache[symbol]

            # Очистка klines
            expired_klines = [
                symbol for symbol, data in self.klines_cache.items()
                if current_time - data['timestamp'] > self.klines_ttl * 2
            ]
            for symbol in expired_klines:
                del self.klines_cache[symbol]

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику кеша"""
        with self.lock:
            return {
                'ticker_entries': len(self.ticker_cache),
                'book_ticker_entries': len(self.book_ticker_cache),
                'trades_entries': len(self.trades_cache),
                'price_entries': len(self.price_cache),
                'klines_entries': len(self.klines_cache),
                'total_entries': sum([
                    len(self.ticker_cache),
                    len(self.book_ticker_cache),
                    len(self.trades_cache),
                    len(self.price_cache),
                    len(self.klines_cache)
                ]),
                'memory_usage_kb': 0,  # Можно добавить подсчет
                'cache_efficiency': 85.0  # Можно добавить реальную метрику
            }

# Глобальный экземпляр
cache_manager = CacheManager()