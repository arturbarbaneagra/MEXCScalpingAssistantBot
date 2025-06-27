
#!/usr/bin/env python3
"""
Комплексная система тестирования MEXCScalping Assistant
Версия: 2.1 - Полное покрытие
"""

import unittest
import asyncio
import time
import json
import tempfile
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock

# Импорты всех модулей для тестирования
from config import config_manager
from watchlist_manager import watchlist_manager
from cache_manager import cache_manager
from metrics_manager import metrics_manager
from api_client import api_client
from circuit_breaker import CircuitBreaker, CircuitState
from data_validator import data_validator
from logger import bot_logger
from alert_manager import alert_manager
from performance_optimizer import performance_optimizer
from auto_maintenance import auto_maintenance

class TestConfigManager(unittest.TestCase):
    """Тесты менеджера конфигурации"""

    def setUp(self):
        self.config = config_manager
        self.original_config = self.config.get_all().copy()

    def tearDown(self):
        # Восстанавливаем оригинальную конфигурацию
        for key, value in self.original_config.items():
            self.config.set(key, value)

    def test_default_values(self):
        """Тест значений по умолчанию"""
        self.assertGreater(self.config.get('VOLUME_THRESHOLD'), 0)
        self.assertGreater(self.config.get('SPREAD_THRESHOLD'), 0)
        self.assertGreater(self.config.get('NATR_THRESHOLD'), 0)

    def test_set_get_value(self):
        """Тест установки и получения значений"""
        test_key = 'TEST_VALUE'
        test_value = 12345
        
        self.config.set(test_key, test_value)
        self.assertEqual(self.config.get(test_key), test_value)

    def test_config_persistence(self):
        """Тест сохранения конфигурации"""
        original_volume = self.config.get('VOLUME_THRESHOLD')
        new_volume = original_volume + 1000
        
        self.config.set('VOLUME_THRESHOLD', new_volume)
        self.assertEqual(self.config.get('VOLUME_THRESHOLD'), new_volume)

class TestWatchlistManager(unittest.TestCase):
    """Тесты менеджера списка отслеживания"""

    def setUp(self):
        self.watchlist = watchlist_manager
        self.original_list = self.watchlist.get_all().copy()
        self.watchlist.clear()

    def tearDown(self):
        self.watchlist.clear()
        for symbol in self.original_list:
            self.watchlist.add(symbol)

    def test_add_remove_coin(self):
        """Тест добавления и удаления монет"""
        test_symbol = "TESTCOIN"

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

    def test_symbol_normalization(self):
        """Тест нормализации символов"""
        test_cases = ["btc", "BTC", "btc_usdt", "BTC_USDT", "btcusdt"]

        for symbol in test_cases:
            self.watchlist.clear()
            self.watchlist.add(symbol)
            self.assertTrue(self.watchlist.contains("BTC"))

    def test_bulk_operations(self):
        """Тест массовых операций"""
        symbols = ["BTC", "ETH", "ADA", "SOL", "DOT"]
        
        for symbol in symbols:
            self.watchlist.add(symbol)
        
        self.assertEqual(self.watchlist.size(), len(symbols))
        
        for symbol in symbols:
            self.assertTrue(self.watchlist.contains(symbol))

class TestCacheManager(unittest.TestCase):
    """Тесты менеджера кеша"""

    def setUp(self):
        self.cache = cache_manager

    def test_cache_operations(self):
        """Тест операций кеша"""
        key = "test_key"
        value = {"test": "data"}

        self.cache.set(key, value)
        cached_value = self.cache.get(key)
        self.assertEqual(cached_value, value)

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

    def test_cache_expiration(self):
        """Тест истечения срока кеша"""
        symbol = "TEST"
        data = {"price": 100}
        
        self.cache.set_price_cache(symbol, 100.0)
        
        # Принудительно устанавливаем старое время
        cache_key = f"price_{symbol}"
        self.cache.cache_timestamps[cache_key] = time.time() - 10
        
        # Должно вернуть None из-за истечения срока
        result = self.cache.get_price_cache(symbol)
        self.assertIsNone(result)

    def test_clear_expired(self):
        """Тест очистки устаревших записей"""
        symbol = "TEST"
        self.cache.set_price_cache(symbol, 100.0)
        
        # Устанавливаем старое время
        cache_key = f"price_{symbol}"
        self.cache.cache_timestamps[cache_key] = time.time() - 10
        
        self.cache.clear_expired()
        
        # Запись должна быть удалена
        self.assertNotIn(cache_key, self.cache.cache_timestamps)
        self.assertNotIn(symbol, self.cache.price_cache)

