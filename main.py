import asyncio
import logging
import io
import os
import sys
import re

# Базовые библиотеки
import qrcode
from PIL import Image, ImageDraw

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

try:
    import cv2
    import numpy as np
    import easyocr

    logging.info("✅ Успешный импорт OpenCVcontrib и EasyOCR")
except ModuleNotFoundError as e:
    logging.critical(f"💥 ОШИБКА: Не установлена библиотека! {e}. Проверьте requirements.txt")
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

# ИНИЦИАЛИЗАЦИЯ ДВИЖКОВ
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()
ocr_reader = easyocr.Reader(['en'], gpu=False)


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


def scan_tag_data(img: np.ndarray):
    """
    Одновременный поиск QR и CLG-кода на основе геометрии расположения.
    """
    detected_link = ""
    detected_clg = ""
    
    try:
        # 1. Сначала ищем QR-код и получаем его координаты
        res, points = wechat_detector.detectAndDecode(img)
        if res:
            detected_link = res[0]
            
            # 2. Если QR найден, берем область над ним для поиска CLG
            if points is not None and len(points) > 0:
                box = points[0]  # Координаты углов QR-кода [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                
                # Вычисляем габариты QR-кода
                x_min = int(min(box[:, 0]))
                x_max = int(max(box[:, 0]))
                y_min = int(min(box[:, 1]))
                
                qr_width = x_max - x_min
                qr_height = int(max(box[:, 1]) - y_min)
                
                # Определяем зону над QR кодом (обычно текст CLG находится в пределах 80% высоты QR-кода над ним)
                # Расширяем немного влево и вправо на 20% для надежности
                crop_y_min = max(0, y_min - int(qr_height * 0.9))
                crop_y_max = y_min - int(qr_height * 0.05) # чуть выше самого края QR
                crop_x_min = max(0, x_min - int(qr_width * 0.2))
                crop_x_max = min(img.shape[1], x_max + int(qr_width * 0.2))
                
                # Вырезаем зону с текстом CLG
                clg_zone = img[crop_y_min:crop_y_max, crop_x_min:crop_x_max]
                
                if clg_zone.size > 0:
                    # Сканируем только эту точечную область
                    ocr_results = ocr_reader.readtext(clg_zone, detail=0)
                    full_text = " ".join(ocr_results)
                    logging.info(f"Считанный текст из зоны над QR: {full_text}")
                    
                    match = re.search(r'(?:\d[\s-]*){12}', full_text)
                    if match:
                        clean_code = re.sub(r'\D', '', match.group(0))
                        detected_clg = f"{clean_code[:3]} {clean_code[3:6]} {clean_code[6:9]} {clean_code[9:]}"

        # 3. Резервный вариант: Если над QR кодом не распозналось или QR не считался, 
        # сканируем картинку целиком
        if not detected_clg:
            ocr_results = ocr_reader.readtext(img, detail=0)
            full_text = " ".join(ocr_results)
            match = re.search(r'(?:\d[\s-]*){12}', full_text)
            if match:
                clean_code = re.sub(r'\D', '', match.group(0))
                detected_clg = f"{clean_code[:3]} {clean_code[3:6]} {clean_code[6:9]} {clean_code[9:]}"
                
    except Exception as e:
        logging.error(f"Ошибка при комплексном сканировании: {e}")
        
    return detected_link, detected_clg


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот готов.\n\n"
        "Отправь фото бирки — я распознаю QR-код и вытащу 12-значный CLG код."
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Принял фото. Фокусирую сканер на кодах...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            await status_msg.edit_text("❌ Не удалось обработать изображение.")
            return

        loop = asyncio.get_running_loop()
        
        # Вызываем оптимизированную функцию сканирования
        detected_link, detected_clg = await loop.run_in_executor(None, scan_tag_data, img)

        # Вывод результатов (Без обратных кавычек вокруг ссылок)
        if detected_link:
            clg_text = detected_clg if detected_clg else "Не удалось распознать"
            
            await status_msg.edit_text(
                f"🔢 **CLG-код:** {clg_text}\n"
                f"🔗 **QR-код:** {detected_link}\n\n"
                f"⏳ Создаю кастомный QR..."
            )
            
            # Генерация нового QR на макете
            result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
            document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
            
            caption_text = (
                f"✅ Готово!\n\n"
                f"🔢 CLG-код: {clg_text}\n"
                f"🔗 Ссылка: {detected_link}"
            )
            await message.reply_document(document=document, caption=caption_text)
            await status_msg.delete()
            
        else:
            if detected_clg:
                await status_msg.edit_text(
                    f"✅ QR-код не считался, но текстовый код найден!\n\n"
                    f"🔢 **CLG-код:** {detected_clg}"
                )
            else:
                await status_msg.edit_text(
                    "❌ Не удалось найти данные.\n\n"
                    "Попробуйте сделать фото под более прямым углом и без сильных бликов на цифрах."
                )

    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("💥 Произошла ошибка при обработке изображения на сервере.")
        if 'status_msg' in locals():
            try:
                await status_msg.delete()
            except:
                pass


@dp.message()
async def handle_other(message: Message):
    await message.answer("Пожалуйста, отправь мне именно **фотографию бирки**.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот со снайперским OCR запущен!")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
