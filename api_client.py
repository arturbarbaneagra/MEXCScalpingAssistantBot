import time
import requests
from typing import Optional, Dict
from logger import bot_logger
from config import config_manager

class MexcApiClient:
    def __init__(self):
        self.base_url = "https://api.mexc.com/api/v3"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'TradingBot/2.0',
            'Content-Type': 'application/json'
        })
        self.last_request_time = 0
        self.request_count = 0
        self.rate_limit_window = 1.0  # 1 секунда

    def _rate_limit(self):
        """Ограничение скорости API запросов"""
        current_time = time.time()

        # Сброс счетчика каждую секунду
        if current_time - self.last_request_time >= self.rate_limit_window:
            self.request_count = 0
            self.last_request_time = current_time

        # Ограничиваем количество запросов в секунду
        max_requests = config_manager.get('MAX_API_REQUESTS_PER_SECOND')
        if self.request_count >= max_requests:
            sleep_time = self.rate_limit_window - (current_time - self.last_request_time)
            if sleep_time > 0:
                time.sleep(sleep_time)
                self.request_count = 0
                self.last_request_time = time.time()

        self.request_count += 1

    def _make_request(self, endpoint: str, params: Dict = None, timeout: int = None) -> Optional[Dict]:
        """Выполняет HTTP запрос с повторными попытками"""
        if timeout is None:
            timeout = config_manager.get('API_TIMEOUT')

        max_retries = config_manager.get('MAX_RETRIES')
        url = f"{self.base_url}/{endpoint}"

        for attempt in range(max_retries):
            try:
                self._rate_limit()

                start_time = time.time()
                response = self.session.get(url, params=params, timeout=timeout)
                response_time = time.time() - start_time

                bot_logger.api_request('GET', url, response.status_code, response_time)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:  # Rate limit
                    wait_time = 2 ** attempt
                    bot_logger.warning(f"Rate limit hit, waiting {wait_time}s")
                    time.sleep(wait_time)
                    continue
                else:
                    bot_logger.error(f"API error {response.status_code}: {response.text}")
                    if attempt == max_retries - 1:
                        return None

            except requests.exceptions.RequestException as e:
                bot_logger.error(f"Request exception on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    return None
                time.sleep(2 ** attempt)

        return None

    def get_candle(self, symbol: str, interval: str = '1m') -> Optional[Dict]:
        """Получает данные свечи согласно документации MEXC"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': 1
        }

        data = self._make_request('klines', params)
        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        try:
            candle = data[0]
            # Структура согласно MEXC API документации:
            # [Open time, Open, High, Low, Close, Volume, Close time, Quote asset volume]
            if len(candle) < 8:
                bot_logger.warning(f"Некорректная структура свечи для {symbol}: {len(candle)} полей")
                return None

            return {
                'open_time': int(candle[0]),      # Index 0: Open time
                'open': float(candle[1]),         # Index 1: Open
                'high': float(candle[2]),         # Index 2: High
                'low': float(candle[3]),          # Index 3: Low  
                'close': float(candle[4]),        # Index 4: Close
                'volume': float(candle[5]),       # Index 5: Volume
                'close_time': int(candle[6]),     # Index 6: Close time
                'quote_volume': float(candle[7]), # Index 7: Quote asset volume
                'timestamp': int(candle[0])       # Для обратной совместимости
            }
        except (IndexError, ValueError, TypeError) as e:
            bot_logger.error(f"Ошибка парсинга свечи для {symbol}: {e}")
            return None

    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Получает тикер монеты с 1-минутными данными согласно MEXC API"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"

        # Получаем последние 3 завершенные 1-минутные свечи согласно документации MEXC
        candle_params = {
            'symbol': symbol,
            'interval': '1m',
            'limit': 3
        }

        candle_data = self._make_request('klines', candle_params)
        if not candle_data or not isinstance(candle_data, list) or len(candle_data) < 2:
            bot_logger.warning(f"Недостаточно 1-минутных данных для {symbol}")
            return None

        try:
            # Проверяем каждую свечу на корректность данных
            valid_candles = []
            for candle in candle_data:
                if (isinstance(candle, list) and len(candle) >= 8 and 
                    all(item is not None for item in candle[:8])):
                    valid_candles.append(candle)
            
            if len(valid_candles) < 2:
                bot_logger.warning(f"Недостаточно валидных свечей для {symbol}")
                return None

            # Сортируем по времени (самая новая свеча - последняя)
            valid_candles.sort(key=lambda x: int(x[0]) if x[0] is not None else 0)

            # Берем две последние завершенные свечи
            previous_candle = valid_candles[-2]  # Предыдущая минута
            current_candle = valid_candles[-1]   # Последняя завершенная минута

            # Проверяем структуру данных
            if not isinstance(current_candle, list) or len(current_candle) < 8:
                bot_logger.warning(f"Некорректная структура свечи для {symbol}")
                return None

            # Безопасное извлечение данных с проверкой на None
            current_close = float(current_candle[4]) if current_candle[4] is not None else 0.0
            current_volume = float(current_candle[5]) if current_candle[5] is not None else 0.0
            quote_volume = float(current_candle[7]) if current_candle[7] is not None else 0.0
            previous_close = float(previous_candle[4]) if previous_candle[4] is not None else 0.0

            if current_close == 0.0:
                bot_logger.warning(f"Нулевая цена для {symbol}")
                return None

            # Рассчитываем изменение цены за последнюю минуту
            price_change = ((current_close - previous_close) / previous_close * 100) if previous_close > 0 else 0.0

            # Получаем текущие bid/ask цены из orderbook
            ticker_params = {'symbol': symbol}
            ticker_data = self._make_request('ticker/bookTicker', ticker_params)

            bid_price = current_close
            ask_price = current_close
            
            if ticker_data and isinstance(ticker_data, dict):
                try:
                    if ticker_data.get('bidPrice'):
                        bid_price = float(ticker_data['bidPrice'])
                    if ticker_data.get('askPrice'):
                        ask_price = float(ticker_data['askPrice'])
                except (ValueError, TypeError):
                    pass  # Используем current_close как fallback

            # Получаем количество сделок за последнюю минуту через trades endpoint
            trade_count = self._get_1min_trades_count(symbol)

            # Рассчитываем объем в базовой валюте для 1-минутного периода
            volume_usdt = current_volume * current_close if current_volume > 0 else 0.0

            bot_logger.debug(f"{symbol}: price={current_close:.6f}, 1m_change={price_change:+.2f}%, volume=${volume_usdt:,.0f}")

            return {
                'price': current_close,
                'change': price_change,  # 1-минутное изменение
                'volume': volume_usdt,  # Объем в USDT за 1 минуту
                'count': trade_count,  # Количество сделок за 24ч (для скальпинга)
                'bid': bid_price,
                'ask': ask_price
            }
        except (KeyError, ValueError, TypeError, IndexError) as e:
            bot_logger.warning(f"Ошибка обработки 1-минутных данных для {symbol}: {e}")
            return None

    def get_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает полные данные монеты"""
        try:
            # Получаем данные тикера и свечи параллельно
            ticker_data = self.get_ticker(symbol)
            if not ticker_data:
                return None

            candle_data = self.get_candle(symbol)
            if not candle_data:
                # Если свеча недоступна, используем данные из тикера
                candle_data = {
                    'high': ticker_data['price'],
                    'low': ticker_data['price'],
                    'close': ticker_data['price']
                }

            # Рассчитываем метрики
            volume = ticker_data['volume']
            spread = self._calculate_spread(ticker_data['bid'], ticker_data['ask'])
            natr = self._calculate_natr(candle_data['high'], candle_data['low'], candle_data['close'])

            # Проверяем критерии активности
            volume_threshold = config_manager.get('VOLUME_THRESHOLD')
            spread_threshold = config_manager.get('SPREAD_THRESHOLD')
            natr_threshold = config_manager.get('NATR_THRESHOLD')

            is_active = (
                volume >= volume_threshold and
                spread >= spread_threshold and
                natr >= natr_threshold
            )

            result = {
                'symbol': symbol,
                'price': ticker_data['price'],
                'change': ticker_data['change'],
                'volume': volume,
                'trades': ticker_data['count'],
                'spread': spread,
                'natr': natr,
                'active': is_active,
                'timestamp': time.time()
            }

            return result

        except Exception as e:
            bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
            return None

    def _calculate_spread(self, bid: float, ask: float) -> float:
        """Рассчитывает спред в процентах"""
        if bid <= 0 or ask <= 0:
            return 0.0
        return ((ask - bid) / bid) * 100

    def _calculate_natr(self, high: float, low: float, close: float) -> float:
        """Рассчитывает нормализованный ATR"""
        if close <= 0:
            return 0.0
        true_range = high - low
        return (true_range / close) * 100

    def _get_1min_trades_count(self, symbol: str) -> int:
        """Получает количество сделок за последнюю минуту"""
        try:
            # Получаем последние сделки (максимум 1000)
            trades_data = self._make_request('trades', {'symbol': symbol, 'limit': 1000})
            
            if not trades_data or not isinstance(trades_data, list):
                bot_logger.debug(f"Нет данных о сделках для {symbol}")
                return 0

            # Вычисляем временной диапазон (последняя минута)
            now = int(time.time() * 1000)  # Текущее время в миллисекундах
            one_minute_ago = now - 60000   # 60 секунд назад

            # Фильтруем сделки за последнюю минуту
            recent_trades = []
            for trade in trades_data:
                if isinstance(trade, dict) and trade.get('time'):
                    try:
                        trade_time = int(trade['time'])
                        if trade_time >= one_minute_ago:
                            recent_trades.append(trade)
                    except (ValueError, TypeError):
                        continue

            trades_count = len(recent_trades)
            bot_logger.debug(f"{symbol}: {trades_count} сделок за последнюю минуту")
            return trades_count

        except Exception as e:
            bot_logger.warning(f"Ошибка получения 1м сделок для {symbol}: {e}")
            return 0

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> Optional[Dict]:
        """Получает данные свечей для символа (1-минутные данные для скальпинга)"""
        try:
            endpoint = "/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }

            url = f"{self.base_url}{endpoint}"
            start_time = time.time()

            response = requests.get(
                url,
                params=params,
                timeout=config_manager.get('API_TIMEOUT', 15)
            )

            response_time = time.time() - start_time

            # Безопасное логирование без чувствительных данных
            safe_url = url.split('?')[0]  # Убираем параметры из URL
            bot_logger.api_request("GET", safe_url, response.status_code, response_time)

            if response.status_code == 200:
                data = response.json()
                if data:
                    # Форматируем данные свечи согласно документации MEXC
                    klines = []
                    for item in data:
                        kline = {
                            'open_time': item[0],           # Index 0: Open time
                            'open': float(item[1]),         # Index 1: Open
                            'high': float(item[2]),         # Index 2: High
                            'low': float(item[3]),          # Index 3: Low
                            'close': float(item[4]),        # Index 4: Close
                            'volume': float(item[5]),       # Index 5: Volume
                            'close_time': item[6],          # Index 6: Close time
                            'quote_volume': float(item[7])  # Index 7: Quote asset volume
                        }
                        klines.append(kline)

                    return {'klines': klines}

            return None

        except Exception as e:
            bot_logger.error(f"Ошибка получения klines для {symbol}: {e}")
            return None

    def get_ticker_24hr(self, symbol: str = None) -> Optional[Dict]:
        """Получает статистику за 24 часа (используется для получения базовой информации)"""
        try:
            endpoint = "/api/v3/ticker/24hr"
            params = {}
            if symbol:
                params['symbol'] = symbol

            url = f"{self.base_url}{endpoint}"
            start_time = time.time()

            response = requests.get(
                url,
                params=params,
                timeout=config_manager.get('API_TIMEOUT', 15)
            )

            response_time = time.time() - start_time

            # Безопасное логирование
            safe_url = url.split('?')[0]
            bot_logger.api_request("GET", safe_url, response.status_code, response_time)

            if response.status_code == 200:
                return response.json()

            return None

        except Exception as e:
            bot_logger.error(f"Ошибка получения ticker 24hr: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Получает текущую цену для символа"""
        try:
            endpoint = "/api/v3/ticker/price"
            params = {'symbol': symbol}

            url = f"{self.base_url}{endpoint}"
            start_time = time.time()

            response = requests.get(
                url,
                params=params,
                timeout=config_manager.get('API_TIMEOUT', 15)
            )

            response_time = time.time() - start_time

            # Безопасное логирование
            safe_url = url.split('?')[0]
            bot_logger.api_request("GET", safe_url, response.status_code, response_time)

            if response.status_code == 200:
                data = response.json()
                return float(data.get('price', 0))

            return None

        except Exception as e:
            bot_logger.error(f"Ошибка получения цены для {symbol}: {e}")
            return None

# Глобальный экземпляр API клиента
api_client = MexcApiClient()