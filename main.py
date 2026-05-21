import asyncio
import logging
import io
import os
import sys
import numpy as np
import qrcode
import cv2
from PIL import Image, ImageDraw
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

if not TOKEN:
    logging.critical("❌ Токен не найден в переменных окружения!")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"
# Инициализируем детектор QR-кодов от OpenCV
qr_detector = cv2.QRCodeDetector()

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

# Функция для считывания QR-кода с фото бирки
def decode_qr_from_photo(photo_bytes: bytes) -> str:
    # Превращаем байты в массив numpy для OpenCV
    nparr = np.frombuffer(photo_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        return ""
        
    # Пытаемся найти и декодировать QR-код
    data, bbox, straight_qrcode = qr_detector.detectAndDecode(img)
    return data

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Теперь работать еще проще.\n\n"
        "Отправь мне **фотографию бирки**, на которой расположен QR-код. "
        "Я сам считаю его, распознаю ссылку и пришлю тебе готовый кастомный QR-код!"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Скачиваю фото и ищу QR-код...")
    
    try:
        # 1. Скачиваем фото бирки в память
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()
        
        # 2. Распознаем QR-код в фоновом потоке
        loop = asyncio.get_running_loop()
        detected_link = await loop.run_in_executor(None, decode_qr_from_photo, photo_bytes)
        
        if not detected_link:
            await status_msg.edit_text("❌ Не удалось найти или считать QR-код на этом фото. Попробуй сделать фото ближе, четче или прислать ссылку текстом.")
            return
            
        await status_msg.edit_text(f"🔍 Найдена ссылка:\n`{detected_link}`\n\n⏳ Генерирую новый QR-код...")
        
        # 3. Генерируем новый QR на основе распознанной ссылки
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nОригинальный текст/ссылка: `{detected_link}`"
        )
        await status_msg.delete()
        
    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("💥 Произошла ошибка при обработке изображения.")
        await status_msg.delete()

@dp.message()
async def handle_other(message: Message):
    await message.answer("Пожалуйста, отправь именно **фотографию бирки** с QR-кодом.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот авто-декодирования успешно запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
