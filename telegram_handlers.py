
"""
Дополнительные обработчики для Telegram бота
Содержит специализированные обработчики, которые не входят в основной класс
"""

from typing import Optional, Dict, Any
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from logger import bot_logger
from config import config_manager
from api_client import api_client
from watchlist_manager import watchlist_manager

class ExtendedTelegramHandlers:
    """Расширенные обработчики для специальных функций"""
    
    def __init__(self, bot_instance):
        self.bot = bot_instance

    async def admin_stats_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Расширенная статистика для администраторов"""
        try:
            from metrics_manager import metrics_manager
            from cache_manager import cache_manager
            from alert_manager import alert_manager
            
            # Собираем подробную статистику
            metrics = metrics_manager.get_summary()
            cache_stats = cache_manager.get_stats()
            alert_stats = alert_manager.get_alert_stats()
            
            stats_text = (
                "📊 <b>Административная статистика:</b>\n\n"
                f"🔄 <b>Система:</b>\n"
                f"• Время работы: {metrics.get('uptime_seconds', 0)/3600:.1f} часов\n"
                f"• Кеш эффективность: {cache_stats.get('cache_efficiency', 0):.1f}%\n"
                f"• Память кеша: {cache_stats.get('memory_usage_kb', 0):.1f} KB\n\n"
                f"🚨 <b>Алерты:</b>\n"
                f"• Всего алертов: {alert_stats.get('total_alerts', 0)}\n"
                f"• Активных: {alert_stats.get('active_alerts', 0)}\n"
                f"• Срабатываний: {alert_stats.get('total_triggers', 0)}\n\n"
                f"📡 <b>API статистика:</b>\n"
            )
            
            api_stats = metrics.get('api_stats', {})
            for endpoint, stats in list(api_stats.items())[:3]:
                stats_text += (
                    f"• {endpoint}: {stats.get('total_requests', 0)} запросов, "
                    f"avg {stats.get('avg_response_time', 0):.2f}s\n"
                )
            
            await update.message.reply_text(
                stats_text,
                reply_markup=self.bot.main_keyboard,
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            bot_logger.error(f"Ошибка в admin_stats_handler: {e}")
            await update.message.reply_text(
                "❌ Ошибка получения статистики",
                reply_markup=self.bot.main_keyboard
            )

    async def bulk_add_coins_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Массовое добавление монет"""
        if len(context.args) == 0:
            await update.message.reply_text(
                "📝 <b>Массовое добавление монет:</b>\n\n"
                "Использование: <code>/bulk_add BTC ETH ADA SOL</code>\n"
                "Или: <code>/bulk_add BTC,ETH,ADA,SOL</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # Парсим символы
        symbols_text = " ".join(context.args)
        symbols = [s.strip().upper() for s in symbols_text.replace(',', ' ').split() if s.strip()]
        
        if not symbols:
            await update.message.reply_text("❌ Не указаны символы для добавления")
            return

        # Ограничиваем количество
        if len(symbols) > 20:
            await update.message.reply_text("❌ Максимум 20 монет за раз")
            return

        await update.message.reply_text(f"🔄 Проверяю {len(symbols)} монет...")

        added_count = 0
        failed_symbols = []

        for symbol in symbols:
            try:
                # Нормализуем символ
                clean_symbol = symbol.replace("_USDT", "").replace("USDT", "")
                
                if watchlist_manager.contains(clean_symbol):
                    continue  # Уже в списке
                
                # Проверяем доступность
                coin_data = await api_client.get_coin_data(clean_symbol)
                if coin_data:
                    watchlist_manager.add(clean_symbol)
                    added_count += 1
                else:
                    failed_symbols.append(clean_symbol)
                    
            except Exception as e:
                bot_logger.error(f"Ошибка при добавлении {symbol}: {e}")
                failed_symbols.append(symbol)

        # Отчет о результатах
        result_text = f"✅ <b>Массовое добавление завершено:</b>\n\n"
        result_text += f"• Добавлено: {added_count} монет\n"
        
        if failed_symbols:
            result_text += f"• Ошибки: {len(failed_symbols)} монет\n"
            if len(failed_symbols) <= 10:
                result_text += f"• Не удалось: {', '.join(failed_symbols)}"

        await update.message.reply_text(
            result_text,
            reply_markup=self.bot.main_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def export_watchlist_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Экспорт списка отслеживания"""
        try:
            coins = watchlist_manager.get_all()
            
            if not coins:
                await update.message.reply_text("📋 Список отслеживания пуст")
                return

            # Создаем текстовый экспорт
            export_text = "# Список отслеживания торгового бота\n"
            export_text += f"# Экспортировано: {len(coins)} монет\n\n"
            
            sorted_coins = sorted(coins)
            for i, coin in enumerate(sorted_coins, 1):
                export_text += f"{i:2d}. {coin}_USDT\n"

            # Отправляем как файл
            import io
            file_buffer = io.BytesIO(export_text.encode('utf-8'))
            file_buffer.name = "watchlist_export.txt"

            await update.message.reply_document(
                document=file_buffer,
                caption=f"📋 Экспорт списка отслеживания ({len(coins)} монет)",
                reply_markup=self.bot.main_keyboard
            )

        except Exception as e:
            bot_logger.error(f"Ошибка экспорта списка: {e}")
            await update.message.reply_text(
                "❌ Ошибка экспорта списка",
                reply_markup=self.bot.main_keyboard
            )

    async def system_health_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Проверка здоровья системы"""
        try:
            from health_check import health_checker
            
            await update.message.reply_text("🔍 Проверяю состояние системы...")
            
            # Запускаем диагностику
            health_data = await health_checker.run_diagnostics()
            
            status_emoji = {
                'healthy': '✅',
                'warning': '⚠️',
                'critical': '🚨',
                'error': '❌'
            }
            
            emoji = status_emoji.get(health_data.get('status', 'error'), '❓')
            
            health_text = (
                f"{emoji} <b>Состояние системы: {health_data.get('status', 'unknown').upper()}</b>\n\n"
                f"💾 <b>Система:</b>\n"
                f"• Память: {health_data.get('system', {}).get('memory_percent', 0):.1f}%\n"
                f"• CPU: {health_data.get('system', {}).get('cpu_percent', 0):.1f}%\n"
                f"• Диск: {health_data.get('system', {}).get('disk_percent', 0):.1f}%\n\n"
                f"🤖 <b>Бот:</b>\n"
                f"• Статус: {'🟢 Работает' if health_data.get('bot', {}).get('bot_running') else '🔴 Остановлен'}\n"
                f"• Режим: {health_data.get('bot', {}).get('bot_mode', 'Нет')}\n"
                f"• Активных монет: {health_data.get('bot', {}).get('active_coins_count', 0)}\n\n"
                f"📡 <b>API:</b>\n"
                f"• Доступность: {'✅' if health_data.get('api', {}).get('api_accessible') else '❌'}\n"
                f"• Время ответа: {health_data.get('api', {}).get('response_time', 0):.2f}s"
            )
            
            await update.message.reply_text(
                health_text,
                reply_markup=self.bot.main_keyboard,
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            bot_logger.error(f"Ошибка проверки здоровья: {e}")
            await update.message.reply_text(
                "❌ Ошибка проверки состояния системы",
                reply_markup=self.bot.main_keyboard
            )

# Создаем экземпляр для использования в основном боте
def create_extended_handlers(bot_instance):
    """Создает экземпляр расширенных обработчиков"""
    return ExtendedTelegramHandlers(bot_instance)
