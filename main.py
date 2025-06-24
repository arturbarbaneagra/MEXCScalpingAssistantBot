
import json
import time
import asyncio
import threading
import os
import websockets
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters, ContextTypes

MEXC_WS_URL = "wss://wbs.mexc.com/ws"
VOLUME_THRESHOLD = 1000
SPREAD_THRESHOLD = 0.1
NATR_THRESHOLD = 0.5

TELEGRAM_TOKEN = "8180368589:AAHgiD22KRFzXHTiFkw4n5WPwN3Ho2hA4rA"
TELEGRAM_CHAT_ID = "1090477927"

WATCHLIST_FILE = "watchlist.json"
BOT_RUNNING = False

# Хранилище данных монет
COIN_DATA = {}
ACTIVE_COINS = {}

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        return set([c.upper().replace("_USDT", "") for c in json.load(open(WATCHLIST_FILE, "r", encoding="utf-8"))])
    return set()

def save_watchlist():
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(list(WATCHLIST), f)

WATCHLIST = load_watchlist()

async def send_telegram_message(text, parse_mode="HTML"):
    import requests
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': parse_mode
    })
    if r.ok:
        return r.json().get("result", {}).get("message_id")
    return None

async def delete_message(message_id):
    import requests
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage", data={
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id
    })

def calculate_metrics(symbol, kline_data):
    """Рассчитывает метрики на основе данных свечи"""
    try:
        print(f"📊 Расчет метрик для {symbol}, данные: {kline_data}")
        
        open_price = float(kline_data.get('o', 0))
        close_price = float(kline_data.get('c', 0))
        high_price = float(kline_data.get('h', 0))
        low_price = float(kline_data.get('l', 0))
        volume = float(kline_data.get('v', 0))
        
        print(f"🔢 {symbol} OHLCV: O={open_price}, H={high_price}, L={low_price}, C={close_price}, V={volume}")
        
        if close_price == 0 or open_price == 0:
            print(f"⚠️ {symbol}: Некорректные цены (close={close_price}, open={open_price})")
            return None
        
        # NATR (Normalized Average True Range)
        natr = (high_price - low_price) / close_price * 100
        
        # Изменение цены
        change = (close_price - open_price) / open_price * 100
        
        print(f"✅ {symbol}: V={volume:.2f}, NATR={natr:.2f}%, Change={change:.2f}%")
        
        return {
            'symbol': symbol,
            'volume': volume,
            'natr': natr,
            'change': change,
            'close': close_price
        }
    except Exception as e:
        print(f"❌ Ошибка расчета метрик для {symbol}: {e}")
        print(f"❌ Данные kline: {kline_data}")
        return None

async def websocket_handler():
    """Обработчик WebSocket соединения"""
    global BOT_RUNNING, COIN_DATA
    
    while BOT_RUNNING:
        try:
            # Подготавливаем подписки для монет из вотчлиста
            subscriptions = []
            for coin in WATCHLIST:
                symbol = coin + "USDT"
                subscriptions.extend([
                    f"spot@public.kline.v3.api@{symbol}@Min1",
                    f"spot@public.bookTicker.v3.api@{symbol}"
                ])
            
            if not subscriptions:
                print("⚠️ Нет подписок, вотчлист пуст")
                await asyncio.sleep(5)
                continue
            
            print(f"🔗 Подключаемся к WebSocket: {MEXC_WS_URL}")
            print(f"📋 Монеты в вотчлисте: {sorted(WATCHLIST)}")
            print(f"📡 Подготовлены подписки: {subscriptions[:5]}... (всего {len(subscriptions)})")
            
            async with websockets.connect(MEXC_WS_URL) as websocket:
                # Подписываемся на данные
                subscribe_msg = {
                    "method": "SUBSCRIPTION",
                    "params": subscriptions
                }
                print(f"📤 Отправляем подписку: {json.dumps(subscribe_msg, indent=2)}")
                await websocket.send(json.dumps(subscribe_msg))
                print(f"✅ Подписались на {len(subscriptions)} потоков данных")
                
                message_count = 0
                async for message in websocket:
                    if not BOT_RUNNING:
                        break
                    
                    message_count += 1
                    if message_count % 100 == 0:
                        print(f"📊 Обработано {message_count} сообщений")
                        
                    try:
                        if message.strip():  # Проверяем что сообщение не пустое
                            data = json.loads(message)
                            await process_websocket_data(data)
                        else:
                            print("⚠️ Получено пустое сообщение")
                    except json.JSONDecodeError as e:
                        print(f"❌ Ошибка JSON: {e}, сообщение: {message[:100]}...")
                        continue
                    except Exception as e:
                        print(f"❌ Ошибка обработки данных: {e}")
                        
        except Exception as e:
            print(f"❌ Ошибка WebSocket: {e}")
            if BOT_RUNNING:
                print("🔄 Переподключение через 5 секунд...")
                await asyncio.sleep(5)