class TestDataValidator(unittest.TestCase):
    """Тесты валидатора данных"""

    def test_validate_coin_data(self):
        """Тест валидации данных монет"""
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

        # Тест с отсутствующим полем
        invalid_data = valid_data.copy()
        del invalid_data['symbol']
        self.assertFalse(data_validator.validate_coin_data(invalid_data))

        # Тест с неправильным типом
        invalid_data = valid_data.copy()
        invalid_data['price'] = "invalid"
        self.assertFalse(data_validator.validate_coin_data(invalid_data))

    def test_validate_symbol(self):
        """Тест валидации символов"""
        self.assertTrue(data_validator.validate_symbol("BTC"))
        self.assertTrue(data_validator.validate_symbol("BTCUSDT"))
        self.assertTrue(data_validator.validate_symbol("BTC_USDT"))
        
        self.assertFalse(data_validator.validate_symbol(""))
        self.assertFalse(data_validator.validate_symbol("A"))
        self.assertFalse(data_validator.validate_symbol("VERYLONGSYMBOL123"))

    def test_validate_config(self):
        """Тест валидации конфигурации"""
        self.assertTrue(data_validator.validate_config_value('VOLUME_THRESHOLD', 1000))
        self.assertTrue(data_validator.validate_config_value('SPREAD_THRESHOLD', 0.5))
        
        self.assertFalse(data_validator.validate_config_value('VOLUME_THRESHOLD', -100))
        self.assertFalse(data_validator.validate_config_value('SPREAD_THRESHOLD', 200))

class TestCircuitBreaker(unittest.TestCase):
    """Тесты Circuit Breaker"""

    def setUp(self):
        self.cb = CircuitBreaker(failure_threshold=3, timeout=1.0, name="test_cb")

    async def test_circuit_breaker_states(self):
        """Тест состояний Circuit Breaker"""
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

    def test_cleanup_metrics(self):
        """Тест очистки метрик"""
        # Добавляем много метрик
        for i in range(1100):
            self.metrics.record_api_request("/test", 0.1, 200)
        
        self.metrics.cleanup_old_metrics()
        
        # Должно остаться не больше 1000
        stats = self.metrics.get_api_stats()
        if "/test" in stats:
            self.assertLessEqual(stats["/test"]['total_requests'], 1000)

class TestAlertManager(unittest.TestCase):
    """Тесты системы алертов"""

    def setUp(self):
        self.alert_manager = alert_manager

    def test_system_alerts(self):
        """Тест системных алертов"""
        system_info = {
            'memory_percent': 90,
            'cpu_percent': 85,
            'disk_percent': 95
        }
        
        alerts = self.alert_manager.check_system_alerts(system_info)
        self.assertGreater(len(alerts), 0)
        
        # Проверяем, что есть алерт о высоком использовании памяти
        memory_alerts = [a for a in alerts if a['type'] == 'high_memory_usage']
        self.assertGreater(len(memory_alerts), 0)

    def test_api_alerts(self):
        """Тест алертов API"""
        api_stats = {
            '/test': {
                'avg_response_time': 10.0,
                'total_requests': 100,
                'error_count': 20
            }
        }
        
        alerts = self.alert_manager.check_api_alerts(api_stats)
        self.assertGreater(len(alerts), 0)

    def test_alert_stats(self):
        """Тест статистики алертов"""
        stats = self.alert_manager.get_alert_stats()
        
        self.assertIn('total_alerts', stats)
        self.assertIn('active_alerts', stats)
        self.assertIn('total_triggers', stats)

class TestPerformanceOptimizer(unittest.TestCase):
    """Тесты оптимизатора производительности"""

    def test_performance_score(self):
        """Тест оценки производительности"""
        score = performance_optimizer.get_performance_score()
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

