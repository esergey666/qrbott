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
import aiohttp  # Для быстрых запросов к Certilogo

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Перехватываем стандартный OpenCV (обычный opencv-python, без contrib)
try:
    import cv2
    import numpy as np
    logging.info("✅ Успешный импорт OpenCV")
except ModuleNotFoundError:
    logging.critical("💥 ОШИБКА: Библиотека 'opencv-python' не установлена!")
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

# Инициализируем стандартный детектор QR (WeChat нам больше не нужен, обычный тоже справится, 
# либо оставь свой старый движок wechat_detector, если он тебе больше нравился)
qr_detector = cv2.QRCodeDetector()


# --- НАДЁЖНЫЙ КАНАЛ ПОЛУЧЕНИЯ CLG КОДА ЧЕРЕЗ API ---
async def get_clg_from_certilogo(url: str) -> str:
    """
    Делает запрос к Certilogo и забирает оригинальный 12-значный код, 
    который сервер выдает при редиректе.
    """
    # Если в ссылке уже есть 12 цифр подряд, просто забираем их
    match = re.search(r'\d{12}', url)
    if match:
        code = match.group(0)
        return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"
        
    # Если ссылка короткая (буквенная), спрашиваем у официального сервера
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # Отправляем запрос и смотрим, куда нас перенаправит сайт
            async with session.get(url, allow_redirects=True, timeout=5) as response:
                final_url = str(response.url)
                
                # Ищем 12 цифр в итоговой ссылке после редиректа
                clg_match = re.search(r'\d{12}', final_url)
                if clg_match:
                    code = clg_match.group(0)
                    return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"
                
                # Попробуем прочесть текст страницы, если код скрыт в куках/сессии
                html_text = await response.text()
                code_in_html = re.search(r'CLG\s*(\d{3})\s*(\d{3})\s*(\d{3})\s*(\d{3})', html_text, re.IGNORECASE)
                if code_in_html:
                    return f"{code_in_html.group(1)} {code_in_html.group(2)} {code_in_html.group(3)} {code_in_html.group(4)}"
                
                # Альтернативный поиск 12 цифр в теле ответа
                all_numbers = re.findall(r'\b\d{12}\b', html_text)
                if all_numbers:
                    code = all_numbers[0]
                    return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"

    except Exception as e:
        logging.error(f"Ошибка при запросе к Certilogo API: {e}")
    
    return "Не удалось извлечь код (запросите вручную)"


# Твой рабочий алгоритм генерации QR-кода на макете (без изменений)
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
        
        # Используем стандартный или твой старый WeChat декодер
        # Если хочешь вернуть старый WeChat, замени две строки ниже на свой старый код WeChat
        res, _, _ = qr_detector.detectAndDecode(img)
        return res if res else ""
    except Exception as e:
        logging.error(f"Ошибка декодера QR: {e}")
        return ""


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот полностью перенастроен.\n\n"
        "Теперь я считываю QR-код, автоматически запрашиваю официальную базу данных Certilogo "
        "и присылаю тебе точный 12-значный CLG-код бирки вместе с кастомным QR!"
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Фото получено. Считываю QR и запрашиваю код CLG...")

    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()

        loop = asyncio.get_running_loop()
        
        # 1. Сканируем QR-код
        detected_link = await loop.run_in_executor(None, decode_qr_from_photo, photo_bytes)

        if not detected_link:
            await status_msg.edit_text("❌ QR-код не найден на фото. Попробуйте сделать снимок четче.")
            return

        # 2. Магия: отправляем ссылку серверу Certilogo, чтобы он отдал нам 12 цифр
        clg_code = await get_clg_from_certilogo(detected_link)

        # 3. Генерируем кастомный макет
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        
        # Отправляем результат
        await message.reply_document(
            document=document,
            caption=f"✅ **Данные успешно получены!**\n\n"
                    f"🔢 **Код CLG:** `{clg_code}`\n"
                    f"🔗 **Ссылка из QR:** `{detected_link}`"
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("💥 Произошла ошибка при обработке изображения.")
        await status_msg.delete()


@dp.message()
async def handle_other(message: Message):
    await message.answer("Пожалуйста, отправь мне именно **фотографию бирки**.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 API-бот Certilogo запущен!")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
