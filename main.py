import asyncio
import logging
import io
import os
import sys

import qrcode
from PIL import Image, ImageDraw
from qreader import QReader  # Мощный ИИ-декодер на чистом Python

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

if not TOKEN:
    logging.critical("❌ Токен не найден в переменных окружения!")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"
# Инициализируем ИИ-читалку кодов
qreader = QReader()

# Твой алгоритм генерации QR-кода на макете
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

# Декодирование через qreader
def decode_qr_local(photo_bytes: bytes) -> str:
    try:
        # Открываем изображение через Pillow
        img = Image.open(io.BytesIO(photo_bytes)).convert('RGB')
        
        # qreader ищет QR-коды на лету
        decoded_text = qreader.detect_and_decode(image=img)
        
        if decoded_text and decoded_text[0]:
            return decoded_text[0]
        return ""
    except Exception as e:
        logging.error(f"Ошибка декодирования QReader: {e}")
        return ""

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Бот готов к работе в многопоточном ИИ-режиме!\n\n"
        "Отправь мне **фотографию бирки** с QR-кодом (даже сложную или помятую), и я сделаю кастомный QR без пробелов."
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Сканирую фото бирки ИИ-радарчиком...")
    
    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()
        
        # Выполняем в фоновом потоке, чтобы бот не зависал при наплыве юзеров
        loop = asyncio.get_running_loop()
        detected_link = await loop.run_in_executor(None, decode_qr_local, photo_bytes)
        
        if not detected_link:
            await status_msg.edit_text(
                "❌ Не удалось считать QR-код на фото.\n\n"
                "Попробуй сделать фото чуть ближе или пришли ссылку обычным текстом!"
            )
            return
            
        await status_msg.edit_text(f"🔍 Код успешно считан!\nСсылка: `{detected_link}`\n\n⏳ Создаю кастомный QR...")
        
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка: `{detected_link}`"
        )
        await status_msg.delete()
        
    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("💥 Произошла ошибка при обработке картинки.")
        await status_msg.delete()

@dp.message(F.text)
async def handle_text_link(message: Message):
    link_data = message.text
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
    logging.info("🚀 Финальный ИИ-бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
