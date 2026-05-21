import asyncio
import logging
import io
import os
import sys
import numpy as np
import qrcode
from PIL import Image, ImageDraw
from pyzbar.pyzbar import decode  # Намного более мощный и стабильный декодер
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

# Новая, стабильная функция декодирования через pyzbar
def decode_qr_from_photo(photo_bytes: bytes) -> str:
    try:
        # Открываем изображение через Pillow (это на 100% безопасно в потоках)
        img = Image.open(io.BytesIO(photo_bytes))
        
        # Находим все QR-коды на фото
        decoded_objects = decode(img)
        
        if not decoded_objects:
            return ""
            
        # Беру первый найденный QR-код и декодирую его в строку
        return decoded_objects[0].data.decode('utf-8')
    except Exception as e:
        logging.error(f"Ошибка внутри декодера pyzbar: {e}")
        return ""

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот обновлен и готов к работе.\n\n"
        "Отправь мне **фотографию бирки** с QR-кодом. Я считаю ссылку и сделаю кастомный QR!"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Скачиваю фото и сканирую QR-код...")
    
    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()
        
        # Безопасный запуск декодирования в фоновом потоке
        loop = asyncio.get_running_loop()
        detected_link = await loop.run_in_executor(None, decode_qr_from_photo, photo_bytes)
        
        if not detected_link:
            await status_msg.edit_text(
                "❌ Не удалось считать QR-код.\n\n"
                "**Совет:** Сделайте фото чуть ближе, без бликов от лампы и убедитесь, что камера сфокусировалась на коде."
            )
            return
            
        await status_msg.edit_text(f"🔍 Код успешно считан!\nСсылка: `{detected_link}`\n\n⏳ Создаю кастомный QR...")
        
        # Генерация нового QR
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка: `{detected_link}`"
        )
        await status_msg.delete()
        
    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("💥 Произошла ошибка при обработке изображения сервера.")
        await status_msg.delete()

@dp.message()
async def handle_other(message: Message):
    await message.answer("Пожалуйста, отправь мне **фотографию бирки** (как картинку, а не файл).")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Новый бот на pyzbar успешно запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
