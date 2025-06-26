
import json
import os
from typing import Set
from datetime import datetime
from logger import bot_logger

class WatchlistManager:
    def __init__(self, watchlist_file: str = "watchlist.json"):
        self.watchlist_file = watchlist_file
        self.watchlist: Set[str] = set()
        self.load_watchlist()
    
    def load_watchlist(self):
        """Загружает список отслеживания из файла"""
        try:
            if os.path.exists(self.watchlist_file):
                with open(self.watchlist_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.watchlist = set(data)
                    elif isinstance(data, dict) and 'coins' in data:
                        self.watchlist = set(data['coins'])
                    else:
                        self.watchlist = set()
                bot_logger.info(f"Загружено {len(self.watchlist)} монет для отслеживания")
            else:
                bot_logger.info("Файл списка отслеживания не найден, создается новый")
                self.watchlist = set()
                self.save_watchlist()
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки списка отслеживания: {e}")
            self.watchlist = set()
    
    def save_watchlist(self):
        """Сохраняет список отслеживания в файл"""
        try:
            data = {
                'coins': list(self.watchlist),
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'count': len(self.watchlist)
            }
            with open(self.watchlist_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            bot_logger.debug(f"Список отслеживания сохранен ({len(self.watchlist)} монет)")
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения списка отслеживания: {e}")
    
    def add(self, symbol: str) -> bool:
        """Добавляет монету в список отслеживания"""
        if symbol and symbol not in self.watchlist:
            self.watchlist.add(symbol.upper())
            self.save_watchlist()
            bot_logger.info(f"Монета {symbol} добавлена в список отслеживания")
            return True
        return False
    
    def remove(self, symbol: str) -> bool:
        """Удаляет монету из списка отслеживания"""
        if symbol and symbol.upper() in self.watchlist:
            self.watchlist.remove(symbol.upper())
            self.save_watchlist()
            bot_logger.info(f"Монета {symbol} удалена из списка отслеживания")
            return True
        return False
    
    def contains(self, symbol: str) -> bool:
        """Проверяет, есть ли монета в списке отслеживания"""
        return symbol.upper() in self.watchlist if symbol else False
    
    def get_all(self) -> Set[str]:
        """Возвращает все монеты из списка отслеживания"""
        return self.watchlist.copy()
    
    def size(self) -> int:
        """Возвращает количество монет в списке отслеживания"""
        return len(self.watchlist)
    
    def clear(self):
        """Очищает список отслеживания"""
        self.watchlist.clear()
        self.save_watchlist()
        bot_logger.info("Список отслеживания очищен")

# Глобальный экземпляр менеджера списка отслеживания
watchlist_manager = WatchlistManager()
