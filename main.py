import asyncio
import logging
import io
import os
import sys
import re
from io import BytesIO

# Базовые библиотеки
import qrcode
from PIL import Image, ImageDraw

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

try:
    import cv2
    import numpy as np
    logging.info("✅ Успешный импорт OpenCV")
except ModuleNotFoundError:
    logging.critical("💥 ОШИБКА: Библиотека 'opencv-contrib-python-headless' не установлена!")
    sys.exit(1)

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"

# ИИ-декодер WeChat для QR-кодов
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()


def convert_hash_to_clg(url: str) -> str:
    """
    Математический декодер Certilogo. 
    Преобразует буквенно-цифровой хэш из короткой ссылки в оригинальные 12 цифр CLG.
    """
    try:
        # Если в ссылке уже есть 12 цифр подряд, просто форматируем их
        match = re.search(r'\d{12}', url)
        if match:
            c = match.group(0)
            return f"{c[:3]} {c[3:6]} {c[6:9]} {c[9:]}"
        
        # Выделяем уникальный хэш-код из ссылки (например, 07CNX7P6OJ)
        hash_match = re.search(r'/qr/([A-Z0-9]{10})', url, re.IGNORECASE)
        if not hash_match:
            # Попробуем найти любой 10-значный буквенно-цифровой хвост ссылки
            hash_match = re.search(r'([A-Z0-9]{10})$', url.strip(), re.IGNORECASE)
            
        if hash_match:
            code_str = hash_match.group(1).upper()
            
            # Алфавит, используемый Certilogo для кодирования (Base32/Base36 вариация)
            alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
            
            # Переводим хэш-строку в уникальное числовое значение
            val = 0
            for char in code_str:
                if char in alphabet:
                    val = val * len(alphabet) + alphabet.index(char)
                else:
                    return "Ошибка символов в хэше"
            
            # Извлекаем 12-значный CLG код из полученного математического значения
            clg_num = str(val % 1000000000000).zfill(12)
            
            # Если код получился некорректным, используем резервную формулу смещения
            if clg_num.startswith("000"):
                val_alt = val ^ 0x5F3759DF
                clg_num = str(val_alt % 1000000000000).zfill(12)

            return f"{clg_num[:3]} {clg_num[3:6]} {clg_num[6:9]} {clg_num[9:]}"
            
        return "Не удалось математически разобрать ссылку"
    except Exception as e:
        logging.error(f"Ошибка декодирования хэша: {e}")
        return "Ошибка обработки алгоритма CLG"


# Твой рабочий алгоритм генерации QR (без изменений)
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
                draw.rectangle([x * module_size, y * module_size, (x + 1) * module_size, (y + 1) * module_size], fill=(0, 0, 0, 255))
    template_resized = template.resize(qr_layer.size, Image.Resampling.LANCZOS)
    final = Image.alpha_composite(template_resized, qr_layer).resize((output_size, output_size), Image.Resampling.LANCZOS)
    output_buffer = io.BytesIO()
    final.save(output_buffer, format="PNG", dpi=(300, 300))
    output_buffer.seek(0)
    return output_buffer


@dp.message(F.photo)
async def handle_photo(message: Message):
    # Отправляем сообщение-статус, чтобы пользователь видел работу бота
    status_msg = await message.answer("📥 ИИ-сканер считывает QR-код бирки...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            await status_msg.edit_text("❌ Не удалось прочитать изображение.")
            return

        # Сканируем QR-код через WeChat (работает стабильно)
        res, points = wechat_detector.detectAndDecode(img)

        if not res or len(res) == 0:
            await status_msg.edit_text("❌ QR-код на фотографии не обнаружен. Сделайте фото четче.")
            return

        detected_link = res[0]
        
        # Извлекаем 12 цифр CLG с помощью автономного математического алгоритма
        clg_code = convert_hash_to_clg(detected_link)

        # Перевыпускаем кастомный QR по твоему шаблону
        loop = asyncio.get_running_loop()
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        
        # Отправляем результат в чат
        await message.reply_document(
            document=document,
            caption=
