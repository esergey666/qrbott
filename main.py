import asyncio
import logging
import io
import os
import sys

import qrcode
import numpy as np
from PIL import Image, ImageDraw
import zxingcpp  # Сверхстабильный и быстрый ИИ-декодер от Google

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

# Локальная функция декодирования через движок Google ZXing
def decode_qr_local(photo_bytes: bytes) -> str:
    try:
        # Открываем картинку через Pillow
        img = Image.open(io.BytesIO(photo_bytes))
        
        # Запускаем чтение кода (работает мгновенно и распознает сложные/помятые QR)
        results = zxingcpp.read_barcodes(img)
        
        if results:
            return results[0].text
        return ""
    except Exception as e:
        logging.error(f"Ошибка декодирования zxing: {e}")
        return ""

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Бот полностью стабилизирован для одновременной работы множества пользователей!\n\n"
        "Просто отправь мне **фотографию бирки** с QR-кодом, и я сделает кастомный QR без пробелов."
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Сканирую фото бирки...")
    
    try:
        # Получаем фото в максимальном качестве
        photo = message.photo[-1]
        
        # Скачиваем файл в память асинхронно
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()
        
        # Запускаем декодирование в изолированном фоновом потоке, 
        # чтобы другие пользователи не вешали бота
        loop = asyncio.get_running_loop()
        detected_link = await loop.run_in_executor(None, decode_qr_local, photo_bytes)
        
        if not detected_link:
            await status_msg.edit_text(
                "❌ Не удалось считать QR-код на фото.\n\n"
                "Попробуй сделать фото чуть ближе или пришли ссылку обычным текстом!"
            )
            return
            
        await status_msg.edit_text(f"🔍 Код успешно считан!\nСсылка: `{detected_link}`\n\n⏳ Создаю кастомный QR...")
        
        # Генерируем новый QR также в фоновом потоке
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка: `{detected_link}`"
        )
        await status_msg.delete()
        
    except Exception as e:
        logging.error(f"Ошибка при обработке фото у пользователя {message.from_user.id}: {e}")
        await message.answer("💥 Произошла ошибка при обработке картинки.")
        await status_msg.delete()

# Поддержка обычных текстовых ссылок (тоже многопоточная)
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
    # Очищаем очередь старых запросов, которые накопились, пока бот висел
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Многопоточный ИИ-бот на ZXing запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
