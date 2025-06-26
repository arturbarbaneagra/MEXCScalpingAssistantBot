import asyncio
import time
import aiohttp
from typing import Optional, Dict, List, Any
from logger import bot_logger
from config import config_manager

class APIClient:
    def __init__(self):
        self.base_url = "https://api.mexc.com/api/v3"
        self.session: Optional[aiohttp.ClientSession] = None
        self.session_created_at = 0
        self.session_lifetime = 300  # 5 минут
        self.request_timeout = 10
        self.max_retries = 2

    async def _ensure_session(self):
        """Обеспечивает наличие активной сессии"""
        current_time = time.time()

        if (self.session is None or 
            self.session.closed or 
            current_time - self.session_created_at > self.session_lifetime):

            if self.session and not self.session.closed:
                try:
                    await self.session.close()
                except Exception as e:
                    bot_logger.debug(f"Ошибка закрытия старой сессии: {e}")

            # Создаем новую сессию с правильными настройками
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=50,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=60,
                enable_cleanup_closed=True
            )

            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={'User-Agent': 'TradingBot/2.0'}
            )
            self.session_created_at = current_time
            bot_logger.info("🔄 Новая оптимизированная HTTP сессия создана")

    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Выполняет HTTP запрос с повторными попытками"""
        await self._ensure_session()

        url = f"{self.base_url}{endpoint}"

        for attempt in range(1, self.max_retries + 1):
            try:
                start_time = time.time()

                async with self.session.get(url, params=params) as response:
                    response_time = time.time() - start_time

                    if response.status == 200:
                        data = await response.json()
                        bot_logger.api_request("GET", url, response.status, response_time)
                        return data
                    else:
                        bot_logger.warning(f"API ошибка {response.status} для {endpoint}")
                        if attempt == self.max_retries:
                            return None
                        await asyncio.sleep(1)

            except asyncio.TimeoutError:
                bot_logger.warning(f"Таймаут запроса на попытке {attempt}: {endpoint}")
                if attempt == self.max_retries:
                    return None
                await asyncio.sleep(1)

            except Exception as e:
                error_msg = str(e)
                bot_logger.error(f"Request exception on attempt {attempt}: {error_msg}")

                # Пересоздаем сессию при критических ошибках
                if any(phrase in error_msg.lower() for phrase in [
                    "session is closed", "timeout context manager", 
                    "connection", "ssl", "server disconnected"
                ]):
                    self.session = None
                    await self._ensure_session()

                if attempt == self.max_retries:
                    return None
                await asyncio.sleep(1)

        return None

    async def get_ticker_data(self, symbol: str) -> Optional[Dict]:
        """Получает данные тикера для символа"""
        params = {'symbol': f"{symbol}USDT"}
        return await self._make_request("/ticker/24hr", params)

    async def get_book_ticker(self, symbol: str) -> Optional[Dict]:
        """Получает данные книги ордеров (bid/ask)"""
        params = {'symbol': f"{symbol}USDT"}
        return await self._make_request("/ticker/bookTicker", params)

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 2) -> Optional[List]:
        """Получает данные свечей"""
        params = {
            'symbol': f"{symbol}USDT",
            'interval': interval,
            'limit': limit
        }
        return await self._make_request("/klines", params)

    async def get_multiple_tickers_batch(self, symbols: List[str]) -> Dict[str, Optional[Dict]]:
        """Получает данные тикеров для списка символов (оптимизированная версия)"""
        results = {}

        # Получаем все тикеры одним запросом
        try:
            all_tickers = await self._make_request("/ticker/24hr")
            if all_tickers:
                # Создаем индекс по символам
                ticker_dict = {ticker['symbol'].replace('USDT', ''): ticker 
                             for ticker in all_tickers 
                             if ticker['symbol'].endswith('USDT')}

                # Заполняем результаты
                for symbol in symbols:
                    results[symbol] = ticker_dict.get(symbol)
            else:
                # Fallback - запрашиваем по одному
                for symbol in symbols:
                    results[symbol] = await self.get_ticker_data(symbol)
        except Exception as e:
            bot_logger.error(f"Ошибка batch запроса тикеров: {e}")
            # Fallback - запрашиваем по одному
            for symbol in symbols:
                try:
                    results[symbol] = await self.get_ticker_data(symbol)
                except Exception as sym_e:
                    bot_logger.error(f"Ошибка получения тикера {symbol}: {sym_e}")
                    results[symbol] = None

        return results

    def _calculate_natr(self, klines: List) -> float:
        """Вычисляет NATR (Normalized Average True Range)"""
        if not klines or len(klines) < 2:
            return 0.0

        try:
            # klines формат: [timestamp, open, high, low, close, volume, ...]
            current = klines[-1]  # Последняя свеча
            previous = klines[-2]  # Предыдущая свеча

            high = float(current[2])
            low = float(current[3])
            prev_close = float(previous[4])
            close = float(current[4])

            # True Range
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            true_range = max(tr1, tr2, tr3)

            # Normalized по цене закрытия
            if close > 0:
                natr = (true_range / close) * 100
                return round(natr, 2)
            return 0.0
        except (IndexError, ValueError, TypeError):
            return 0.0

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> Optional[List]:
        """Получает последние сделки для символа"""
        params = {
            'symbol': f"{symbol}USDT",
            'limit': limit
        }
        return await self._make_request("/trades", params)

    async def get_trades_last_minute(self, symbol: str) -> int:
        """Получает количество сделок за последнюю минуту"""
        try:
            # Получаем последние сделки
            trades = await self.get_recent_trades(symbol, 1000)
            if not trades:
                return 0

            current_time = time.time() * 1000  # в миллисекундах
            minute_ago = current_time - 60000  # 60 секунд назад

            # Считаем сделки за последнюю минуту
            trades_count = 0
            for trade in trades:
                if isinstance(trade, dict) and 'time' in trade:
                    trade_time = int(trade['time'])
                    if trade_time >= minute_ago:
                        trades_count += 1
                    else:
                        # Сделки идут по убыванию времени, можно прерывать
                        break

            bot_logger.debug(f"{symbol}: Сделок за последнюю минуту: {trades_count}")
            return trades_count

        except Exception as e:
            bot_logger.error(f"Ошибка получения сделок для {symbol}: {e}")
            return 0
    
    async def get_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает полные данные по монете для анализа (только 1-минутные данные)"""
        try:
            # Получаем все необходимые данные параллельно
            tasks = [
                self.get_ticker_data(symbol),  # Только для текущей цены
                self.get_book_ticker(symbol),
                self.get_klines(symbol, "1m", 2),
                self.get_trades_last_minute(symbol)
            ]

            ticker_data, book_data, klines_data, trades_1m = await asyncio.gather(*tasks, return_exceptions=True)

            # Проверяем результаты
            if isinstance(ticker_data, Exception) or not ticker_data:
                bot_logger.warning(f"Нет данных цены для {symbol}")
                return None

            if isinstance(book_data, Exception) or not book_data:
                bot_logger.warning(f"Нет book ticker для {symbol}")
                return None

            if isinstance(klines_data, Exception) or not klines_data:
                bot_logger.warning(f"Нет 1м klines для {symbol}")
                volume_1m_usdt = 0
                change_1m = 0
                natr = 0
            else:
                # Получаем объём за 1 минуту из klines
                last_candle = klines_data[-1] if klines_data else None
                if last_candle:
                    volume_1m_usdt = float(last_candle[7])  # quoteAssetVolume - оборот в USDT
                    open_price = float(last_candle[1])
                    close_price = float(last_candle[4])
                    high_price = float(last_candle[2])
                    low_price = float(last_candle[3])

                    # Рассчитываем изменение за 1 минуту
                    if open_price > 0:
                        change_1m = ((close_price - open_price) / open_price) * 100
                    else:
                        change_1m = 0

                    # Рассчитываем NATR за 1 минуту
                    if open_price > 0:
                        true_range = max(
                            high_price - low_price,
                            abs(high_price - open_price),
                            abs(low_price - open_price)
                        )
                        natr = (true_range / open_price) * 100
                    else:
                        natr = 0
                else:
                    volume_1m_usdt = 0
                    change_1m = 0
                    natr = 0

            bot_logger.info(f"{symbol}: 1m volume={volume_1m_usdt:.2f} USDT из klines")

            # Получаем количество сделок за 1 минуту
            trades_count = 0
            if not isinstance(trades_1m, Exception) and isinstance(trades_1m, int):
                trades_count = trades_1m

            bot_logger.info(f"{symbol}: Точные данные - volume={volume_1m_usdt:.2f} USDT, trades={trades_count}")

            # Рассчитываем спред
            bid_price = float(book_data['bidPrice'])
            ask_price = float(book_data['askPrice'])
            mid_price = (bid_price + ask_price) / 2
            spread = ((ask_price - bid_price) / mid_price) * 100 if mid_price > 0 else 0

            # Проверяем активность только по 1-минутным данным
            vol_thresh = config_manager.get('VOLUME_THRESHOLD')
            spread_thresh = config_manager.get('SPREAD_THRESHOLD')
            natr_thresh = config_manager.get('NATR_THRESHOLD')

            is_active = (
                volume_1m_usdt >= vol_thresh and
                spread >= spread_thresh and
                natr >= natr_thresh
            )

            # Проверяем наличие недавних сделок (за последние 60 секунд)
            has_recent_trades = trades_count > 0

            price = float(ticker_data['lastPrice']) if ticker_data else 0

            return {
                'symbol': symbol,
                'price': price,
                'volume': volume_1m_usdt,  # 1-минутный оборот в USDT
                'change': change_1m,  # 1-минутное изменение
                'spread': spread,
                'natr': natr,  # 1-минутный NATR
                'trades': trades_count,  # Количество сделок за 1 минуту
                'active': is_active,
                'has_recent_trades': has_recent_trades,
                'timestamp': time.time()
            }

        except Exception as e:
            bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
            return None

    async def close(self):
        """Закрывает сессию правильно"""
        if self.session and not self.session.closed:
            try:
                await self.session.close()
                # Даем время на завершение всех соединений
                await asyncio.sleep(0.1)
                bot_logger.debug("API сессия закрыта")
            except Exception as e:
                bot_logger.debug(f"Ошибка закрытия API сессии: {e}")
        self.session = None

# Глобальный экземпляр
api_client = APIClient()