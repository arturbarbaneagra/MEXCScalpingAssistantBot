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
        self.last_request_time = 0
        self.request_count = 0
        self.start_time = time.time()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получает или создает HTTP сессию с правильной конфигурацией"""
        if self.session is None or self.session.closed:
            # Правильная конфигурация таймаутов
            timeout = aiohttp.ClientTimeout(
                total=config_manager.get('API_TIMEOUT', 12),
                connect=5,
                sock_read=10
            )

            # Настройки коннектора для оптимизации
            connector = aiohttp.TCPConnector(
                limit=20,
                limit_per_host=10,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True
            )

            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    'User-Agent': 'TradingBot/2.0',
                    'Accept': 'application/json'
                }
            )
            bot_logger.debug("🔄 HTTP сессия создана")

        return self.session

    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Выполняет HTTP запрос с обработкой ошибок и retry логикой"""
        url = f"{self.base_url}{endpoint}"

        # Rate limiting
        await self._rate_limit()

        max_retries = config_manager.get('MAX_RETRIES', 2)

        for attempt in range(max_retries + 1):
            start_time = time.time()

            try:
                session = await self._get_session()

                # Используем правильный контекстный менеджер для таймаута
                async with session.get(url, params=params) as response:
                    request_time = time.time() - start_time

                    # Логируем запрос
                    bot_logger.api_request("GET", url, response.status, request_time)

                    if response.status == 200:
                        data = await response.json()
                        return data
                    elif response.status == 429:  # Rate limit
                        bot_logger.warning(f"Rate limit hit, waiting...")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        bot_logger.warning(f"API error {response.status} for {endpoint}")
                        return None

            except asyncio.TimeoutError:
                bot_logger.debug(f"Timeout on attempt {attempt + 1} for {endpoint}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                return None
            except aiohttp.ClientError as e:
                bot_logger.debug(f"Client error on attempt {attempt + 1}: {type(e).__name__}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    # Пересоздаем сессию при ошибке
                    await self.close()
                    continue
                return None
            except Exception as e:
                error_msg = str(e)
                # Скрываем частые ошибки timeout context manager
                if "timeout context manager" in error_msg.lower():
                    bot_logger.debug(f"Timeout context error on attempt {attempt + 1}")
                else:
                    bot_logger.debug(f"Request exception on attempt {attempt + 1}: {type(e).__name__}")

                if attempt < max_retries:
                    await asyncio.sleep(1)
                    # Пересоздаем сессию при ошибке
                    await self.close()
                    continue
                return None

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

    async def _rate_limit(self):
        """Реализует rate limiting"""
        interval = time.time() - self.last_request_time
        if interval < 0.1:
            await asyncio.sleep(0.1 - interval)
        self.last_request_time = time.time()

    async def close(self):
        """Правильно закрывает HTTP сессию и коннекторы"""
        if self.session and not self.session.closed:
            try:
                # Закрываем сессию
                await self.session.close()

                # Даем время на завершение всех соединений
                await asyncio.sleep(0.1)

                # Принудительно завершаем event loop если есть незакрытые соединения
                if hasattr(self.session, '_connector') and self.session._connector:
                    await self.session._connector.close()

                bot_logger.debug("HTTP сессия корректно закрыта")
            except Exception as e:
                bot_logger.debug(f"Ошибка при закрытии HTTP сессии: {type(e).__name__}")
            finally:
                self.session = None

# Глобальный экземпляр
api_client = APIClient()