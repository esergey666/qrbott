import asyncio
import logging
import io
import os
import sys
import re  # Добавили для поиска регулярными выражениями
from io import BytesIO

# Базовые библиотеки
import qrcode
from PIL import Image, ImageDraw

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Перехватываем ошибки импорта OpenCV и EasyOCR
try:
    import cv2
    import numpy as np
    import easyocr  # Добавили OCR библиотеку

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

# ИНИЦИАЛИЗАЦИЯ ДВИЖКОВ (делается один раз при запуске)
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()
# Инициализируем распознавание английского текста и цифр (работает быстрее, чем мульти can)
ocr_reader = easyocr.Reader(['en'], gpu=False)  # Если на сервере есть GPU, поставь True


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


def decode_qr_from_photo(img: np.ndarray) -> str:
    """Сканирование QR-кода"""
    try:
        res, points = wechat_detector.detectAndDecode(img)
        return res[0] if res else ""
    except Exception as e:
        logging.error(f"Ошибка внутри ИИ-декодера WeChat: {e}")
        return ""


def extract_clg_code(img: np.ndarray) -> str:
    """Сканирование текста и поиск 12-значного CLG кода"""
    try:
        # Извлекаем весь текст с картинки
        results = ocr_reader.readtext(img, detail=0)
        full_text = " ".join(results)
        
        # Регулярное выражение ищет ровно 12 цифр, между которыми могут быть пробелы или дефисы
        # Примеры: "307 667 487 937", "CLG307667487937", "307-667-487-937"
        match = re.search(r'(?:\d[\s-]*){12}', full_text)
        
        if match:
            # Очищаем найденный код от пробелов и тире, оставляя только 12 цифр
            clean_code = re.sub(r'\D', '', match.group(0))
            # Форматируем для красоты по 3 цифры: "307 667 487 937"
            formatted_code = f"{clean_code[:3]} {clean_code[3:6]} {clean_code[6:9]} {clean_code[9:]}"
            return formatted_code
        return ""
    except Exception as e:
        logging.error(f"Ошибка внутри ИИ-распознавания текста: {e}")
        return ""


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот обновлен.\n\n"
        "Я умею считывать **QR-коды** и находить **12-значные CLG-коды** прямо на фотографии бирки.\n"
        "Просто отправь мне фото!"
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Принял фото. Запускаю сканирование кода и текста...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        # Декодируем байты в OpenCV формат один раз для обеих функций
        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            await status_msg.edit_text("❌ Не удалось обработать изображение.")
            return

        loop = asyncio.get_running_loop()
        
        # Запускаем распознавание QR и текста параллельно в фоновом потоке
        detected_link = await loop.run_in_executor(None, decode_qr_from_photo, img)
        detected_clg = await loop.run_in_executor(None, extract_clg_code, img)

        # Формируем текст ответа
        response_text = ""
        if detected_clg:
            response_text += f"🔢 **Найден CLG-код:** `{detected_clg}`\n"
        else:
            response_text += "🔢 **CLG-код:** Не удалось распознать текст.\n"

        if detected_link:
            response_text += f"🔍 **QR-код:** Ссылка распознана!\n\n⏳ Создаю кастомный QR..."
            await status_msg.edit_text(response_text)
            
            # Генерация нового QR на основе ссылки
            result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
            document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
            
            caption_text = f"✅ Готово!\n🔢 CLG-код: `{detected_clg if detected_clg else 'не найден'}`\n🔗 Ссылка: `{detected_link}`"
            await message.reply_document(document=document, caption=caption_text)
            await status_msg.delete()
        else:
            response_text += "\n❌ **QR-код:** Не найден на фото."
            # Если QR не найден, но CLG код есть — просто отдаем его текстом
            if detected_clg:
                await status_msg.edit_text(f"✅ Нашел текстовый код!\n\n🔢 **CLG-код:** `{detected_clg}`\n\n(QR-код считать не удалось)")
            else:
                await status_msg.edit_text("❌ Не удалось найти ни QR-код, ни 12-значный текстовый код.")

    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("💥 Произошла ошибка при обработке изображения на сервере.")
        if 'status_msg' in locals():
            await status_msg.delete()


@dp.message()
async def handle_other(message: Message):
    await message.answer("Пожалуйста, отправь мне именно **фотографию бирки**.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот со сканером QR и текста запущен!")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
