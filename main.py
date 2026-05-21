import asyncio
import logging
import io
import os
import sys

import qrcode
import cv2
import numpy as np
from PIL import Image, ImageDraw

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

# Полное логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

if not TOKEN:
    logging.critical("❌ Токен не найден в переменных окружения!")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"

# ИНИЦИАЛИЗАЦИЯ ИИ-ДВИЖКА WECHAT
# Создаем детектор один раз при старте, чтобы не тратить время на каждое фото
try:
    wechat_detector = cv2.wechat_qrcode_WeChatQRCode()
    logging.info("🧠 Нейросетевой движок WeChatQRCode успешно инициализирован!")
except Exception as e:
    logging.critical(f"❌ Не удалось запустить WeChatQRCode: {e}")
    sys.exit(1)

# Твой базовый алгоритм генерации QR-кода на JPG макете
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
    
    # Извлекаем матрицу напрямую через списки Python
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

# Функция распознавания QR через ИИ WeChat
def decode_qr_wechat(photo_bytes: bytes) -> str:
    try:
        # Декодируем байты в картинку OpenCV
        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return ""
        
        # Нейросеть сканирует всё изображение
        res, points = wechat_detector.detectAndDecode(img)
        
        if res and len(res) > 0 and res[0]:
            return res[0]
        return ""
    except Exception as e:
        logging.error(f"Ошибка ИИ-декодера WeChat: {e}")
        return ""

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Рад приветствовать! Бот успешно переведен на ИИ-движок WeChat.\n\n"
        "Отправь мне **фотографию бирки** с QR-кодом под любым углом или с плохим фокусом, "
        "и я сделаю идеальный кастомный QR-код без пробелов!"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Загружаю изображение в нейросеть WeChat...")
    
    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()
        
        # Запускаем тяжелую нейросеть в фоновом потоке системы, 
        # чтобы бот не «зависал» при одновременных запросах от других людей
        loop = asyncio.get_running_loop()
        detected_link = await loop.run_in_executor(None, decode_qr_wechat, photo_bytes)
        
        if not detected_link:
            await status_msg.edit_text(
                "❌ ИИ-сканер WeChat не смог найти читаемый код на фото.\n\n"
                "**Совет:** Попробуй сделать снимок чуть ровнее, без сильных световых бликов, или отправь ссылку текстом."
            )
            return
            
        await status_msg.edit_text(f"🔍 Код успешно распознан!\nСсылка: `{detected_link}`\n\n⏳ Накладываю на фирменный макет...")
        
        # Генерируем макет также в фоновом потоке
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка с бирки: `{detected_link}`"
        )
        await status_msg.delete()
        
    except Exception as e:
        logging.error(f"Ошибка обработки фото: {e}")
        await message.answer("💥 Произошла внутренняя ошибка сервера при обработке фото.")
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
    logging.info("🚀 Сверхмощный ИИ-бот на WeChatQRCode запущен в работу!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