async def process_websocket_data(data):
    """Обрабатывает данные от WebSocket"""
    global COIN_DATA, ACTIVE_COINS
    
    # Логируем все входящие данные для отладки
    print(f"🔍 Получены данные: {json.dumps(data, indent=2)}")
    
    # Проверяем, что это нужный нам тип данных
    if not isinstance(data, dict):
        print("❌ Данные не являются словарем")
        return
    
    # Проверяем различные форматы данных от MEXC
    print(f"📦 Структура данных: {list(data.keys())}")
    
    # Обработка данных kline (различные возможные форматы)
    kline_processed = False
    
    # Формат 1: данные в поле 'd'
    if 'd' in data and 's' in data:
        print("🎯 Обнаружен формат kline с полем 'd'")
        symbol_full = data['s']
        if symbol_full.endswith('USDT'):
            symbol = symbol_full.replace('USDT', '')
            if symbol in WATCHLIST:
                kline_data = data['d']
                print(f"📈 Данные kline для {symbol}: {kline_data}")
                if isinstance(kline_data, dict):
                    metrics = calculate_metrics(symbol, kline_data)
                    if metrics:
                        if symbol not in COIN_DATA:
                            COIN_DATA[symbol] = {}
                        COIN_DATA[symbol].update({
                            **metrics,
                            'last_update': time.time()
                        })
                        print(f"✅ Обновлены данные для {symbol}: V={metrics['volume']:.2f}, NATR={metrics['natr']:.2f}%")
                        kline_processed = True
    
    # Формат 2: прямые данные kline
    elif 'o' in data and 'c' in data and 'h' in data and 'l' in data and 'v' in data and 's' in data:
        print("🎯 Обнаружен прямой формат kline")
        symbol_full = data['s']
        if symbol_full.endswith('USDT'):
            symbol = symbol_full.replace('USDT', '')
            if symbol in WATCHLIST:
                print(f"📈 Прямые данные kline для {symbol}: {data}")
                metrics = calculate_metrics(symbol, data)
                if metrics:
                    if symbol not in COIN_DATA:
                        COIN_DATA[symbol] = {}
                    COIN_DATA[symbol].update({
                        **metrics,
                        'last_update': time.time()
                    })
                    print(f"✅ Обновлены данные для {symbol}: V={metrics['volume']:.2f}, NATR={metrics['natr']:.2f}%")
                    kline_processed = True
    
    # Обработка данных bookTicker
    ticker_processed = False
    
    # Различные форматы ticker данных
    if 'b' in data and 'a' in data and 's' in data:
        print("🎯 Обнаружен формат bookTicker")
        symbol_full = data['s']
        if symbol_full.endswith('USDT'):
            symbol = symbol_full.replace('USDT', '')
            if symbol in WATCHLIST:
                try:
                    bid = float(data['b'])
                    ask = float(data['a'])
                    print(f"💰 Ticker данные для {symbol}: bid={bid}, ask={ask}")
                    if bid > 0:
                        spread = (ask - bid) / bid * 100
                        
                        if symbol not in COIN_DATA:
                            COIN_DATA[symbol] = {}
                        
                        COIN_DATA[symbol]['spread'] = spread
                        COIN_DATA[symbol]['last_update'] = time.time()
                        
                        print(f"✅ Обновлен спред для {symbol}: {spread:.3f}%")
                        ticker_processed = True
                except (ValueError, ZeroDivisionError) as e:
                    print(f"❌ Ошибка обработки спреда для {symbol}: {e}")
    
    # Если ничего не обработано, выводим полную информацию
    if not kline_processed and not ticker_processed:
        print(f"⚠️ Неизвестный формат данных: {data}")
    
    # Проверяем условия активности для всех монет с обновленными данными
    for symbol in COIN_DATA.keys():
        if symbol in WATCHLIST:
            await check_coin_activity(symbol)

