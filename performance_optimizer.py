
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
        self.auto_optimization_enabled = True
        self.optimization_stats = {
            'total_optimizations': 0,
            'successful_optimizations': 0,
            'last_optimization_time': 0,
            'performance_improvements': []
        }
        
    async def optimize(self):
        """Основной метод оптимизации (исправлен)"""
        try:
            await self.auto_optimize()
            self.optimization_stats['successful_optimizations'] += 1
            bot_logger.info("✅ Оптимизация производительности завершена успешно")
        except Exception as e:
            bot_logger.error(f"❌ Ошибка оптимизации: {e}")
        finally:
            self.optimization_stats['total_optimizations'] += 1
            self.optimization_stats['last_optimization_time'] = time.time()
        
    async def auto_optimize(self):
        """Автоматическая оптимизация на основе метрик"""
        current_time = time.time()
        
        if not self.auto_optimization_enabled:
            return
            
        if current_time - self.last_optimization < self.optimization_interval:
            return
            
        try:
            # Получаем текущие метрики
            metrics = metrics_manager.get_summary()
            api_stats = metrics.get('api_stats', {})
            
            # Анализируем производительность
            performance_data = self._analyze_performance(api_stats)
            
            if performance_data['needs_optimization']:
                await self._apply_optimizations(performance_data)
                
            # Сохраняем историю производительности
            self.performance_history.append({
                'timestamp': current_time,
                'metrics': performance_data,
                'actions_taken': performance_data.get('actions_taken', [])
            })
            
            # Ограничиваем размер истории
            if len(self.performance_history) > 100:
                self.performance_history = self.performance_history[-50:]
            
            self.last_optimization = current_time
            
        except Exception as e:
            bot_logger.error(f"Ошибка автооптимизации: {e}")
    
    def _analyze_performance(self, api_stats: Dict) -> Dict[str, Any]:
        """Анализирует текущую производительность"""
        analysis = {
            'needs_optimization': False,
            'avg_response_time': 0,
            'slow_endpoints': [],
            'actions_taken': [],
            'performance_score': 100
        }
        
        if not api_stats:
            return analysis
            
        # Анализируем времена ответа
        avg_response_times = []
        slow_endpoints = []
        
        for endpoint, stats in api_stats.items():
            avg_time = stats.get('avg_response_time', 0)
            if avg_time > 0:
                avg_response_times.append(avg_time)
                if avg_time > 1.0:  # Медленные endpoint'ы
                    slow_endpoints.append({
                        'endpoint': endpoint,
                        'avg_time': avg_time,
                        'requests': stats.get('total_requests', 0)
                    })
        
        if avg_response_times:
            overall_avg = sum(avg_response_times) / len(avg_response_times)
            analysis['avg_response_time'] = overall_avg
            analysis['slow_endpoints'] = slow_endpoints
            
            # Определяем необходимость оптимизации
            if overall_avg > 0.8 or len(slow_endpoints) > 0:
                analysis['needs_optimization'] = True
                
            # Рассчитываем оценку производительности
            analysis['performance_score'] = self._calculate_performance_score(overall_avg)
        
        return analysis
    
    async def _apply_optimizations(self, performance_data: Dict):
        """Применяет оптимизации на основе анализа"""
        actions_taken = []
        
        try:
            avg_time = performance_data['avg_response_time']
            
            # Если API медленный - снижаем нагрузку
            if avg_time > 1.0:
                current_batch_size = config_manager.get('CHECK_BATCH_SIZE')
                if current_batch_size > 8:
                    new_batch_size = max(8, current_batch_size - 2)
                    config_manager.set('CHECK_BATCH_SIZE', new_batch_size)
                    actions_taken.append(f"Уменьшен batch_size до {new_batch_size}")
                    
                current_interval = config_manager.get('CHECK_BATCH_INTERVAL')
                if current_interval < 1.0:
                    new_interval = min(1.2, current_interval + 0.2)
                    config_manager.set('CHECK_BATCH_INTERVAL', new_interval)
                    actions_taken.append(f"Увеличен interval до {new_interval}")
                
                bot_logger.info(f"🔧 Автооптимизация (медленный API): {', '.join(actions_taken)}")
            
            # Если API быстрый - можем увеличить нагрузку
            elif avg_time < 0.3:
                current_batch_size = config_manager.get('CHECK_BATCH_SIZE')
                if current_batch_size < 20:
                    new_batch_size = min(20, current_batch_size + 1)
                    config_manager.set('CHECK_BATCH_SIZE', new_batch_size)
                    actions_taken.append(f"Увеличен batch_size до {new_batch_size}")
                    
                current_interval = config_manager.get('CHECK_BATCH_INTERVAL')
                if current_interval > 0.3:
                    new_interval = max(0.3, current_interval - 0.05)
                    config_manager.set('CHECK_BATCH_INTERVAL', new_interval)
                    actions_taken.append(f"Уменьшен interval до {new_interval}")
                
                if actions_taken:
                    bot_logger.info(f"⚡ Автооптимизация (быстрый API): {', '.join(actions_taken)}")
            
            # Оптимизация кеша
            cache_optimizations = await self._optimize_cache()
            actions_taken.extend(cache_optimizations)
            
            # Сохраняем действия в performance_data
            performance_data['actions_taken'] = actions_taken
            
            # Записываем улучшения в статистику
            if actions_taken:
                self.optimization_stats['performance_improvements'].append({
                    'timestamp': time.time(),
                    'actions': actions_taken,
                    'performance_before': performance_data['avg_response_time']
                })
            
        except Exception as e:
            bot_logger.error(f"Ошибка применения оптимизаций: {e}")
    
    async def _optimize_cache(self) -> list:
        """Оптимизирует настройки кеша"""
        actions = []
        
        try:
            from cache_manager import cache_manager
            cache_stats = cache_manager.get_stats()
            
            # Если кеш переполнен, уменьшаем TTL
            if cache_stats.get('total_entries', 0) > 1000:
                current_ttl = config_manager.get('CACHE_TTL_SECONDS', 5)
                if current_ttl > 3:
                    new_ttl = max(3, current_ttl - 1)
                    config_manager.set('CACHE_TTL_SECONDS', new_ttl)
                    actions.append(f"Уменьшен TTL кеша до {new_ttl}с")
            
            # Если кеш малоиспользуемый, увеличиваем TTL
            elif cache_stats.get('total_entries', 0) < 100:
                current_ttl = config_manager.get('CACHE_TTL_SECONDS', 5)
                if current_ttl < 10:
                    new_ttl = min(10, current_ttl + 1)
                    config_manager.set('CACHE_TTL_SECONDS', new_ttl)
                    actions.append(f"Увеличен TTL кеша до {new_ttl}с")
            
        except Exception as e:
            bot_logger.debug(f"Ошибка оптимизации кеша: {e}")
        
        return actions
    
    def _calculate_performance_score(self, avg_response_time: float) -> float:
        """Рассчитывает оценку производительности (0-100)"""
        if avg_response_time <= 0.2:
            return 100.0
        elif avg_response_time <= 0.5:
            return 90.0
        elif avg_response_time <= 1.0:
            return 75.0
        elif avg_response_time <= 2.0:
            return 50.0
        elif avg_response_time <= 3.0:
            return 25.0
        else:
            return 10.0
    
    def get_performance_score(self) -> float:
        """Возвращает текущую оценку производительности"""
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
            return self._calculate_performance_score(avg_response_time)
                
        except Exception as e:
            bot_logger.debug(f"Ошибка расчета performance score: {e}")
            return 50.0
    
    def get_optimization_stats(self) -> Dict[str, Any]:
        """Возвращает статистику оптимизаций"""
        return {
            **self.optimization_stats,
            'optimization_enabled': self.auto_optimization_enabled,
            'recent_history': self.performance_history[-10:] if self.performance_history else [],
            'current_score': self.get_performance_score()
        }
    
    def enable_auto_optimization(self):
        """Включает автооптимизацию"""
        self.auto_optimization_enabled = True
        bot_logger.info("✅ Автооптимизация включена")
    
    def disable_auto_optimization(self):
        """Отключает автооптимизацию"""
        self.auto_optimization_enabled = False
        bot_logger.info("⏸️ Автооптимизация отключена")
    
    async def force_optimization(self):
        """Принудительная оптимизация"""
        self.last_optimization = 0  # Сбрасываем таймер
        await self.auto_optimize()
        bot_logger.info("🔧 Принудительная оптимизация выполнена")

# Глобальный экземпляр оптимизатора
performance_optimizer = PerformanceOptimizer()
