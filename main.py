
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

def calculate_metrics(symbol, data):
    """Рассчитывает метрики на основе данных свечи"""
    open_price = float(data.get('o', 0))
    close_price = float(data.get('c', 0))
    high_price = float(data.get('h', 0))
    low_price = float(data.get('l', 0))
    volume = float(data.get('v', 0))
    
    if close_price == 0 or open_price == 0:
        return None
    
    # NATR (Normalized Average True Range)
    natr = (high_price - low_price) / close_price * 100
    
    # Изменение цены
    change = (close_price - open_price) / open_price * 100
    
    return {
        'symbol': symbol,
        'volume': volume,
        'natr': natr,
        'change': change,
        'close': close_price
    }

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
                await asyncio.sleep(5)
                continue
            
            async with websockets.connect(MEXC_WS_URL) as websocket:
                # Подписываемся на данные
                subscribe_msg = {
                    "method": "SUBSCRIPTION",
                    "params": subscriptions
                }
                await websocket.send(json.dumps(subscribe_msg))
                print(f"📡 Подписались на {len(subscriptions)} потоков данных")
                
                async for message in websocket:
                    if not BOT_RUNNING:
                        break
                        
                    try:
                        data = json.loads(message)
                        await process_websocket_data(data)
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        print(f"Ошибка обработки данных: {e}")
                        
        except Exception as e:
            print(f"Ошибка WebSocket: {e}")
            if BOT_RUNNING:
                await asyncio.sleep(5)  # Ждем перед переподключением

async def process_websocket_data(data):
    """Обрабатывает данные от WebSocket"""
    global COIN_DATA, ACTIVE_COINS
    
    if 'c' not in data or 's' not in data:
        return
    
    symbol_full = data['s']
    if not symbol_full.endswith('USDT'):
        return
    
    symbol = symbol_full.replace('USDT', '')
    
    if symbol not in WATCHLIST:
        return
    
    # Обновляем данные свечей
    if 'k' in data:
        kline_data = data['k']
        metrics = calculate_metrics(symbol, kline_data)
        
        if metrics:
            COIN_DATA[symbol] = {
                **metrics,
                'last_update': time.time(),
                'spread': COIN_DATA.get(symbol, {}).get('spread', 0)
            }
    
    # Обновляем данные спреда
    elif 'b' in data and 'a' in data:  # bookTicker данные
        bid = float(data['b'])
        ask = float(data['a'])
        if bid > 0:
            spread = (ask - bid) / bid * 100
            if symbol in COIN_DATA:
                COIN_DATA[symbol]['spread'] = spread
            else:
                COIN_DATA[symbol] = {'spread': spread, 'last_update': time.time()}
    
    # Проверяем условия активности
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
    
    is_active = (volume >= VOLUME_THRESHOLD and 
                 spread >= SPREAD_THRESHOLD and 
                 natr >= NATR_THRESHOLD)
    
    currently_active = symbol in ACTIVE_COINS
    
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
    ["Показать список монет", "Статус активных монет"]
], resize_keyboard=True)

ADDING, REMOVING = range(2)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите действие:", reply_markup=MENU_KEYBOARD)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_RUNNING
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
        
    else:
        await update.message.reply_text("Выберите из меню:", reply_markup=MENU_KEYBOARD)
    
    return ConversationHandler.END

async def add_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.upper().strip().replace("_USDT", "")
    # Простая проверка валидности символа
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
        # Удаляем из активных монет и данных
        if coin in ACTIVE_COINS:
            del ACTIVE_COINS[coin]
        if coin in COIN_DATA:
            del COIN_DATA[coin]
        await update.message.reply_text(f"🗑️ {coin} удалена из отслеживания.", reply_markup=MENU_KEYBOARD)
    else:
        await update.message.reply_text(f"❌ {coin} не найдена в списке.", reply_markup=MENU_KEYBOARD)
    return ConversationHandler.END

# ————————————
if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler)],
        states={
            ADDING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin)],
            REMOVING: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_coin)]
        },
        fallbacks=[],
        per_message=False
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    
    print("🚀 Telegram бот запущен...")
    app.run_polling()
