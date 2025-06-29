
#!/usr/bin/env python3
"""
Утилита для очистки и ротации логов
"""

import os
import glob
import time
from datetime import datetime, timedelta

def cleanup_logs():
    """Очищает старые логи"""
    print("🧹 Начинаем очистку логов...")
    
    # Удаляем старые ротированные логи (старше 7 дней)
    cutoff_time = time.time() - (7 * 24 * 3600)
    
    log_patterns = [
        "trading_bot.log.*",
        "*.log.backup",
        "bot_log_*.log"
    ]
    
    deleted_count = 0
    for pattern in log_patterns:
        for log_file in glob.glob(pattern):
            try:
                if os.path.getmtime(log_file) < cutoff_time:
                    size_mb = os.path.getsize(log_file) / 1024 / 1024
                    os.remove(log_file)
                    print(f"  ✅ Удален: {log_file} ({size_mb:.1f} MB)")
                    deleted_count += 1
            except Exception as e:
                print(f"  ❌ Ошибка удаления {log_file}: {e}")
    
    # Проверяем размер основного лога
    if os.path.exists("trading_bot.log"):
        size_mb = os.path.getsize("trading_bot.log") / 1024 / 1024
        print(f"📊 Основной лог: trading_bot.log ({size_mb:.1f} MB)")
        
        if size_mb > 45:  # Если больше 45MB, принудительно ротируем
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"trading_bot.log.{timestamp}.rotated"
            try:
                os.rename("trading_bot.log", backup_name)
                print(f"  🔄 Принудительная ротация: {backup_name}")
            except Exception as e:
                print(f"  ❌ Ошибка ротации: {e}")
    
    print(f"✅ Очистка завершена. Удалено файлов: {deleted_count}")

if __name__ == "__main__":
    cleanup_logs()
