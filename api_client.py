#!/usr/bin/env python3
"""
API клиент для взаимодействия с MEXC
Версия: 2.1 - с улучшенной обработкой ошибок
"""

import aiohttp
import asyncio
import time
from typing import Optional, Dict, Any, List
from logger import bot_logger
from circuit_breaker import api_circuit_breakers
from cache_manager import cache_manager
from api_performance_monitor import api_performance_monitor
from api_recovery_manager import APIRecoveryManager

class APIClient:
    """Клиент для работы с MEXC API"""

    def __init__(self):
        self.base_url = "https://api.mexc.com"
        self.session: Optional[aiohttp.ClientSession] = None
        self.recovery_manager = APIRecoveryManager()
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получает или создает aiohttp сессию"""
        async with self._session_lock:
            if self.session is None or self.session.closed:
                timeout = aiohttp.ClientTimeout(total=10, connect=5)
                connector = aiohttp.TCPConnector(
                    limit=100,
                    limit_per_host=20,
                    keepalive_timeout=30,
                    enable_cleanup_closed=True
                )
                self.session = aiohttp.ClientSession(
                    timeout=timeout,
                    connector=connector,
                    headers={'User-Agent': 'MEXC-Trading-Bot/2.1'}
                )
            return self.session

    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Выполняет HTTP запрос с обработкой ошибок"""
        if params is None:
            params = {}

        url = f"{self.base_url}{endpoint}"
        circuit_breaker = api_circuit_breakers.get('general')

        async def make_call():
            session = await self._get_session()
            start_time = time.time()

            try:
                async with session.get(url, params=params) as response:
                    response_time = time.time() - start_time

                    # Записываем метрики производительности
                    api_performance_monitor.record_request(
                        endpoint, response_time, response.status
                    )

                    if response.status == 200:
                        data = await response.json()
                        # Сохраняем успешные данные для fallback
                        symbol = params.get('symbol', 'unknown')
                        self.recovery_manager.store_successful_data(endpoint, symbol, data)
                        return data
                    else:
                        bot_logger.warning(f"API ошибка {response.status}: {endpoint}")
                        raise aiohttp.ClientError(f"HTTP {response.status}")

            except asyncio.TimeoutError:
                bot_logger.warning(f"Таймаут запроса к {endpoint}")
                raise
            except Exception as e:
                bot_logger.error(f"Ошибка запроса к {endpoint}: {e}")
                raise

        try:
            if circuit_breaker:
                result = await circuit_breaker.call(make_call)
            else:
                result = await make_call()
            return result

        except Exception as e:
            # Попытка получить fallback данные
            symbol = params.get('symbol', 'unknown')
            fallback_data = self.recovery_manager.get_fallback_data(endpoint, symbol)
            if fallback_data:
                bot_logger.info(f"Используем fallback данные для {endpoint}:{symbol}")
                return fallback_data
            return None

    async def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Получает данные тикера"""
        cache_key = f"ticker_{symbol}"
        cached_data = cache_manager.get(cache_key)
        if cached_data:
            return cached_data

        data = await self._make_request("/api/v3/ticker/24hr", {"symbol": symbol})
        if data:
            cache_manager.set(cache_key, data, ttl=5)  # Кеш на 5 секунд
        return data

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> Optional[List]:
        """Получает данные свечей"""
        cache_key = f"klines_{symbol}_{interval}_{limit}"
        cached_data = cache_manager.get(cache_key)
        if cached_data:
            return cached_data

        data = await self._make_request("/api/v3/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        })
        if data:
            cache_manager.set(cache_key, data, ttl=10)  # Кеш на 10 секунд
        return data

    async def get_trades(self, symbol: str, limit: int = 100) -> Optional[List]:
        """Получает данные сделок"""
        cache_key = f"trades_{symbol}_{limit}"
        cached_data = cache_manager.get(cache_key)
        if cached_data:
            return cached_data

        data = await self._make_request("/api/v3/trades", {
            "symbol": symbol,
            "limit": limit
        })
        if data:
            cache_manager.set(cache_key, data, ttl=3)  # Кеш на 3 секунды
        return data

    async def get_order_book(self, symbol: str, limit: int = 100) -> Optional[Dict]:
        """Получает стакан заявок"""
        cache_key = f"depth_{symbol}_{limit}"
        cached_data = cache_manager.get(cache_key)
        if cached_data:
            return cached_data

        data = await self._make_request("/api/v3/depth", {
            "symbol": symbol,
            "limit": limit
        })
        if data:
            cache_manager.set(cache_key, data, ttl=2)  # Кеш на 2 секунды
        return data

    async def test_connection(self) -> bool:
        """Тестирует соединение с API"""
        try:
            # Используем существующий эндпоинт для проверки соединения
            data = await self._make_request('/api/v3/time')
            return data is not None and 'serverTime' in data
        except Exception as e:
            bot_logger.error(f"Ошибка тестирования соединения: {e}")
            return False

    async def close(self):
        """Закрывает сессию"""
        async with self._session_lock:
            if self.session and not self.session.closed:
                try:
                    await self.session.close()
                    # Дополнительная пауза для корректного закрытия соединений
                    await asyncio.sleep(0.1)
                    bot_logger.info("API клиент закрыт")
                except Exception as e:
                    bot_logger.warning(f"Ошибка при закрытии API клиента: {e}")
                finally:
                    self.session = None

# Глобальный экземпляр
api_client = APIClient()