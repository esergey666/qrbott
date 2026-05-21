import asyncio
import logging
import io
import os
import sys

import qrcode
from PIL import Image, ImageDraw
import zxingcpp  # Сверхстабильный декодер от Google

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

if not TOKEN:
    logging.critical("❌ Токен не найден в переменных окружения!")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"

# Твой алгоритм генерации QR-кода на макете (ТЕПЕРЬ БЕЗ NUMPY)
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
    
    # Получаем матрицу QR-кода в виде обычного списка списков Python (вместо numpy)
    qr_matrix = qr.get_matrix()
    matrix_size = len(qr_matrix)  # Для version=3 это будет 29
    
    module_size = template.width // matrix_size
    qr_layer = Image.new('RGBA', (matrix_size * module_size, matrix_size * module_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(qr_layer)
    
    for y in range(matrix_size):
        for x in range(matrix_size):
            if qr_matrix[y][x]:  # Если модуль черный (True)
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

# Декодирование через движок Google ZXing
def decode_qr_local(photo_bytes: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(photo_bytes))
        results = zxingcpp.read_barcodes(img)
        if results:
            return results[0].text
        return ""
    except Exception as e:
        logging.error(f"Ошибка декодирования zxing: {e}")
        return ""

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Бот обновлен и полностью защищен от зависаний!\n\n"
        "Отправь мне **фотографию бирки** с QR-кодом, и я сделаю кастомный QR без пробелов."
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Сканирую фото бирки...")
    
    try:
        photo = message.photo[-1]
        file_in_io = io.BytesIO()
        await bot.download(photo, destination=file_in_io)
        photo_bytes = file_in_io.getvalue()
        
        # Обработка в изолированном потоке для многопоточности
        loop = asyncio.get_running_loop()
        detected_link = await loop.run_in_executor(None, decode_qr_local, photo_bytes)
        
        if not detected_link:
            await status_msg.edit_text(
                "❌ Не удалось считать QR-код на фото.\n\n"
                "Попробуй сделать фото ближе или пришли ссылку обычным текстом!"
            )
            return
            
        await status_msg.edit_text(f"🔍 Код успешно считан!\nСсылка: `{detected_link}`\n\n⏳ Создаю кастомный QR...")
        
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка: `{detected_link}`"
        )
        await status_msg.delete()
        
    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("💥 Произошла ошибка при обработке картинки.")
        await status_msg.delete()

@dp.message(F.text)
async def handle_text_link(message: Message):
    link_data = message.text
    if link_data.startswith("/"):
        return
        
    status_msg = await message.answer("⏳ Генерирую QR по текстовой ссылке...")
    try:
        loop = asyncio.get_running_loop()
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, link_data)
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(document=document, caption=f"✅ Готово!\nСсылка: `{link_data}`")
        await status_msg.delete()
    except Exception as e:
        await message.answer("💥 Ошибка при генерации.")
        await status_msg.delete()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот без numpy успешно запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
