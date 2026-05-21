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
    import pytesseract  # Снова используем его, но теперь точечно!
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

# Включаем WeChat QR декодер (он даёт координаты углов)
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()


def crop_and_ocr_clg(img, points) -> str:
    """
    Берет координаты QR-кода, вычисляет зону НАД ним 
    и отправляет в Tesseract только чистые цифры.
    """
    try:
        h, w, _ = img.shape
        
        # Получаем координаты углов QR-кода
        pts = points[0].astype(int) # [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
        
        # Находим верхнюю границу QR-кода
        top_y = min(pts[0][1], pts[1][1])
        left_x = min(pts[0][0], pts[3][0])
        right_x = max(pts[1][0], pts[2][0])
        
        qr_width = right_x - left_x
        
        # Вырезаем прямоугольник НАД QR-кодом (примерно на 40% от высоты QR-кода вверх)
        # Немного расширяем влево и вправо, чтобы захватить буквы "CLG" и все цифры
        crop_top = max(0, top_y - int(qr_width * 0.45))
        crop_bottom = top_y - int(qr_width * 0.05) # Чуть выше самого QR, чтобы не поймать его рамку
        crop_left = max(0, left_x - int(qr_width * 0.1))
        crop_right = min(w, right_x + int(qr_width * 0.1))
        
        # Сама обрезка
        roi = img[crop_top:crop_bottom, crop_left:crop_right]
        
        # Предварительная обработка вырезанного куска для улучшения читаемости
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Увеличиваем картинку в 2 раза, чтобы мелкий шрифт стал крупным
        resized = cv2.resize(gray, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        # Делаем текст жестким черным, а фон белым
        _, thresh = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        
        # Настройки Tesseract: ищем строго строчку цифр
        custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789'
        text = pytesseract.image_to_string(thresh, config=custom_config)
        
        cleaned = re.sub(r'\s+', '', text)
        match = re.search(r'\d{12}', cleaned)
        
        if match:
            code = match.group(0)
            return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"
            
        # Если 12 цифр не нашлось, попробуем взять вообще любые цифры (вдруг смазалась одна цифра)
        just_digits = ''.join(filter(str.isdigit, cleaned))
        if len(just_digits) >= 10:
            return f"Приблизительно: {just_digits}"
            
        return "Не удалось считать текст над QR"
        
    except Exception as e:
        logging.error(f"Ошибка при кропе/OCR: {e}")
        return "Ошибка зоны распознавания"


# Твой алгоритм генерации QR (без изменений)
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

        # Декодируем картинку в массив OpenCV
        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            await status_msg.edit_text("❌ Ошибка чтения файла изображения.")
            return

        # Находим QR-код и получаем его точки (points)
        res, points = wechat_detector.detectAndDecode(img)

        if not res or points is None or len(points) == 0:
            await status_msg.edit_text("❌ QR-код не обнаружен. Сделайте фото ровнее.")
            return

        detected_link = res[0]

        # Запускаем чтение цифр СТРОГО в зоне над найденным кодом
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
        logging.error(f"Ошибка: {e}")
        await message.answer("💥 Произошла ошибка при анализе изображения.")
        await status_msg.delete()


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
