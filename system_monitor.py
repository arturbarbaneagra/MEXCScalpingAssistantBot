
#!/usr/bin/env python3
"""
Мониторинг системных ресурсов
"""

import psutil
import time
from typing import Dict, Any
from logger import bot_logger

class SystemMonitor:
    """Мониторинг системных ресурсов"""
    
    def __init__(self):
        self.start_time = time.time()
    
    def get_system_info(self) -> Dict[str, Any]:
        """Получает информацию о системе"""
        try:
            memory = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=1)
            disk = psutil.disk_usage('/')
            
            return {
                'memory_percent': memory.percent,
                'memory_available_gb': memory.available / (1024**3),
                'cpu_percent': cpu_percent,
                'disk_percent': disk.percent,
                'disk_free_gb': disk.free / (1024**3),
                'uptime_hours': (time.time() - self.start_time) / 3600,
                'timestamp': time.time()
            }
        except Exception as e:
            bot_logger.error(f"Ошибка получения системной информации: {e}")
            return {}
    
    def check_system_health(self) -> Dict[str, Any]:
        """Проверяет состояние системы"""
        info = self.get_system_info()
        
        health = {
            'status': 'healthy',
            'warnings': [],
            'critical': []
        }
        
        # Проверки
        if info.get('memory_percent', 0) > 85:
            health['warnings'].append(f"Высокое использование памяти: {info['memory_percent']:.1f}%")
            if info['memory_percent'] > 95:
                health['critical'].append("Критическое использование памяти")
                health['status'] = 'critical'
        
        if info.get('cpu_percent', 0) > 80:
            health['warnings'].append(f"Высокая загрузка CPU: {info['cpu_percent']:.1f}%")
            if info['cpu_percent'] > 95:
                health['critical'].append("Критическая загрузка CPU")
                health['status'] = 'critical'
        
        if info.get('disk_percent', 0) > 90:
            health['warnings'].append(f"Мало места на диске: {info['disk_percent']:.1f}%")
            if info['disk_percent'] > 98:
                health['critical'].append("Критически мало места на диске")
                health['status'] = 'critical'
        
        if health['warnings'] and health['status'] == 'healthy':
            health['status'] = 'warning'
        
        return health

# Глобальный экземпляр
system_monitor = SystemMonitor()
