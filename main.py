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
    import pytesseract  
    logging.info("✅ Успешный импорт OpenCV и PyTesseract")
except ModuleNotFoundError as e:
    logging.critical(f"💥 ОШИБКА: Проверьте зависимости! {e}")
    sys.exit(1)

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"

# Включаем WeChat QR декодер
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()


def crop_and_ocr_clg(img, points) -> str:
    """
    Стабильно извлекает координаты QR-кода, аккуратно вырезает 
    строку с CLG над ним и распознает цифры через Tesseract.
    """
    try:
        h, w, _ = img.shape
        
        # WeChat возвращает список массивов. Переводим в плоский массив точек int
        pts = np.array(points[0]).reshape(4, 2).astype(int)
        
        # Находим крайние координаты QR-кода
        min_x = np.min(pts[:, 0])
        max_x = np.max(pts[:, 0])
        min_y = np.min(pts[:, 1])
        
        qr_width = max_x - min_x
        if qr_width <= 0:
            qr_width = 200

        # Вычисляем зону строго над QR-кодом
        # Поднимаемся вверх на 55% от ширины QR и берем ширину с запасом 20% вбок
        crop_top = max(0, min_y - int(qr_width * 0.55))
        crop_bottom = max(0, min_y - int(qr_width * 0.05)) # Чуть выше рамки QR
        crop_left = max(0, min_x - int(qr_width * 0.2))
        crop_right = min(w, max_x + int(qr_width * 0.2))
        
        # Проверка на корректность получившихся координат
        if crop_bottom <= crop_top or crop_right <= crop_left:
            return "Ошибка позиционирования кода"
            
        # Вырезаем область (Region of Interest)
        roi = img[crop_top:crop_bottom, crop_left:crop_right]
        
        # --- ПРЕДОБРАБОТКА ИЗОБРАЖЕНИЯ ДЛЯ СТАБИЛЬНОГО OCR ---
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        
        # Увеличиваем картинку в 3 раза (Tesseract любит крупные шрифты)
        resized = cv2.resize(gray, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        
        # Легкое размытие, чтобы сгладить текстуру ткани
        blurred = cv2.GaussianBlur(resized, (5, 5), 0)
        
        # Адаптивный ЧБ фильтр: убирает перепады света и тени
        thresh = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY, 11, 2
        )
        
        # Конфигурация Tesseract: psm 7 (поиск одной строки текста)
        custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789'
        text = pytesseract.image_to_string(thresh, config=custom_config)
        
        # Очищаем результат от пробелов и мусора
        cleaned = re.sub(r'\s+', '', text)
        
        # Поиск 12 цифр подряд
        match = re.search(r'\d{12}', cleaned)
        if match:
            code = match.group(0)
            return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"
            
        # Резервный вариант: если цифры разбились пробелами, собираем всё что нашли
        digits_only = ''.join(filter(str.isdigit, cleaned))
        if len(digits_only) == 12:
            return f"{digits_only[:3]} {digits_only[3:6]} {digits_only[6:9]} {digits_only[9:]}"
        elif len(digits_only) >= 10:
            return f"Частично: {digits_only}"
            
        return "Не удалось считать цифры над QR"
        
    except Exception as e:
        logging.error(f"Ошибка в блоке кропа/OCR: {e}")
        return "Ошибка обработки текста"


# Алгоритм генерации QR (без изменений)
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
    status_msg = await message.answer("📥 Обрабатываю изображение бирки...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            await status_msg.edit_text("❌ Ошибка чтения файла изображения.")
            return

        # Находим QR-код
        res, points = wechat_detector.detectAndDecode(img)

        if not res or points is None or len(points) == 0:
            await status_msg.edit_text("❌ QR-код не обнаружен. Сделайте фото ровнее.")
            return

        detected_link = res[0]

        # Безопасный запуск OCR в фоновом потоке, чтобы бот не зависал
        loop = asyncio.get_running_loop()
        clg_code = await loop.run_in_executor(None, crop_and_ocr_clg, img, points)

        # Генерируем кастомный QR
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        
        await message.reply_document(
            document=document,
            caption=f"✅ **Обработка завершена!**\n\n"
                    f"🔢 **Код CLG:** `{clg_code}`\n"
                    f"🔗 **Ссылка:** `{detected_link}`"
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Глобальная ошибка обработчика: {e}")
        await message.answer("💥 Произошла ошибка при анализе изображения.")
        try:
            await status_msg.delete()
        except:
            pass


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
