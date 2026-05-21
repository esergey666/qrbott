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

# Перехватываем ошибки импорта OpenCV и Tesseract
try:
    import cv2
    import numpy as np
    import pytesseract  # Библиотека для распознавания текста

    logging.info("✅ Успешный импорт OpenCV и PyTesseract")
except ModuleNotFoundError as e:
    logging.critical(f"💥 ОШИБКА: Не установлена библиотека! {e}")
    sys.exit(1)

# ЕСЛИ ВЫ НА WINDOWS: раскомментируйте строку ниже и укажите путь к tesseract.exe
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

if not TOKEN:
    logging.critical("❌ Токен не найден в переменных окружения!")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"


# --- ИИ ДЕКОДЕРЫ ---
# 1. Движок WeChat для QR-кодов
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()

# 2. Функция распознавания 12-значного CLG текста через Tesseract OCR
def extract_clg_text_from_image(photo_bytes: bytes) -> str:
    try:
        # Открываем изображение через PIL и переводим в оттенки серого для лучшего распознавания
        img = Image.open(io.BytesIO(photo_bytes)).convert('L')
        
        # Увеличиваем контрастность, чтобы текст был четче
        img = img.point(lambda x: 0 if x < 128 else 255, '1')

        # Запускаем поиск только цифр (оптимизируем под конфигурацию Tesseract)
        custom_config = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789'
        text = pytesseract.image_to_string(img, config=custom_config)
        
        # Удаляем все пробелы и переносы, ищем 12 цифр подряд
        cleaned_text = re.sub(r'\s+', '', text)
        match = re.search(r'\d{12}', cleaned_text)
        
        if match:
            code = match.group(0)
            # Форматируем в красивый вид: 012 345 678 901
            return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"
        
        return "Не удалось считать цифры (CLG)"
    except Exception as e:
        logging.error(f"Ошибка при работе Tesseract OCR: {e}")
        return "Ошибка распознавания текста"


# Твой рабочий алгоритм генерации QR-кода на макете
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


def decode_qr_from_photo(photo_bytes: bytes) -> str:
    try:
        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return ""
        res, points = wechat_detector.detectAndDecode(img)
        return res[0] if res else ""
    except Exception as e:
        logging.error(f"Ошибка внутри ИИ-декодера WeChat: {e}")
        return ""


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот обновлен.\n\n"
        "Теперь я не только создаю кастомный QR-код по вашей технологии, "
        "но и параллельно считываю 12-значный CLG код прямо с текста на фотографии бирки!"
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Фото получено. Сканирую QR-код и распознаю цифры...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        loop = asyncio.get_running_loop()
        
        # 1. Параллельно запускаем считывание QR-кода и распознавание напечатанного CLG-текста
        detected_link = await loop.run_in_executor(None, decode_qr_from_photo, photo_bytes)
        clg_text_code = await loop.run_in_executor(None, extract_clg_text_from_image, photo_bytes)

        if not detected_link:
            await status_msg.edit_text(
                f"❌ QR-код не найден.\n\n"
                f"🔢 **Считанные цифры с бирки:** `{clg_text_code}`\n\n"
                f"Попробуйте сделать фото более четким и под прямым углом."
            )
            return

        # 2. Если QR найден, генерируем новый кастомный макет
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        
        # Отправляем готовый кастомный QR и распознанные цифры в красивом формате
        await message.reply_document(
            document=document,
            caption=f"✅ **Все данные успешно считаны!**\n\n"
                    f"🔢 **Код с бирки:** `{clg_text_code}`\n"
                    f"🔗 **Ссылка из QR:** `{detected_link}`"
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
    logging.info("🚀 Бот (QR-генератор + Text OCR) запущен!")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
