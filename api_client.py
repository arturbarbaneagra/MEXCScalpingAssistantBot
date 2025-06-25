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
        """Получает данные свечи"""
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
            return {
                'open': float(candle[1]),
                'high': float(candle[2]),
                'low': float(candle[3]),
                'close': float(candle[4]),
                'volume': float(candle[5]),
                'timestamp': int(candle[0])
            }
        except (IndexError, ValueError, TypeError) as e:
            bot_logger.error(f"Ошибка парсинга свечи для {symbol}: {e}")
            return None

    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Получает тикер монеты с 1-минутными данными"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"
        
        # Получаем последние 2 завершенные 1-минутные свечи для расчета изменения
        candle_params = {
            'symbol': symbol,
            'interval': '1m',
            'limit': 2  # Последние 2 минуты для расчета изменения
        }

        candle_data = self._make_request('klines', candle_params)
        if not candle_data or not isinstance(candle_data, list) or len(candle_data) == 0:
            bot_logger.warning(f"Нет 1-минутных данных для {symbol}")
            return None

        try:
            # Получаем данные из последней 1-минутной свечи
            current_candle = candle_data[0]
            
            # Проверяем что свеча содержит достаточно данных
            if not isinstance(current_candle, list) or len(current_candle) < 8:
                bot_logger.warning(f"Некорректная структура свечи для {symbol}: {current_candle}")
                return None

            current_open = float(current_candle[1])
            current_high = float(current_candle[2])
            current_low = float(current_candle[3])
            current_close = float(current_candle[4])
            current_volume = float(current_candle[7])  # quoteVolume в USDT

            # Рассчитываем изменение цены за последнюю минуту
            price_change = ((current_close - current_open) / current_open * 100) if current_open > 0 else 0.0

            # Получаем текущие bid/ask цены из orderbook
            ticker_params = {'symbol': symbol}
            ticker_data = self._make_request('ticker/bookTicker', ticker_params)
            
            bid_price = float(ticker_data['bidPrice']) if ticker_data and ticker_data.get('bidPrice') else current_close
            ask_price = float(ticker_data['askPrice']) if ticker_data and ticker_data.get('askPrice') else current_close

            return {
                'price': current_close,  # Цена закрытия последней минуты
                'change': price_change,  # Изменение за последнюю минуту
                'volume': current_volume,  # Объем за последнюю минуту в USDT
                'count': 1,  # Примерное количество сделок (можно улучшить)
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

# Глобальный экземпляр API клиента
api_client = MexcApiClient()