async def check_coin_activity(symbol):
    """Проверяет активность монеты и отправляет уведомления"""
    if symbol not in COIN_DATA:
        return
    
    data = COIN_DATA[symbol]
    now = time.time()
    
    # Проверяем актуальность данных (не старше 5 минут)
    if now - data.get('last_update', 0) > 300:
        return
    
    volume = data.get('volume', 0)
    spread = data.get('spread', 0)
    natr = data.get('natr', 0)
    change = data.get('change', 0)
    
    # Проверяем наличие всех необходимых данных
    if volume == 0 or spread == 0 or natr == 0:
        return
    
    is_active = (volume >= VOLUME_THRESHOLD and 
                 spread >= SPREAD_THRESHOLD and 
                 natr >= NATR_THRESHOLD)
    
    currently_active = symbol in ACTIVE_COINS
    
    print(f"🔍 Проверка {symbol}: V={volume:.2f} (>{VOLUME_THRESHOLD}), S={spread:.3f}% (>{SPREAD_THRESHOLD}), NATR={natr:.2f}% (>{NATR_THRESHOLD}) = {'АКТИВЕН' if is_active else 'НЕ АКТИВЕН'}")
    
    if is_active and not currently_active:
        # Монета стала активной
        msg = (
            f"🚨 <b>{symbol}_USDT активен</b>\n"
            f"🔄 Изм: {change:.2f}%\n"
            f"📊 Объём: ${volume:,.2f}  NATR: {natr:.2f}%\n"
            f"⇄ Спред: {spread:.2f}%"
        )
        msg_id = await send_telegram_message(msg)
        ACTIVE_COINS[symbol] = {'start': now, 'msg_id': msg_id}
        print(f"🚨 {symbol} стал активным!")
        
    elif not is_active and currently_active:
        # Монета стала неактивной
        duration = now - ACTIVE_COINS[symbol]['start']
        msg_id = ACTIVE_COINS[symbol]['msg_id']
        
        if msg_id:
            await delete_message(msg_id)
        
        if duration >= 60:
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            msg = f"✅ <b>{symbol}_USDT</b> — активность завершена\n⏱ Длительность: {minutes} мин {seconds} сек"
            await send_telegram_message(msg)
        
        del ACTIVE_COINS[symbol]
        print(f"✅ {symbol} стал неактивным")

async def cleanup_old_data():
    """Очищает устаревшие данные"""
    while BOT_RUNNING:
        now = time.time()
        to_remove = []
        
        for symbol, data in COIN_DATA.items():
            if now - data.get('last_update', 0) > 600:  # Удаляем данные старше 10 минут
                to_remove.append(symbol)
        
        for symbol in to_remove:
            del COIN_DATA[symbol]
            if symbol in ACTIVE_COINS:
                del ACTIVE_COINS[symbol]
        
        await asyncio.sleep(60)  # Проверяем каждую минуту

