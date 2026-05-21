import asyncio
import logging
import io
import os
import sys
import re
from io import BytesIO
import aiohttp  # Легкие асинхронные запросы вместо тяжелого OCR

# Базовые библиотеки
import qrcode
from PIL import Image, ImageDraw

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

try:
    import cv2
    import numpy as np
    logging.info("✅ Успешный импорт OpenCVcontrib")
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

# ИНИЦИАЛИЗАЦИЯ ДВИЖКА QR (потребляет мало памяти)
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()


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
    try:
        res, points = wechat_detector.detectAndDecode(img)
        return res[0] if res else ""
    except Exception as e:
        logging.error(f"Ошибка WeChat: {e}")
        return ""


async def get_clg_from_url(url: str) -> str:
    """
    Ультралегкий веб-запрос. 
    Получает CLG-код напрямую с сайта Certilogo по ID из ссылки без OCR.
    """
    try:
        # Извлекаем ID (например, 06GD7NKF17) из ссылки
        match = re.search(r'/qr/([A-Za-z0-9]+)', url)
        if not match:
            return ""
        
        qr_id = match.group(1)
        # Официальный адрес API верификации Certilogo
        api_url = f"https://api.certilogo.com/v1/code/{qr_id}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    # Извлекаем чистый 12-значный код из ответа компании
                    clg_raw = data.get("code", "")
                    if clg_raw and len(clg_raw) == 12:
                        return f"{clg_raw[:3]} {clg_raw[3:6]} {clg_raw[6:9]} {clg_raw[9:]}"
    except Exception as e:
        logging.error(f"Не удалось получить CLG по сети: {e}")
    return ""


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот оптимизирован для ультра-быстрой работы на слабом сервере.\n\n"
        "Отправь мне фото бирки, я мгновенно считаю QR и выдам CLG-код!"
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Обрабатываю фото...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        nparr = np.frombuffer(photo_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            await status_msg.edit_text("❌ Ошибка чтения изображения.")
            return

        loop = asyncio.get_running_loop()
        detected_link = await loop.run_in_executor(None, decode_qr_from_photo, img)

        if detected_link:
            # Вместо тяжелого OCR делаем легкий запрос во внешней функции
            detected_clg = await get_clg_from_url(detected_link)
            
            clg_text = detected_clg if detected_clg else "Не удалось запросить у Certilogo"
            
            await status_msg.edit_text(
                f"🔢 **CLG-код:** {clg_text}\n"
                f"🔗 **QR-код:** {detected_link}\n\n"
                f"⏳ Создаю макет..."
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
            await status_msg.edit_text(
                "❌ Не удалось считать QR-код.\n"
                "Пожалуйста, сделайте фото ровнее, ближе и без бликов."
            )

    except Exception as e:
        logging.error(f"Ошибка обработки: {e}")
        await message.answer("💥 Произошла ошибка при обработке.")
        if 'status_msg' in locals():
            try:
                await status_msg.delete()
            except:
                pass


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Легкий бот запущен!")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
