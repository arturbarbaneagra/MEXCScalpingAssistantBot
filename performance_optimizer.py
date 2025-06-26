
import time
import asyncio
from typing import Dict, Any
from logger import bot_logger
from config import config_manager
from metrics_manager import metrics_manager
from advanced_alerts import advanced_alert_manager

class PerformanceOptimizer:
    """Автоматический оптимизатор производительности"""
    
    def __init__(self):
        self.last_optimization = 0
        self.optimization_interval = 300  # 5 минут
        self.performance_history = []
        
    async def auto_optimize(self):
        """Автоматическая оптимизация на основе метрик"""
        current_time = time.time()
        
        if current_time - self.last_optimization < self.optimization_interval:
            return
            
        try:
            # Получаем текущие метрики
            metrics = metrics_manager.get_summary()
            api_stats = metrics.get('api_stats', {})
            
            # Анализируем производительность
            avg_response_times = []
            for endpoint, stats in api_stats.items():
                avg_time = stats.get('avg_response_time', 0)
                if avg_time > 0:
                    avg_response_times.append(avg_time)
            
            if avg_response_times:
                overall_avg = sum(avg_response_times) / len(avg_response_times)
                
                # Если API медленный - снижаем нагрузку
                if overall_avg > 1.0:
                    current_batch_size = config_manager.get('CHECK_BATCH_SIZE')
                    if current_batch_size > 6:
                        new_batch_size = max(6, current_batch_size - 2)
                        config_manager.set('CHECK_BATCH_SIZE', new_batch_size)
                        
                        current_interval = config_manager.get('CHECK_BATCH_INTERVAL')
                        new_interval = min(1.5, current_interval + 0.1)
                        config_manager.set('CHECK_BATCH_INTERVAL', new_interval)
                        
                        bot_logger.info(f"🔧 Автооптимизация: batch_size={new_batch_size}, interval={new_interval}")
                
                # Если API быстрый - можем увеличить нагрузку
                elif overall_avg < 0.3:
                    current_batch_size = config_manager.get('CHECK_BATCH_SIZE')
                    if current_batch_size < 15:
                        new_batch_size = min(15, current_batch_size + 1)
                        config_manager.set('CHECK_BATCH_SIZE', new_batch_size)
                        
                        current_interval = config_manager.get('CHECK_BATCH_INTERVAL')
                        new_interval = max(0.4, current_interval - 0.05)
                        config_manager.set('CHECK_BATCH_INTERVAL', new_interval)
                        
                        bot_logger.info(f"⚡ Автооптимизация: batch_size={new_batch_size}, interval={new_interval}")
            
            self.last_optimization = current_time
            
        except Exception as e:
            bot_logger.error(f"Ошибка автооптимизации: {e}")
    
    def get_performance_score(self) -> float:
        """Возвращает оценку производительности (0-100)"""
        try:
            metrics = metrics_manager.get_summary()
            api_stats = metrics.get('api_stats', {})
            
            if not api_stats:
                return 100.0
            
            # Считаем среднее время ответа
            total_time = 0
            total_requests = 0
            
            for stats in api_stats.values():
                avg_time = stats.get('avg_response_time', 0)
                requests = stats.get('total_requests', 0)
                total_time += avg_time * requests
                total_requests += requests
            
            if total_requests == 0:
                return 100.0
            
            avg_response_time = total_time / total_requests
            
            # Конвертируем в оценку (чем меньше время, тем выше оценка)
            if avg_response_time <= 0.2:
                return 100.0
            elif avg_response_time <= 0.5:
                return 90.0
            elif avg_response_time <= 1.0:
                return 75.0
            elif avg_response_time <= 2.0:
                return 50.0
            else:
                return 25.0
                
        except Exception:
            return 50.0

# Глобальный экземпляр оптимизатора
performance_optimizer = PerformanceOptimizer()
