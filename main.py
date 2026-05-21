import asyncio
import logging
import io
import os
import sys
from io import BytesIO

# Базовые библиотеки
import qrcode
from PIL import Image, ImageDraw

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Перехватываем ошибки импорта OpenCV, так как на хостинге он ставится долго
try:
    import cv2
    import numpy as np
    logging.info("✅ Успешный импорт OpenCVcontrib")
except ModuleNotFoundError:
    logging.critical("💥 ОШИБКА: Библиотека 'opencv-contrib-python-headless' не установлена! Проверьте requirements.txt")
    sys.exit(1)

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

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

# ИНИЦИАЛИЗАЦИЯ ИИ-ДЕКОДЕРА WECHAT (делается один раз при запуске бота)
# Это сверхмощная нейросеть, встроенная в OpenCVcontrib
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()

# Функция декодирования через ИИ-движок WeChat
def decode_qr_from_photo(photo_bytes: bytes) -> str:
    try:
        # Превращаем байты в формат, понятный OpenCV
        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return ""
        
        # Находим и декодируем QR-код с помощью нейросети
        # res — это список найденных строк (WeChat видит все коды в кадре)
        res, points = wechat_detector.detectAndDecode(img)
        
        # Если хотя бы один код найден, берем первый
        if res:
            return res[0]
        else:
            return ""
    except Exception as e:
        logging.error(f"Ошибка внутри ИИ-декодера WeChat: {e}")
        return ""

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот обновлен до уровня ИИ-сканера.\n\n"
        "Я использую сверхмощный нейросетевой движок WeChat для распознавания QR-кодов. "
        "Просто отправь мне фото бирки под любым углом и с любым освещением, я считаю код и сделаю кастомный QR!"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Принял фото. Запускаю ИИ-сканер для поиска QR-кода...")
    
    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()
        
        # Мощное ИИ-декодирование в фоновом потоке
        loop = asyncio.get_running_loop()
        detected_link = await loop.run_in_executor(None, decode_qr_from_photo, photo_bytes)
        
        if not detected_link:
            await status_msg.edit_text(
                "❌ К сожалению, даже ИИ-сканер не смог найти QR-код на этом фото.\n\n"
                "Это значит, что изображение слишком размыто, код полностью засвечен бликом от лампы или перекрыт грязью."
            )
            return
            
        await status_msg.edit_text(f"🔍 ИИ-сканер успешно распознал код!\nСсылка: `{detected_link}`\n\n⏳ Создаю кастомный QR...")
        
        # Генерация нового QR
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка из бирки: `{detected_link}`"
        )
        await status_msg.delete()
        
    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("💥 Произошла ошибка при обработке изображения на сервере.")
        await status_msg.delete()

@dp.message()
async def handle_other(message: Message):
    await message.answer("Пожалуйста, отправь мне именно **фотографию бирки**.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Сверхмощный ИИ-бот на WeChatQRCode запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
