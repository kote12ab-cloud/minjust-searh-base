import csv
import re
import os
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# === НАСТРОЙКИ ===
CSV_FILE = 'exportfsm.csv'
TOKEN = " "  # ✅ Убедитесь, что токен правильный
RESULTS_PER_PAGE = 5  # Количество записей на странице

# Глобальная база: ID -> описание
EXTREMIST_DATABASE = {}

# Храним время последнего нажатия кнопки (защита от спама)
LAST_BUTTON_PRESS = {}

# === Экранирование для MarkdownV2 ===
def escape_markdown_v2(text: str) -> str:
    """
    Экранирует специальные символы для Telegram MarkdownV2
    """
    if not text:
        return ""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

# === Очистка и парсинг строки CSV ===
def clean_text(text):
    """Очищает текст от лишних пробелов и кавычек"""
    return re.sub(r'\s+', ' ', text.strip().strip('"'))

def parse_line_robust(line):
    """Ручной парсинг строки с учётом кавычек и дат"""
    line = line.strip()
    if not line:
        return []

    # Удаляем дату в конце: 01.01.2024
    line = re.sub(r';?\s*\d{2}\.\d{2}\.\d{4}$', '', line)
    # Удаляем мусор в начале
    line = re.sub(r'^[!?»“"»\s]+', '', line)

    parts = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            in_quotes = not in_quotes
        if c == ';' and not in_quotes:
            parts.append(''.join(current))
            current = []
        else:
            current.append(c)
        i += 1
    if current:
        parts.append(''.join(current))

    items = []
    i = 0
    while i < len(parts):
        part = clean_text(parts[i])
        if part.isdigit():
            item_id = int(part)
            if i + 1 < len(parts):
                desc = clean_text(parts[i + 1])
                if desc and not desc.isdigit() and len(desc) > 5:
                    items.append((item_id, desc))
                    i += 2
                    continue
        i += 1
    return items

# === Загрузка базы из CSV ===
def load_database():
    if not os.path.exists(CSV_FILE):
        print(f"❌ Файл {CSV_FILE} не найден в папке: {os.getcwd()}")
        return False

    try:
        with open(CSV_FILE, 'r', encoding='cp1251') as f:
            content = f.read()
        print(f"✅ Файл {CSV_FILE} прочитан (кодировка cp1251).")
    except Exception as e:
        print(f"❌ Ошибка чтения файла: {e}")
        return False

    lines = content.splitlines()
    parsed_count = 0
    for line_num, line in enumerate(lines, 1):
        try:
            pairs = parse_line_robust(line)
            for item_id, desc in pairs:
                EXTREMIST_DATABASE[item_id] = desc
                parsed_count += 1
        except Exception as e:
            print(f"⚠️ Ошибка парсинга строки {line_num}: {e}")
            continue

    print(f"✅ Загружено {len(EXTREMIST_DATABASE)} записей.")
    return True

# === Поиск по ID или тексту ===
def search(query: str):
    """Ищет по ID или по вхождению подстроки"""
    if not query.strip():
        return []
    query_clean = query.strip().lower()
    results = []

    for item_id, desc in EXTREMIST_DATABASE.items():
        if query_clean.isdigit() and int(query_clean) == item_id:
            results.append((item_id, desc))
        elif query_clean in desc.lower():
            results.append((item_id, desc))

    return sorted(results, key=lambda x: x[0])

# === Telegram: /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение"""
    welcome_text = (
        "🛡️ *Не офицальынй  бот поиска по Федеральному списку экстремистских материалов РФ*\n\n"
        "Отправьте:\n"
        "• Номер записи (например, `3632`)\n"
        "• Или слово (например, `Книга`, `Брошура`, `статья`, `музыка`)\n\n"
        "⚠️ Содержание может быть шокирующим. Только для правового ознакомления."
    )
    safe_text = escape_markdown_v2(welcome_text)
    await update.message.reply_text(
        text=safe_text,
        parse_mode='MarkdownV2'
    )

# === Telegram: обработка текстовых сообщений ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает запрос пользователя"""
    query = update.message.text.strip()
    if not query:
        await update.message.reply_text("Введите запрос.")
        return

    results = search(query)
    if not results:
        escaped_query = escape_markdown_v2(query)
        await update.message.reply_text(
            text=f"❌ Ничего не найдено по запросу: *{escaped_query}*",
            parse_mode='MarkdownV2'
        )
        return

    # Сохраняем результаты
    context.user_data['results'] = results
    context.user_data['query'] = query
    context.user_data['current_page'] = 0

    # Показываем первую страницу
    await send_page(update, context)