def start_bot_loop():
    """Запускает основной цикл бота"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Запускаем задачи
    tasks = [
        websocket_handler(),
        cleanup_old_data()
    ]
    
    loop.run_until_complete(asyncio.gather(*tasks))

# ——— Telegram меню ———
MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["Запустить бота", "Выключить бота"],
    ["Добавить монету", "Исключить монету"],
    ["Показать список монет", "Статус активных монет"],
    ["Показать данные монет", "Установить пороги"]
], resize_keyboard=True)

ADDING, REMOVING, SETTING_THRESHOLDS = range(3)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите действие:", reply_markup=MENU_KEYBOARD)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_RUNNING, VOLUME_THRESHOLD, SPREAD_THRESHOLD, NATR_THRESHOLD
    t = update.message.text
    
    if t == "Запустить бота":
        if not BOT_RUNNING:
            BOT_RUNNING = True
            threading.Thread(target=start_bot_loop, daemon=True).start()
            await update.message.reply_text("🚀 Бот запущен и подключен к WebSocket MEXC.", reply_markup=MENU_KEYBOARD)
        else:
            await update.message.reply_text("Бот уже работает.", reply_markup=MENU_KEYBOARD)
            
    elif t == "Выключить бота":
        BOT_RUNNING = False
        await update.message.reply_text("🛑 Бот остановлен.", reply_markup=MENU_KEYBOARD)
        
    elif t == "Добавить монету":
        await update.message.reply_text("Введите символ (например BTC):")
        return ADDING
        
    elif t == "Исключить монету":
        await update.message.reply_text("Введите символ:")
        return REMOVING
        
    elif t == "Показать список монет":
        text = "📋 Отслеживаемые монеты:\n" + "\n".join(f"• {coin}" for coin in sorted(WATCHLIST)) if WATCHLIST else "Список пуст."
        await update.message.reply_text(text, reply_markup=MENU_KEYBOARD)
        
    elif t == "Статус активных монет":
        if ACTIVE_COINS:
            lines = []
            for symbol, info in ACTIVE_COINS.items():
                duration = int(time.time() - info['start'])
                minutes = duration // 60
                seconds = duration % 60
                lines.append(f"🔥 {symbol}_USDT — {minutes}м {seconds}с")
            text = "⚡ Активные монеты:\n" + "\n".join(lines)
        else:
            text = "💤 Нет активных монет"
        await update.message.reply_text(text, reply_markup=MENU_KEYBOARD)
        
    elif t == "Показать данные монет":
        if COIN_DATA:
            lines = []
            for symbol, data in list(COIN_DATA.items())[:10]:  # Показываем первые 10
                volume = data.get('volume', 0)
                spread = data.get('spread', 0)
                natr = data.get('natr', 0)
                change = data.get('change', 0)
                lines.append(f"📊 {symbol}: V={volume:.0f}, S={spread:.3f}%, NATR={natr:.2f}%, Δ={change:.2f}%")
            text = "📈 Данные монет:\n" + "\n".join(lines)
        else:
            text = "📭 Нет данных о монетах"
        await update.message.reply_text(text, reply_markup=MENU_KEYBOARD)
        
    elif t == "Установить пороги":
        text = (f"📊 Текущие пороги:\n"
                f"• Объём: {VOLUME_THRESHOLD}\n"
                f"• Спред: {SPREAD_THRESHOLD}%\n"
                f"• NATR: {NATR_THRESHOLD}%\n\n"
                f"Введите новые значения через пробел:\nобъём спред natr")
        await update.message.reply_text(text)
        return SETTING_THRESHOLDS
        
    else:
        await update.message.reply_text("Выберите из меню:", reply_markup=MENU_KEYBOARD)
    
    return ConversationHandler.END

async def add_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.upper().strip().replace("_USDT", "")
    if len(coin) >= 2 and coin.isalnum():
        WATCHLIST.add(coin)
        save_watchlist()
        await update.message.reply_text(f"✅ {coin} добавлена в отслеживание.", reply_markup=MENU_KEYBOARD)
    else:
        await update.message.reply_text(f"❌ Некорректный символ: {coin}", reply_markup=MENU_KEYBOARD)
    return ConversationHandler.END

async def remove_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.upper().strip().replace("_USDT", "")
    if coin in WATCHLIST:
        WATCHLIST.remove(coin)
        save_watchlist()
        if coin in ACTIVE_COINS:
            del ACTIVE_COINS[coin]
        if coin in COIN_DATA:
            del COIN_DATA[coin]
        await update.message.reply_text(f"🗑️ {coin} удалена из отслеживания.", reply_markup=MENU_KEYBOARD)
    else:
        await update.message.reply_text(f"❌ {coin} не найдена в списке.", reply_markup=MENU_KEYBOARD)
    return ConversationHandler.END

async def set_thresholds(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global VOLUME_THRESHOLD, SPREAD_THRESHOLD, NATR_THRESHOLD
    try:
        parts = update.message.text.strip().split()
        if len(parts) == 3:
            VOLUME_THRESHOLD = float(parts[0])
            SPREAD_THRESHOLD = float(parts[1])
            NATR_THRESHOLD = float(parts[2])
            text = (f"✅ Пороги обновлены:\n"
                    f"• Объём: {VOLUME_THRESHOLD}\n"
                    f"• Спред: {SPREAD_THRESHOLD}%\n"
                    f"• NATR: {NATR_THRESHOLD}%")
        else:
            text = "❌ Введите 3 числа через пробел"
    except ValueError:
        text = "❌ Некорректные числа"
    
    await update.message.reply_text(text, reply_markup=MENU_KEYBOARD)
    return ConversationHandler.END

# ————————————
if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler)],
        states={
            ADDING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin)],
            REMOVING: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_coin)],
            SETTING_THRESHOLDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_thresholds)]
        },
        fallbacks=[],
        per_message=False
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    
    print("🚀 Telegram бот запущен...")
    app.run_polling()
