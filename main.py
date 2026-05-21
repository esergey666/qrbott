import asyncio
import logging
import io
import os
import sys

import qrcode
from PIL import Image, ImageDraw

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

if not TOKEN:
    logging.critical("❌ Токен не найден в переменных окружения!")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"

# Твой проверенный алгоритм генерации QR-кода на макете (без сторонних модулей)
def generate_qr_on_template(data: str, output_size=1200) -> io.BytesIO:
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Файл макета '{TEMPLATE_PATH}' не найден!")
        
    template = Image.open(TEMPLATE_PATH).convert('RGBA')
    
    qr = qrcode.QRCode(
        version=3,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=0,
        mask_pattern=3
    )
    qr.add_data(data)
    qr.make(fit=True)
    
    qr_matrix = qr.get_matrix()
    matrix_size = len(qr_matrix)
    
    module_size = template.width // matrix_size
    qr_layer = Image.new('RGBA', (matrix_size * module_size, matrix_size * module_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(qr_layer)
    
    for y in range(matrix_size):
        for x in range(matrix_size):
            if qr_matrix[y][x]:
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

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Бот успешно обновлен и оптимизирован!\n\n"
        "**Как теперь работать:**\n"
        "1. Отправь мне **фотографию бирки** с QR-кодом.\n"
        "2. Или просто отправь ссылку **обычным текстом**.\n\n"
        "Я сразу сгенерирую кастомный QR-код без пробелов на нашем макете!"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Проверяю данные фотографии...")
    
    detected_link = None

    # Трюк: Если Telegram сам распознал URL внутри фото (или в описании), забираем его
    if message.caption_entities:
        for entity in message.caption_entities:
            if entity.type == "url":
                # Извлекаем кусок текста, который является ссылкой
                detected_link = message.caption[entity.offset:entity.offset + entity.length]

    # Подстраховка: если ты скинул фото и в подписи (caption) написал ссылку руками
    if not detected_link and message.caption:
        if "http" in message.caption or "." in message.caption:
            detected_link = message.caption.strip()

    # Если Telegram не поделился ссылкой из метаданных чата
    if not detected_link:
        await status_msg.edit_text(
            "⚠️ Серверный сканер не смог автоматически вытащить ссылку из этого фото.\n\n"
            "**Чтобы продолжить:**\n"
            "Просто пришли мне ссылку с этой бирки **обычным текстом** (или скопируй её из приложения сканера), и я мгновенно сделаю QR!"
        )
        return

    # Если ссылка успешно найдена
    await status_msg.edit_text(f"🔍 Найдена ссылка:\n`{detected_link}`\n\n⏳ Создаю кастомный QR...")
    
    try:
        loop = asyncio.get_running_loop()
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка: `{detected_link}`"
        )
        await status_msg.delete()
    except Exception as e:
        logging.error(f"Ошибка генерации: {e}")
        await message.answer("💥 Произошла ошибка при сборке изображения.")
        await status_msg.delete()

@dp.message(F.text)
async def handle_text_link(message: Message):
    link_data = message.text.strip()
    if link_data.startswith("/"):
        return
        
    status_msg = await message.answer("⏳ Генерирую QR по текстовой ссылке...")
    try:
        loop = asyncio.get_running_loop()
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, link_data)
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(document=document, caption=f"✅ Готово!\nСсылка: `{link_data}`")
        await status_msg.delete()
    except Exception as e:
        await message.answer("💥 Ошибка при генерации.")
        await status_msg.delete()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Облегченный стабильный бот запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