# === Telegram: отправка страницы с пагинацией ===
async def send_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет текущую страницу результатов"""
    results = context.user_data.get('results', [])
    page = context.user_data.get('current_page', 0)
    total_pages = (len(results) + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE
    start_idx = page * RESULTS_PER_PAGE
    end_idx = start_idx + RESULTS_PER_PAGE
    page_results = results[start_idx:end_idx]

    # Формируем текст
    escaped_query = escape_markdown_v2(context.user_data.get('query', ''))
    response = f"🔎 Найдено: *{len(results)}* записей по запросу `{escaped_query}`\n"
    response += f"📄 Страница *{page + 1}/{total_pages}*\n\n"

    for item_id, desc in page_results:
        preview = desc[:200] + "..." if len(desc) > 200 else desc
        escaped_desc = escape_markdown_v2(preview)
        escaped_id = escape_markdown_v2(str(item_id))
        response += f"📌 *№ {escaped_id}*\n{escaped_desc}\n\n"

    # Кнопки
    keyboard = []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀️ Назад", callback_data="prev"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("Далее ▶️", callback_data="next"))
    if row:
        keyboard.append(row)
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Попытка редактировать или отправить
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=response,
                reply_markup=reply_markup,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
        else:
            await update.message.reply_text(
                text=response,
                reply_markup=reply_markup,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
    except Exception as e:
        error_msg = str(e).lower()
        if "message is not modified" in error_msg:
            await update.callback_query.answer()
        elif "too many requests" in error_msg:
            await update.callback_query.answer("⏳ Слишком много запросов. Подождите...")
        else:
            print(f"❌ Ошибка при редактировании: {e}")
            # Резерв: отправка нового сообщения
            try:
                await update.callback_query.message.reply_text(
                    text="📬 Результаты (обновлено):",
                    parse_mode='MarkdownV2'
                )
                await update.callback_query.message.reply_text(
                    text=response,
                    reply_markup=reply_markup,
                    parse_mode='MarkdownV2',
                    disable_web_page_preview=True
                )
            except:
                pass

# === Telegram: обработка кнопок (с защитой от спама) ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий 'Назад' / 'Далее'"""
    query = update.callback_query
    user_id = query.from_user.id
    now = datetime.now()

    # Защита от быстрых нажатий
    last_time = LAST_BUTTON_PRESS.get(user_id)
    if last_time and (now - last_time) < timedelta(milliseconds=800):
        await query.answer("⏳ Подождите...", show_alert=False)
        return

    LAST_BUTTON_PRESS[user_id] = now

    try:
        await query.answer()  # Подтверждаем нажатие
    except:
        pass  # Игнорируем, если уже отвечено

    page = context.user_data.get('current_page', 0)
    results = context.user_data.get('results', [])
    total_pages = (len(results) + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE

    if query.data == "next" and page < total_pages - 1:
        context.user_data['current_page'] = page + 1
        await send_page(update, context)
    elif query.data == "prev" and page > 0:
        context.user_data['current_page'] = page - 1
        await send_page(update, context)
    else:
        try:
            await query.edit_message_text(
                text="❌ Эта страница недоступна.",
                parse_mode='MarkdownV2'
            )
        except:
            pass

# === Главная функция ===
def main():
    print("📁 Работаем в папке:", os.getcwd())
    time.sleep(0.5)

    # Загружаем базу
    if not load_database():
        print("🔴 Ошибка загрузки базы данных.")
        input("Нажмите Enter для выхода...")
        return

    # Проверка токена
    if TOKEN == "YOUR_BOT_TOKEN_HERE" or "YOUR_BOT_TOKEN_HERE" in TOKEN:
        print("❌ Замените TOKEN на настоящий токен бота!")
        input("Нажмите Enter для выхода...")
        return

    print("🚀 Создаём Telegram-бота...")
    time.sleep(0.5)

    try:
        app = Application.builder().token(TOKEN).build()

        # Обработчики
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(CallbackQueryHandler(button_handler))

        print("✅ Бот готов. Запускаем polling...")
        app.run_polling(drop_pending_updates=True)

    except Exception as e:
        print(f"🔴 Ошибка запуска бота: {e}")
        import traceback
        traceback.print_exc()
        input("Нажмите Enter для выхода...")

if __name__ == "__main__":
    main()