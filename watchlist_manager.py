import json
import os
from typing import Set
from datetime import datetime
from logger import bot_logger

class WatchlistManager:
    def __init__(self, file_path: str = "watchlist.json"):
        self.file_path = file_path
        self.watchlist: Set[str] = set()
        self.load()

    def load(self):
        """Загружает список отслеживания из файла"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.watchlist = set(data.get('symbols', []))
                    bot_logger.info(f"Загружено {len(self.watchlist)} монет для отслеживания")
            else:
                self.watchlist = set()
                bot_logger.info("Создан новый список отслеживания")
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки списка отслеживания: {e}")
            self.watchlist = set()

    def save(self):
        """Сохраняет список отслеживания в файл"""
        try:
            data = {
                'symbols': list(self.watchlist),
                'updated': datetime.now().isoformat()
            }
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            bot_logger.debug(f"Список отслеживания сохранен: {len(self.watchlist)} монет")
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения списка отслеживания: {e}")

    def add(self, symbol: str) -> bool:
        """Добавляет символ в список отслеживания"""
        symbol = symbol.upper().replace("_USDT", "").replace("USDT", "")
        if symbol not in self.watchlist:
            self.watchlist.add(symbol)
            self.save()
            bot_logger.info(f"Добавлена монета: {symbol}")
            return True
        return False

    def remove(self, symbol: str) -> bool:
        """Удаляет символ из списка отслеживания"""
        symbol = symbol.upper().replace("_USDT", "").replace("USDT", "")
        if symbol in self.watchlist:
            self.watchlist.remove(symbol)
            self.save()
            bot_logger.info(f"Удалена монета: {symbol}")
            return True
        return False

    def contains(self, symbol: str) -> bool:
        """Проверяет наличие символа в списке"""
        symbol = symbol.upper().replace("_USDT", "").replace("USDT", "")
        return symbol in self.watchlist

    def get_all(self) -> Set[str]:
        """Возвращает все символы в списке отслеживания"""
        return self.watchlist.copy()

    def size(self) -> int:
        """Возвращает размер списка отслеживания"""
        return len(self.watchlist)

    def clear(self):
        """Очищает список отслеживания"""
        self.watchlist.clear()
        self.save()
        bot_logger.info("Список отслеживания очищен")

# Экземпляр менеджера списка администратора
watchlist_manager = WatchlistManager()