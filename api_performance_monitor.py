
#!/usr/bin/env python3
"""
Мониторинг производительности API запросов
"""

import time
import statistics
from collections import defaultdict, deque
from typing import Dict, List, Optional
from logger import bot_logger

class APIPerformanceMonitor:
    def __init__(self, window_size=100):
        self.window_size = window_size
        self.response_times = defaultdict(lambda: deque(maxlen=window_size))
        self.error_counts = defaultdict(int)
        self.success_counts = defaultdict(int)
        self.last_error_time = defaultdict(float)
        
        # Пороги для алертов
        self.slow_threshold = 2.0  # секунды
        self.error_rate_threshold = 0.1  # 10%
        
    def record_request(self, endpoint: str, response_time: float, status_code: int):
        """Записывает метрики запроса"""
        try:
            self.response_times[endpoint].append(response_time)
            
            if 200 <= status_code < 300:
                self.success_counts[endpoint] += 1
            else:
                self.error_counts[endpoint] += 1
                self.last_error_time[endpoint] = time.time()
                
        except Exception as e:
            bot_logger.error(f"Ошибка записи метрик API: {e}")
    
    def get_endpoint_stats(self, endpoint: str) -> Dict:
        """Получает статистику для конкретного endpoint"""
        try:
            times = list(self.response_times[endpoint])
            if not times:
                return {'status': 'no_data'}
            
            total_requests = self.success_counts[endpoint] + self.error_counts[endpoint]
            error_rate = self.error_counts[endpoint] / max(total_requests, 1)
            
            stats = {
                'endpoint': endpoint,
                'total_requests': total_requests,
                'avg_response_time': statistics.mean(times),
                'median_response_time': statistics.median(times),
                'min_response_time': min(times),
                'max_response_time': max(times),
                'error_rate': error_rate,
                'error_count': self.error_counts[endpoint],
                'success_count': self.success_counts[endpoint],
                'status': self._get_endpoint_health_status(endpoint, times, error_rate)
            }
            
            # Добавляем 95-й перцентиль если достаточно данных
            if len(times) >= 20:
                sorted_times = sorted(times)
                p95_index = int(0.95 * len(sorted_times))
                stats['p95_response_time'] = sorted_times[p95_index]
            
            return stats
            
        except Exception as e:
            bot_logger.error(f"Ошибка получения статистики endpoint {endpoint}: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def _get_endpoint_health_status(self, endpoint: str, times: List[float], error_rate: float) -> str:
        """Определяет статус здоровья endpoint"""
        try:
            avg_time = statistics.mean(times) if times else 0
            
            if error_rate > self.error_rate_threshold:
                return 'critical'
            elif avg_time > self.slow_threshold:
                return 'warning'
            else:
                return 'healthy'
                
        except Exception:
            return 'unknown'
    
    def get_all_stats(self) -> Dict:
        """Получает статистику всех endpoints"""
        try:
            all_stats = {}
            total_requests = 0
            total_errors = 0
            all_times = []
            
            for endpoint in self.response_times.keys():
                endpoint_stats = self.get_endpoint_stats(endpoint)
                all_stats[endpoint] = endpoint_stats
                
                if endpoint_stats.get('status') != 'no_data':
                    total_requests += endpoint_stats.get('total_requests', 0)
                    total_errors += endpoint_stats.get('error_count', 0)
                    all_times.extend(self.response_times[endpoint])
            
            # Общая статистика
            summary = {
                'total_requests': total_requests,
                'total_errors': total_errors,
                'overall_error_rate': total_errors / max(total_requests, 1),
                'endpoints': all_stats
            }
            
            if all_times:
                summary.update({
                    'overall_avg_response_time': statistics.mean(all_times),
                    'overall_median_response_time': statistics.median(all_times)
                })
            
            return summary
            
        except Exception as e:
            bot_logger.error(f"Ошибка получения общей статистики API: {e}")
            return {'error': str(e)}
    
    def get_slow_endpoints(self) -> List[str]:
        """Возвращает список медленных endpoints"""
        try:
            slow_endpoints = []
            
            for endpoint, times in self.response_times.items():
                if times:
                    avg_time = statistics.mean(times)
                    if avg_time > self.slow_threshold:
                        slow_endpoints.append(endpoint)
            
            return slow_endpoints
            
        except Exception as e:
            bot_logger.error(f"Ошибка поиска медленных endpoints: {e}")
            return []
    
    def get_error_prone_endpoints(self) -> List[str]:
        """Возвращает список endpoints с высокой частотой ошибок"""
        try:
            error_endpoints = []
            
            for endpoint in self.response_times.keys():
                total_requests = self.success_counts[endpoint] + self.error_counts[endpoint]
                if total_requests > 10:  # Минимум запросов для анализа
                    error_rate = self.error_counts[endpoint] / total_requests
                    if error_rate > self.error_rate_threshold:
                        error_endpoints.append(endpoint)
            
            return error_endpoints
            
        except Exception as e:
            bot_logger.error(f"Ошибка поиска проблемных endpoints: {e}")
            return []
    
    def reset_stats(self):
        """Сбрасывает всю статистику"""
        try:
            self.response_times.clear()
            self.error_counts.clear()
            self.success_counts.clear()
            self.last_error_time.clear()
            bot_logger.info("Статистика API производительности сброшена")
            
        except Exception as e:
            bot_logger.error(f"Ошибка сброса статистики API: {e}")

# Глобальный экземпляр
api_performance_monitor = APIPerformanceMonitor()
