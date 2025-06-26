import unittest
import asyncio
import time
from unittest.mock import Mock, patch, AsyncMock
import json

# Импорты модулей для тестирования
from config import config_manager
from watchlist_manager import watchlist_manager
from cache_manager import cache_manager
from metrics_manager import metrics_manager
from api_client import api_client
from circuit_breaker import CircuitBreaker, CircuitState
from data_validator import data_validator
from logger import bot_logger

class TestConfigManager(unittest.TestCase):
    """Тесты менеджера конфигурации"""

    def setUp(self):
        self.config = config_manager

    def test_default_values(self):
        """Тест значений по умолчанию"""
        self.assertEqual(self.config.get('VOLUME_THRESHOLD'), 1000)
        self.assertEqual(self.config.get('SPREAD_THRESHOLD'), 0.1)
        self.assertEqual(self.config.get('NATR_THRESHOLD'), 0.5)

    def test_set_get_value(self):
        """Тест установки и получения значений"""
        original_value = self.config.get('VOLUME_THRESHOLD')
        test_value = 2000

        self.config.set('VOLUME_THRESHOLD', test_value)
        self.assertEqual(self.config.get('VOLUME_THRESHOLD'), test_value)

        # Восстанавливаем оригинальное значение
        self.config.set('VOLUME_THRESHOLD', original_value)

class TestWatchlistManager(unittest.TestCase):
    """Тесты менеджера списка отслеживания"""

    def setUp(self):
        self.watchlist = watchlist_manager
        # Очищаем список перед каждым тестом
        self.original_list = self.watchlist.get_all().copy()
        self.watchlist.clear()

    def tearDown(self):
        # Восстанавливаем оригинальный список
        self.watchlist.clear()
        for symbol in self.original_list:
            self.watchlist.add(symbol)

    def test_add_remove_coin(self):
        """Тест добавления и удаления монет"""
        test_symbol = "TEST"

        # Тест добавления
        self.assertTrue(self.watchlist.add(test_symbol))
        self.assertTrue(self.watchlist.contains(test_symbol))
        self.assertEqual(self.watchlist.size(), 1)

        # Тест дублирования
        self.assertFalse(self.watchlist.add(test_symbol))
        self.assertEqual(self.watchlist.size(), 1)

        # Тест удаления
        self.assertTrue(self.watchlist.remove(test_symbol))
        self.assertFalse(self.watchlist.contains(test_symbol))
        self.assertEqual(self.watchlist.size(), 0)

        # Тест удаления несуществующего
        self.assertFalse(self.watchlist.remove("NONEXISTENT"))

    def test_symbol_normalization(self):
        """Тест нормализации символов"""
        test_cases = ["btc", "BTC", "btc_usdt", "BTC_USDT", "btcusdt"]

        for symbol in test_cases:
            self.watchlist.clear()
            self.watchlist.add(symbol)
            self.assertTrue(self.watchlist.contains("BTC"))

class TestCacheManager(unittest.TestCase):
    """Тесты менеджера кеша"""

    def setUp(self):
        self.cache = cache_manager

    def test_cache_operations(self):
        """Тест операций кеша"""
        key = "test_key"
        value = {"test": "data"}

        # Тест установки и получения
        self.cache.set(key, value)
        cached_value = self.cache.get(key)
        self.assertEqual(cached_value, value)

        # Тест несуществующего ключа
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_ticker_cache(self):
        """Тест кеша тикеров"""
        symbol = "BTC"
        ticker_data = {
            "symbol": "BTCUSDT",
            "lastPrice": "50000.00",
            "volume": "1000.00"
        }

        self.cache.set_ticker_cache(symbol, ticker_data)
        cached_data = self.cache.get_ticker_cache(symbol)
        self.assertEqual(cached_data, ticker_data)

class TestCircuitBreaker(unittest.TestCase):
    """Тесты Circuit Breaker"""

    def setUp(self):
        self.cb = CircuitBreaker(failure_threshold=3, timeout=1.0, name="test_cb")

    async def test_circuit_breaker_states(self):
        """Тест состояний Circuit Breaker"""

        # Тест начального состояния
        self.assertEqual(self.cb.state, CircuitState.CLOSED)

        # Тест успешного вызова
        async def success_func():
            return "success"

        result = await self.cb.call(success_func)
        self.assertEqual(result, "success")
        self.assertEqual(self.cb.state, CircuitState.CLOSED)

        # Тест превышения порога ошибок
        async def failing_func():
            raise Exception("Test error")

        for i in range(3):
            with self.assertRaises(Exception):
                await self.cb.call(failing_func)

        self.assertEqual(self.cb.state, CircuitState.OPEN)

        # Тест блокировки вызовов в OPEN состоянии
        with self.assertRaises(Exception):
            await self.cb.call(success_func)

