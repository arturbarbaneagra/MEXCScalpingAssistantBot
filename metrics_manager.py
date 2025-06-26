
import time
import asyncio
from typing import Dict, List, Any
from collections import defaultdict, deque
from logger import bot_logger

class MetricsManager:
    def __init__(self):
        self.start_time = time.time()
        self.api_metrics: Dict[str, List[float]] = defaultdict(list)
        self.performance_metrics: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self.counters: Dict[str, int] = defaultdict(int)
        self.last_cleanup = time.time()

    def record_api_request(self, endpoint: str, response_time: float, status_code: int):
        """Записывает метрику API запроса"""
        self.api_metrics[endpoint].append(response_time)
        self.counters[f"api_requests_{endpoint}"] += 1
        
        if status_code >= 400:
            self.counters[f"api_errors_{endpoint}"] += 1

    def record_performance_metric(self, metric_name: str, value: float):
        """Записывает метрику производительности"""
        self.performance_metrics[metric_name].append(value)
        bot_logger.performance_metric(metric_name, value)

    def get_api_stats(self) -> Dict[str, Any]:
        """Возвращает статистику API"""
        stats = {}
        for endpoint, times in self.api_metrics.items():
            if times:
                stats[endpoint] = {
                    'total_requests': len(times),
                    'avg_response_time': sum(times) / len(times),
                    'max_response_time': max(times),
                    'min_response_time': min(times),
                    'error_count': self.counters.get(f"api_errors_{endpoint}", 0)
                }
        return stats

    def get_performance_stats(self) -> Dict[str, Any]:
        """Возвращает статистику производительности"""
        stats = {}
        for metric_name, values in self.performance_metrics.items():
            if values:
                stats[metric_name] = {
                    'current': values[-1],
                    'avg': sum(values) / len(values),
                    'max': max(values),
                    'min': min(values)
                }
        return stats

    def get_uptime(self) -> float:
        """Возвращает время работы в секундах"""
        return time.time() - self.start_time

    def cleanup_old_metrics(self):
        """Очищает старые метрики"""
        current_time = time.time()
        # Очищаем раз в час
        if current_time - self.last_cleanup > 3600:
            # Оставляем только последние 1000 записей для каждого endpoint
            for endpoint in self.api_metrics:
                if len(self.api_metrics[endpoint]) > 1000:
                    self.api_metrics[endpoint] = self.api_metrics[endpoint][-1000:]
            
            self.last_cleanup = current_time
            bot_logger.debug("Выполнена очистка старых метрик")

    def get_summary(self) -> Dict[str, Any]:
        """Возвращает сводку всех метрик"""
        return {
            'uptime_seconds': self.get_uptime(),
            'api_stats': self.get_api_stats(),
            'performance_stats': self.get_performance_stats(),
            'counters': dict(self.counters)
        }

# Глобальный экземпляр метрик
metrics_manager = MetricsManager()
