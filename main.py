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

# ИНИЦИАЛИЗАЦИЯ ДВИЖКОВ (один раз при старте)
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
    Безопасный поиск QR и CLG-кода с защитой от вылетов геометрии.
    """
    detected_link = ""
    detected_clg = ""
    
    # 1. Распознаем QR-код
    try:
        res, points = wechat_detector.detectAndDecode(img)
        if res:
            detected_link = res[0]
            logging.info(f"QR код успешно найден движком WeChat: {detected_link}")
    except Exception as e:
        logging.error(f"Ошибка при работе WeChatQRCode: {e}")
        points = None

    # 2. Пробуем снайперски вырезать зону над QR-кодом для поиска CLG
    if detected_link and points is not None and len(points) > 0:
        try:
            box = points[0]
            img_h, img_w = img.shape[:2]

            x_min = int(max(0, min(box[:, 0])))
            x_max = int(min(img_w, max(box[:, 0])))
            y_min = int(max(0, min(box[:, 1])))
            
            qr_width = x_max - x_min
            qr_height = int(max(box[:, 1]) - y_min)
            
            # Строгие безопасные границы вырезки
            crop_y_min = max(0, y_min - int(qr_height * 0.9))
            crop_y_max = max(0, y_min - int(qr_height * 0.05))
            crop_x_min = max(0, x_min - int(qr_width * 0.2))
            crop_x_max = min(img_w, x_max + int(qr_width * 0.2))
            
            if crop_y_max > crop_y_min and crop_x_max > crop_x_min:
                clg_zone = img[crop_y_min:crop_y_max, crop_x_min:crop_x_max]
                
                if clg_zone.size > 0:
                    ocr_results = ocr_reader.readtext(clg_zone, detail=0)
                    full_text = " ".join(ocr_results)
                    logging.info(f"Текст из зоны над QR: {full_text}")
                    
                    match = re.search(r'(?:\d[\s-]*){12}', full_text)
                    if match:
                        clean_code = re.sub(r'\D', '', match.group(0))
                        detected_clg = f"{clean_code[:3]} {clean_code[3:6]} {clean_code[6:9]} {clean_code[9:]}"
        except Exception as e:
            logging.error(f"Не удалось сделать точечную вырезку над QR (пропускаем): {e}")

    # 3. Резервный сценарий: Если код все еще не найден — сканируем ВСЁ фото целиком
    if not detected_clg:
        try:
            logging.info("Точечный поиск не дал результатов. Запуск OCR по всей площади...")
            ocr_results = ocr_reader.readtext(img, detail=0)
            full_text = " ".join(ocr_results)
            
            match = re.search(r'(?:\d[\s-]*){12}', full_text)
            if match:
                clean_code = re.sub(r'\D', '', match.group(0))
                detected_clg = f"{clean_code[:3]} {clean_code[3:6]} {clean_code[6:9]} {clean_code[9:]}"
        except Exception as e:
            logging.error(f"Ошибка при общем OCR сканировании кадра: {e}")
                
    return detected_link, detected_clg


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот исправлен и работает стабильно.\n\n"
        "Отправь мне фото бирки, я считаю данные."
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Принял фото. Сканирую коды...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            await status_msg.edit_text("❌ Ошибка чтения файла. Попробуйте загрузить фото еще раз.")
            return

        loop = asyncio.get_running_loop()
        
        # Безопасный вызов сканера
        detected_link, detected_clg = await loop.run_in_executor(None, scan_tag_data, img)

        # Обработка результатов
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
                    f"✅ Текстовый код успешно считан!\n\n"
                    f"🔢 **CLG-код:** {detected_clg}\n\n"
                    f"⚠️ Ссылку из QR-кода распознать не удалось."
                )
            else:
                await status_msg.edit_text(
                    "❌ Не удалось считать данные.\n\n"
                    "Убедитесь, что цифры и QR-код находятся в фокусе, не размыты и на них нет ярких бликов."
                )

    except Exception as e:
        logging.error(f"Критическая ошибка handle_photo: {e}")
        await message.answer("💥 Произошла системная ошибка при обработке.")
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
    logging.info("🚀 Бот перезапущен в безопасном режиме!")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