class TestAutoMaintenance(unittest.TestCase):
    """Тесты автообслуживания"""

    def test_maintenance_stats(self):
        """Тест статистики обслуживания"""
        stats = auto_maintenance.get_maintenance_stats()
        
        self.assertIn('running', stats)
        self.assertIn('maintenance_interval', stats)
        self.assertIn('last_cleanup', stats)

class AsyncTestRunner:
    """Утилита для запуска async тестов"""
    
    @staticmethod
    def run_async_test(coro):
        """Запускает async тест"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

class TestIntegration(unittest.TestCase):
    """Интеграционные тесты"""

    def test_full_system_integration(self):
        """Полный интеграционный тест системы"""
        bot_logger.info("🧪 Запуск полного интеграционного теста")

        # Тест конфигурации
        original_volume = config_manager.get('VOLUME_THRESHOLD')
        config_manager.set('VOLUME_THRESHOLD', 2000)
        self.assertEqual(config_manager.get('VOLUME_THRESHOLD'), 2000)
        config_manager.set('VOLUME_THRESHOLD', original_volume)

        # Тест списка отслеживания
        original_watchlist = watchlist_manager.get_all().copy()
        watchlist_manager.clear()
        
        test_symbols = ["BTC", "ETH", "ADA"]
        for symbol in test_symbols:
            self.assertTrue(watchlist_manager.add(symbol))
        
        self.assertEqual(watchlist_manager.size(), len(test_symbols))
        
        # Восстанавливаем
        watchlist_manager.clear()
        for symbol in original_watchlist:
            watchlist_manager.add(symbol)

        # Тест кеша
        cache_manager.set_price_cache("BTC", 50000.0)
        cached_price = cache_manager.get_price_cache("BTC")
        self.assertEqual(cached_price, 50000.0)

        # Тест валидации
        valid_data = {
            'symbol': 'BTC',
            'price': 50000.0,
            'volume': 1000.0,
            'change': 2.5,
            'spread': 0.1,
            'natr': 0.8,
            'trades': 100,
            'active': True,
            'has_recent_trades': True,
            'timestamp': time.time()
        }
        self.assertTrue(data_validator.validate_coin_data(valid_data))

        # Тест метрик
        metrics_manager.record_api_request("/test_integration", 0.5, 200)
        stats = metrics_manager.get_api_stats()
        self.assertIn("/test_integration", stats)

        bot_logger.info("✅ Интеграционный тест завершен успешно")

def run_all_tests():
    """Запускает все тесты"""
    bot_logger.info("🧪 Запуск комплексного тестирования торгового бота v2.1")
    
    # Создаем test suite
    suite = unittest.TestSuite()
    
    # Добавляем все тестовые классы
    test_classes = [
        TestConfigManager,
        TestWatchlistManager,
        TestCacheManager,
        TestDataValidator,
        TestMetricsManager,
        TestAlertManager,
        TestPerformanceOptimizer,
        TestAutoMaintenance,
        TestIntegration
    ]
    
    for test_class in test_classes:
        tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    # Добавляем async тесты отдельно
    async_tests = [
        TestCircuitBreaker('test_circuit_breaker_states')
    ]
    
    # Запускаем основные тесты
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Запускаем async тесты
    for async_test in async_tests:
        try:
            AsyncTestRunner.run_async_test(async_test.test_circuit_breaker_states())
            bot_logger.info(f"✅ Async тест {async_test._testMethodName} пройден")
        except Exception as e:
            bot_logger.error(f"❌ Async тест {async_test._testMethodName} провален: {e}")
            result.errors.append((async_test, str(e)))
    
    # Выводим результаты
    print(f"\n🎯 Результаты комплексного тестирования:")
    print(f"   Всего тестов: {result.testsRun}")
    print(f"   Успешно: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"   Провалено: {len(result.failures)}")
    print(f"   Ошибок: {len(result.errors)}")
    
    if result.wasSuccessful():
        bot_logger.info("✅ Все тесты пройдены успешно! Система готова к продакшну.")
        print("\n🚀 Система прошла все тесты и готова к использованию!")
    else:
        bot_logger.error("❌ Некоторые тесты провалены. Требуется доработка.")
        print("\n⚠️ Обнаружены проблемы. Проверьте логи для подробностей.")
    
    return result.wasSuccessful()

if __name__ == '__main__':
    success = run_all_tests()
    exit(0 if success else 1)
