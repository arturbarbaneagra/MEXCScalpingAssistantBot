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

    async def get_recent_trades(self, symbol: str, limit: int = 5) -> Optional[List]:
        """Получает последние сделки для символа"""
        params = {
            'symbol': f"{symbol}USDT",
            'limit': limit
        }
        return await self._make_request("/trades", params)

    async def get_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает полные данные монеты включая количество сделок и оборот за 1 минуту"""
        try:
            # Параллельно получаем все необходимые данные
            ticker_task = self.get_ticker_data(symbol)
            book_task = self.get_book_ticker(symbol)
            klines_task = self.get_klines(symbol, "1m", 2)  # Получаем 2 последние минутные свечи
            trades_task = self.get_recent_trades(symbol, 10)  # Получаем больше сделок для проверки активности

            ticker_data, book_data, klines_data, trades_data = await asyncio.gather(
                ticker_task, book_task, klines_task, trades_task, return_exceptions=True
            )

            # Проверяем результаты
            if isinstance(ticker_data, Exception) or not ticker_data:
                bot_logger.debug(f"Ошибка получения ticker для {symbol}: {ticker_data}")
                return None
            if isinstance(book_data, Exception) or not book_data:
                bot_logger.debug(f"Ошибка получения book для {symbol}: {book_data}")
                return None
            if isinstance(klines_data, Exception) or not klines_data:
                bot_logger.debug(f"Ошибка получения klines для {symbol}: {klines_data}")
                return None

            # Извлекаем основные данные
            price = float(ticker_data['lastPrice'])
            change_24h = float(ticker_data['priceChangePercent'])
            
            # Получаем данные из последней минутной свечи
            volume_1m_usdt = 0.0  # Оборот в USDT за 1 минуту
            trades_1m = 0        # Количество сделок за 1 минуту
            
            # Детальная отладка klines данных
            if klines_data and isinstance(klines_data, list) and len(klines_data) > 0:
                last_candle = klines_data[-1]
                bot_logger.debug(f"{symbol} kline структура: {last_candle[:10] if len(last_candle) > 10 else last_candle}")
                
                try:
                    # Структура kline согласно MEXC API:
                    # [timestamp, open, high, low, close, volume, close_time, quote_volume, count, taker_buy_base_asset_volume, taker_buy_quote_asset_volume]
                    if len(last_candle) >= 9:
                        volume_1m_usdt = float(last_candle[7])  # quote_volume (оборот в USDT)
                        trades_1m = int(float(last_candle[8]))  # count (количество сделок)
                        
                        bot_logger.info(f"{symbol}: 1m volume={volume_1m_usdt:.2f} USDT, trades={trades_1m}, candle_len={len(last_candle)}")
                    else:
                        bot_logger.warning(f"{symbol}: Недостаточно данных в kline: {len(last_candle)} элементов")
                        
                except (ValueError, TypeError, IndexError) as e:
                    bot_logger.error(f"Ошибка парсинга kline для {symbol}: {e}, данные: {last_candle}")
                    volume_1m_usdt = 0.0
                    trades_1m = 0
            else:
                bot_logger.warning(f"{symbol}: Нет klines данных")

            # Fallback расчет если основные данные не получены
            if volume_1m_usdt == 0 and ticker_data.get('quoteVolume'):
                try:
                    volume_24h = float(ticker_data['quoteVolume'])
                    volume_1m_usdt = volume_24h / 1440  # Приблизительно
                    bot_logger.debug(f"{symbol}: Fallback volume from 24h: {volume_1m_usdt:.2f}")
                except (ValueError, TypeError):
                    volume_1m_usdt = 0.0

            if trades_1m == 0 and ticker_data.get('count'):
                try:
                    trades_24h = int(float(ticker_data['count']))
                    trades_1m = max(1, trades_24h // 1440)  # Минимум 1 если есть торговля
                    bot_logger.debug(f"{symbol}: Fallback trades from 24h: {trades_1m}")
                except (ValueError, TypeError):
                    trades_1m = 0

            # Получаем bid/ask для расчета спреда
            bid_price = float(book_data['bidPrice'])
            ask_price = float(book_data['askPrice'])

            # Вычисляем спред
            spread = ((ask_price - bid_price) / bid_price) * 100 if bid_price > 0 else 0

            # Вычисляем NATR
            natr = self._calculate_natr(klines_data) if klines_data else 0.0

            # Проверяем недавние сделки из trades endpoint
            has_recent_trades = False
            recent_trades_count = 0
            if trades_data and isinstance(trades_data, list):
                current_time = time.time() * 1000
                for trade in trades_data:
                    if isinstance(trade, dict) and 'time' in trade:
                        trade_time = int(trade['time'])
                        if current_time - trade_time < 120000:  # 2 минуты
                            recent_trades_count += 1
                has_recent_trades = recent_trades_count >= 2
                bot_logger.debug(f"{symbol}: Recent trades in 2min: {recent_trades_count}")

            # Определяем активность на основе фильтров
            from config import config_manager
            vol_threshold = config_manager.get('VOLUME_THRESHOLD')
            spread_threshold = config_manager.get('SPREAD_THRESHOLD')
            natr_threshold = config_manager.get('NATR_THRESHOLD')

            is_active = (
                volume_1m_usdt >= vol_threshold and
                spread >= spread_threshold and
                natr >= natr_threshold and
                trades_1m > 0
            )

            result = {
                'symbol': symbol,
                'price': price,
                'change': change_24h,
                'volume': volume_1m_usdt,
                'trades': trades_1m,
                'spread': round(spread, 2),
                'natr': natr,
                'active': is_active,
                'has_recent_trades': has_recent_trades,
                'timestamp': time.time()
            }
            
            bot_logger.debug(f"{symbol} итоговые данные: volume={volume_1m_usdt:.2f}, trades={trades_1m}, active={is_active}")
            return result

        except Exception as e:
            bot_logger.error(f"Критическая ошибка получения данных для {symbol}: {e}", exc_info=True)
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