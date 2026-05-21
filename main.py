import asyncio
import logging
import io
import os
import sys

# Настраиваем логирование на максимум (DEBUG покажет вообще всё)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    stream=sys.stdout
)

logging.info("--- ЗАПУСК ДИАГНОСТИКИ БОТА ---")

# 1. Проверяем переменные окружения
api_token = os.getenv("API_TOKEN")
bot_token = os.getenv("BOT_TOKEN")

logging.info(f"Проверка API_TOKEN: {'НАЙДЕН' if api_token else 'ОТСУТСТВУЕТ (None)'}")
logging.info(f"Проверка BOT_TOKEN: {'НАЙДЕН' if bot_token else 'ОТСУТСТВУЕТ (None)'}")

# Берем то, что нашлось
TOKEN = api_token or bot_token

if not TOKEN:
    logging.critical("❌ ОШИБКА: Ни API_TOKEN, ни BOT_TOKEN не найдены в системе! Бот останавливается.")
    sys.exit(1)
else:
    # Выводим первые и последние символы токена для сверки, скрывая середину
    logging.info(f"✅ Токен успешно считан: {TOKEN[:10]}...{TOKEN[-5:]}")

# 2. Проверяем наличие файла макета
TEMPLATE_PATH = "maket.jpg"
if os.path.exists(TEMPLATE_PATH):
    logging.info(f"✅ Файл макета '{TEMPLATE_PATH}' успешно найден в корневой папке.")
else:
    logging.warning(f"⚠️ ВНИМАНИЕ: Файл '{TEMPLATE_PATH}' НЕ НАЙДЕН! Положи его рядом с main.py на GitHub.")

# Импортируем тяжелые библиотеки после проверки окружения
try:
    import numpy as np
    import qrcode
    from PIL import Image, ImageDraw
    from aiogram import Bot, Dispatcher, F
    from aiogram.types import Message, BufferedInputFile
    from aiogram.filters import CommandStart
    logging.info("✅ Все библиотеки (aiogram, numpy, qrcode, pillow) импортированы без ошибок.")
except Exception as e:
    logging.critical(f"❌ Ошибка импорта модулей: {e}")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

def generate_qr_on_template(data: str, output_size=1200) -> io.BytesIO:
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Файл макета '{TEMPLATE_PATH}' не найден!")
    template = Image.open(TEMPLATE_PATH).convert('RGBA')
    qr = qrcode.QRCode(version=3, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=1, border=0, mask_pattern=3)
    qr.add_data(data)
    qr.make(fit=True)
    qr_matrix = np.array(qr.get_matrix(), dtype=np.uint8)
    module_size = template.width // 29
    qr_layer = Image.new('RGBA', (29 * module_size, 29 * module_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(qr_layer)
    for y in range(29):
        for x in range(29):
            if qr_matrix[y, x] == 1:
                left = x * module_size
                top = y * module_size
                right = left + module_size
                bottom = top + module_size
                draw.rectangle([left, top, right, bottom], fill=(0, 0, 0, 255))
    template_resized = template.resize(qr_layer.size, Image.Resampling.LANCZOS)
    final = Image.alpha_composite(template_resized, qr_layer)
    final = final.resize((output_size, output_size), Image.Resampling.LANCZOS)
    output_buffer = io.BytesIO()
    final.save(output_buffer, format="PNG", dpi=(300, 300))
    output_buffer.seek(0)
    return output_buffer

@dp.message(CommandStart())
async def cmd_start(message: Message):
    logging.info(f"👤 Пользователь @{message.from_user.username} (ID: {message.from_user.id}) нажал /start")
    await message.answer("👋 Привет! Отправь мне ссылку, и я сделаю QR-код без пробелов.")

@dp.message(F.text)
async def handle_link(message: Message):
    link_data = message.text
    logging.info(f"📥 Получена ссылка от ID {message.from_user.id}: {link_data}")
    status_msg = await message.answer("⏳ Генерирую фирменный QR-код...")
    try:
        loop = asyncio.get_running_loop()
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, link_data)
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(document=document, caption=f"✅ Готово!\nСсылка: `{link_data}`")
        logging.info(f"📤 QR-код успешно отправлен пользователю ID {message.from_user.id}")
        await status_msg.delete()
    except Exception as e:
        logging.error(f"💥 Ошибка генерации: {e}")
        await message.answer("💥 Произошла ошибка при обработке.")
        await status_msg.delete()

async def main():
    logging.info("🚀 Запускаю метод dp.start_polling(bot)...")
    try:
        # Сбрасываем вебхуки, если они были установлены хостингом по ошибке
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("🧼 Предыдущие зависшие сообщения очищены.")
        await dp.start_polling(bot)
    except Exception as e:
        logging.critical(f"❌ Бот не смог подключиться к Telegram API: {e}")

if __name__ == '__main__':
    asyncio.run(main())
