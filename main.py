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
    Извлекает точные координаты QR-кода, вырезает область НАД ним,
    обрабатывает её и отправляет в Tesseract для точного чтения CLG.
    """
    try:
        h, w, _ = img.shape
        
        # Распаковываем массив точек WeChat (он возвращает список массивов)
        if isinstance(points, list):
            pts = np.array(points[0]).reshape(4, 2).astype(int)
        else:
            pts = np.array(points).reshape(4, 2).astype(int)
        
        # pts содержит 4 точки: [левый_верх, правый_верх, правый_ниж, левый_ниж]
        top_left = pts[0]
        top_right = pts[1]
        
        # Вычисляем ширину QR-кода, чтобы понять масштаб
        qr_width = int(np.linalg.norm(top_right - top_left))
        if qr_width == 0:
            qr_width = 200  # Дефолтное значение, если что-то пошло не так
            
        # Находим базовую линию верха QR-кода
        top_y = min(top_left[1], top_right[1])
        center_x = int((top_left[0] + top_right[0]) / 2)
        
        # Формируем строгую рамку НАД кодом
        # Поднимаемся вверх на 45% от ширины QR, и берем высоту в 40% от ширины QR
        crop_top = max(0, top_y - int(qr_width * 0.50))
        crop_bottom = max(0, top_y - int(qr_width * 0.05)) # Чуть выше рамки QR
        crop_left = max(0, center_x - int(qr_width * 0.65)) # Берем с запасом влево
        crop_right = min(w, center_x + int(qr_width * 0.65)) # Берем с запасом вправо
        
        # Проверяем корректность координат обрезки
        if crop_bottom <= crop_top or crop_right <= crop_left:
            return "Некорректная зона обрезки текста"
            
        # Вырезаем область с текстом CLG
        roi = img[crop_top:crop_bottom, crop_left:crop_right]
        
        # --- ПРЕДОБРАБОТКА ИЗОБРАЖЕНИЯ ДЛЯ TESSERACT ---
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        
        # Увеличиваем картинку в 3 раза (Tesseract гораздо лучше распознает крупный текст)
        resized = cv2.resize(gray, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        
        # Размытие для удаления шума текстуры ткани/ниток
        blurred = cv2.GaussianBlur(resized, (5, 5), 0)
        
        # Переводим в жесткий ЧБ (адаптивная бинаризация подстраивается под освещение)
        thresh = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY, 11, 2
        )
        
        # Настройки Tesseract: psm 7 означает, что мы ищем одну строку текста
        custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789'
        text = pytesseract.image_to_string(thresh, config=custom_config)
        
        # Чистим строку от мусора
        cleaned = re.sub(r'\s+', '', text)
        
        # Ищем строго 12 цифр подряд
        match = re.search(r'\d{12}', cleaned)
        if match:
            code = match.group(0)
            return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"
            
        # Резервный поиск: если 12 цифр подряд не склеились, забираем просто все цифры из строки
        digits_only = ''.join(filter(str.isdigit, cleaned))
        if len(digits_only) == 12:
            return f"{digits_only[:3]} {digits_only[3:6]} {digits_only[6:9]} {digits_only[9:]}"
        elif len(digits_only) > 0:
            return f"Частично: {digits_only}"
            
        return "Цифры не найдены в зоне над QR"
        
    except Exception as e:
        logging.error(f"Ошибка в блоке кропа/OCR: {e}")
        return "Ошибка обработки текста"


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
    status_msg = await message.answer("📥 Считываю QR-код и распознаю CLG...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            await status_msg.edit_text("❌ Ошибка обработки картинки.")
            return

        # Находим QR-код и извлекаем точки через ИИ WeChat
        res, points = wechat_detector.detectAndDecode(img)

        if not res or points is None or len(points) == 0:
            await status_msg.edit_text("❌ QR-код на фото не обнаружен.")
            return

        detected_link = res[0]

        # Запускаем исправленный OCR
        loop = asyncio.get_running_loop()
        clg_code = await loop.run_in_executor(None, crop_and_ocr_clg, img, points)

        # Создаем кастомный макет
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        
        await message.reply_document(
            document=document,
            caption=f"✅ **Успешно обработано!**\n\n"
                    f"🔢 **Код CLG:** `{clg_code}`\n"
                    f"🔗 **Ссылка:** `{detected_link}`"
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Глобальная ошибка: {e}")
        await message.answer("💥 Внутренняя ошибка сервера при анализе фото.")
        await status_msg.delete()


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