class TestDataValidator(unittest.TestCase):
    """Тесты валидатора данных"""

    def test_validate_coin_data(self):
        """Тест валидации данных монет"""

        # Валидные данные
        valid_data = {
            'symbol': 'BTC',
            'price': 50000.0,
            'volume': 1000.0,
            'change': 5.0,
            'spread': 0.1,
            'natr': 0.5,
            'trades': 100,
            'active': True,
            'has_recent_trades': True,
            'timestamp': time.time()
        }

        self.assertTrue(data_validator.validate_coin_data(valid_data))

        # Невалидные данные - отсутствует обязательное поле
        invalid_data = valid_data.copy()
        del invalid_data['symbol']

        self.assertFalse(data_validator.validate_coin_data(invalid_data))

        # Невалидные данные - неправильный тип
        invalid_data = valid_data.copy()
        invalid_data['price'] = "invalid"

        self.assertFalse(data_validator.validate_coin_data(invalid_data))

class TestMetricsManager(unittest.TestCase):
    """Тесты менеджера метрик"""

    def setUp(self):
        self.metrics = metrics_manager

    def test_api_metrics(self):
        """Тест метрик API"""
        endpoint = "/test"
        response_time = 0.5
        status_code = 200

        self.metrics.record_api_request(endpoint, response_time, status_code)

        stats = self.metrics.get_api_stats()
        self.assertIn(endpoint, stats)
        self.assertEqual(stats[endpoint]['total_requests'], 1)
        self.assertEqual(stats[endpoint]['avg_response_time'], response_time)

    def test_performance_metrics(self):
        """Тест метрик производительности"""
        metric_name = "test_metric"
        value = 100.0

        self.metrics.record_performance_metric(metric_name, value)

        stats = self.metrics.get_performance_stats()
        self.assertIn(metric_name, stats)
        self.assertEqual(stats[metric_name]['current'], value)

class MockAPITests(unittest.TestCase):
    """Тесты API клиента с mock'ами"""

    def setUp(self):
        self.api = api_client

    @patch('aiohttp.ClientSession.get')
    async def test_get_ticker_data_success(self, mock_get):
        """Тест успешного получения данных тикера"""

        # Настраиваем mock
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "symbol": "BTCUSDT",
            "lastPrice": "50000.00",
            "volume": "1000.00"
        })

        mock_get.return_value.__aenter__.return_value = mock_response

        # Тестируем
        result = await self.api.get_ticker_data("BTC")

        self.assertIsNotNone(result)
        self.assertEqual(result['symbol'], "BTCUSDT")
        self.assertEqual(result['lastPrice'], "50000.00")

    @patch('aiohttp.ClientSession.get')
    async def test_get_ticker_data_failure(self, mock_get):
        """Тест обработки ошибки API"""

        # Настраиваем mock для ошибки
        mock_response = AsyncMock()
        mock_response.status = 500

        mock_get.return_value.__aenter__.return_value = mock_response

        # Тестируем
        result = await self.api.get_ticker_data("BTC")

        self.assertIsNone(result)

