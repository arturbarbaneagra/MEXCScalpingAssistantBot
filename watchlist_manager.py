
import json
import os
from typing import Set
from logger import bot_logger

class WatchlistManager:
    def __init__(self, filename: str = "watchlist.json"):
        self.filename = filename
        self.watchlist: Set[str] = set()
        self.load()
    
    def load(self) -> None:
        """Загружает список из файла"""
        if not os.path.exists(self.filename):
            bot_logger.info("Файл списка не найден, создается новый")
            self.save()
            return
        
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.watchlist = set(data)
                else:
                    bot_logger.warning("Неверный формат файла списка")
                    self.watchlist = set()
            
            bot_logger.info(f"Загружен список из {len(self.watchlist)} монет")
        except (json.JSONDecodeError, IOError) as e:
            bot_logger.error(f"Ошибка загрузки списка: {e}")
            self.watchlist = set()
    
    def save(self) -> None:
        """Сохраняет список в файл"""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(sorted(list(self.watchlist)), f, ensure_ascii=False, indent=2)
            bot_logger.info(f"Список сохранен ({len(self.watchlist)} монет)")
        except IOError as e:
            bot_logger.error(f"Ошибка сохранения списка: {e}")
    
    def add(self, symbol: str) -> bool:
        """Добавляет монету в список"""
        normalized = symbol.upper().replace("_USDT", "").replace("USDT", "").strip()
        if not normalized:
            return False
        
        if normalized not in self.watchlist:
            self.watchlist.add(normalized)
            self.save()
            bot_logger.info(f"Добавлена монета: {normalized}")
            return True
        return False
    
    def remove(self, symbol: str) -> bool:
        """Удаляет монету из списка"""
        normalized = symbol.upper().replace("_USDT", "").replace("USDT", "").strip()
        if normalized in self.watchlist:
            self.watchlist.remove(normalized)
            self.save()
            bot_logger.info(f"Удалена монета: {normalized}")
            return True
        return False
    
    def contains(self, symbol: str) -> bool:
        """Проверяет наличие монеты в списке"""
        normalized = symbol.upper().replace("_USDT", "").replace("USDT", "").strip()
        return normalized in self.watchlist
    
    def get_all(self) -> Set[str]:
        """Возвращает все монеты"""
        return self.watchlist.copy()
    
    def size(self) -> int:
        """Возвращает размер списка"""
        return len(self.watchlist)
    
    def clear(self) -> None:
        """Очищает список"""
        self.watchlist.clear()
        self.save()
        bot_logger.info("Список очищен")

# Глобальный экземпляр менеджера списка
watchlist_manager = WatchlistManager()