def run_async_test(coro):
    """Хелпер для запуска async тестов"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# Integration тесты
class TestIntegration(unittest.TestCase):
    """Интеграционные тесты"""

    def test_config_watchlist_integration(self):
        """Тест интеграции конфигурации и списка отслеживания"""
        # Сохраняем оригинальные значения
        original_threshold = config_manager.get('VOLUME_THRESHOLD')
        original_watchlist = watchlist_manager.get_all().copy()

        try:
            # Тестируем изменение конфигурации
            config_manager.set('VOLUME_THRESHOLD', 5000)
            self.assertEqual(config_manager.get('VOLUME_THRESHOLD'), 5000)

            # Тестируем добавление монет
            watchlist_manager.clear()
            watchlist_manager.add("BTC")
            watchlist_manager.add("ETH")

            self.assertEqual(watchlist_manager.size(), 2)
            self.assertTrue(watchlist_manager.contains("BTC"))
            self.assertTrue(watchlist_manager.contains("ETH"))

        finally:
            # Восстанавливаем оригинальные значения
            config_manager.set('VOLUME_THRESHOLD', original_threshold)
            watchlist_manager.clear()
            for symbol in original_watchlist:
                watchlist_manager.add(symbol)

    def test_system_integration(self):
        """Интеграционный тест системы"""
        bot_logger.info("🧪 Запуск интеграционного теста")

        # Сохраняем оригинальные значения
        original_threshold = config_manager.get('VOLUME_THRESHOLD')
        original_watchlist = watchlist_manager.get_all().copy()

        try:
            # Тестируем изменение конфигурации
            config_manager.set('VOLUME_THRESHOLD', 5000)
            self.assertEqual(config_manager.get('VOLUME_THRESHOLD'), 5000)

            # Тестируем добавление монет
            watchlist_manager.clear()
            watchlist_manager.add("BTC")
            watchlist_manager.add("ETH")

            self.assertEqual(watchlist_manager.size(), 2)
            self.assertTrue(watchlist_manager.contains("BTC"))
            self.assertTrue(watchlist_manager.contains("ETH"))

            # Тестируем валидацию данных
            from data_validator import data_validator
            valid_data = {
                'symbol': 'BTC',
                'price': 50000.0,
                'volume': 1000.0,
                'change': 2.5,
                'spread': 0.1,
                'natr': 0.8,
                'trades': 100,
                'active': True
            }
            self.assertTrue(data_validator.validate_coin_data(valid_data))

            # Тестируем кеш
            from cache_manager import cache_manager
            cache_manager.set_price_cache("BTC", 50000.0)
            cached_price = cache_manager.get_price_cache("BTC")
            self.assertEqual(cached_price, 50000.0)

            # Тестируем метрики
            from metrics_manager import metrics_manager
            metrics_manager.record_api_request("/test", 0.5, 200)
            stats = metrics_manager.get_api_stats()
            self.assertIn("/test", stats)

        finally:
            # Восстанавливаем оригинальные значения
            config_manager.set('VOLUME_THRESHOLD', original_threshold)
            watchlist_manager.clear()
            for symbol in original_watchlist:
                watchlist_manager.add(symbol)

    def test_performance_validation(self):
        """Тест валидации производительности"""
        bot_logger.info("🧪 Тест производительности")

        from performance_optimizer import performance_optimizer
        score = performance_optimizer.get_performance_score()
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_alert_system(self):
        """Тест системы алертов"""
        bot_logger.info("🧪 Тест системы алертов")

        from alert_manager import alert_manager
        from advanced_alerts import advanced_alert_manager

        # Тестируем базовые алерты
        alerts = alert_manager.check_system_alerts({
            'memory_percent': 90,
            'cpu_percent': 85,
            'disk_percent': 95
        })
        self.assertGreater(len(alerts), 0)

        # Тестируем продвинутые алерты
        stats = advanced_alert_manager.get_alert_stats()
        self.assertIn('total_alerts', stats)

if __name__ == '__main__':
    # Настройка тестового окружения
    bot_logger.info("🧪 Запуск комплексных тестов торгового бота")

    # Создаем test suite
    suite = unittest.TestSuite()

    # Добавляем тесты
    suite.addTest(unittest.makeSuite(TestConfigManager))
    suite.addTest(unittest.makeSuite(TestWatchlistManager))
    suite.addTest(unittest.makeSuite(TestCacheManager))
    suite.addTest(unittest.makeSuite(TestCircuitBreaker))
    suite.addTest(unittest.makeSuite(TestDataValidator))
    suite.addTest(unittest.makeSuite(TestMetricsManager))
    suite.addTest(unittest.makeSuite(MockAPITests))
    suite.addTest(unittest.makeSuite(TestIntegration))

    # Запускаем тесты
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Выводим результаты
    if result.wasSuccessful():
        bot_logger.info("✅ Все тесты пройдены успешно!")
    else:
        bot_logger.error(f"❌ Тесты провалены: {len(result.failures)} failures, {len(result.errors)} errors")

    print(f"\n🎯 Результаты тестирования:")
    print(f"   Всего тестов: {result.testsRun}")
    print(f"   Успешно: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"   Провалено: {len(result.failures)}")
    print(f"   Ошибок: {len(result.errors)}")