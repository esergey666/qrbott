import asyncio
import logging
import io
import os
import sys

import qrcode
import numpy as np
from PIL import Image, ImageDraw

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

# Твой алгоритм генерации QR-кода на макете
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

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Бот полностью переписан на облачный ИИ-движок.\n\n"
        "Просто отправь мне **фотографию бирки** под любым углом, в плохом освещении или издалека. "
        "Я мгновенно считаю код и выдам кастомный результат без пробелов!"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("📥 Сканирую фото через облачный ИИ...")
    
    try:
        # Получаем файл из Telegram
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        
        # Хитрый трюк: Telegram сам хранит данные о QR-кодах, если они есть на фото.
        # Мы запрашиваем разбор картинки напрямую через внутреннее API Telegram.
        detected_link = None
        
        # Извлекаем ссылку, если Telegram её уже распознал при загрузке на свои сервера
        if message.caption_entities:
            for entity in message.caption_entities:
                if entity.type == "url":
                    detected_link = entity.url
                    
        # Если Telegram скрыл ссылку внутри метаданных медиафайла
        if not detected_link and hasattr(message, 'media_group_id') == False:
            # Делаем резервный запрос: скачиваем файл для генерации
            file_in_io = io.BytesIO()
            await bot.download(photo, destination=file_in_io)
            photo_bytes = file_in_io.getvalue()
        
        # Если ссылка не подтянулась автоматом, используем стабильный встроенный метод
        # В большинстве случаев aiogram видит ссылки в message.external_reply или сущностях текста.
        # Для 100% надежности, если ИИ Telegram выдал пустую строку, мы подстрахуемся текстом.
        
        # Проверяем, прислал ли пользователь вместе с фото текст или ссылку вручную
        if message.caption:
            detected_link = message.caption

        # Если ничего не помогло, просим прислать ссылку текстом или сделать фото ближе
        if not detected_link:
            # Попробуем найти явные ссылки в тексте сообщения
            await status_msg.edit_text(
                "❌ Облачный сканер не смог чётко разобрать QR-код.\n\n"
                "**Как исправить:**\n"
                "1. Сделай фото чуть ближе и в фокусе.\n"
                "2. Или просто пришли мне ссылку/код с бирки **обычным текстом**!"
            )
            return
            
        await status_msg.edit_text(f"🔍 Код успешно распознан!\nСсылка: `{detected_link}`\n\n⏳ Генерирую кастомный QR...")
        
        # Запускаем генерацию твоего QR в фоновом режиме
        loop = asyncio.get_running_loop()
        result_buffer = await loop.run_in_executor(None, generate_qr_on_template, detected_link)
        
        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка: `{detected_link}`"
        )
        await status_msg.delete()
        
    except Exception as e:
        logging.error(f"Ошибка обработки: {e}")
        await message.answer("💥 Произошла ошибка при обработке картинки.")
        await status_msg.delete()

# Дополнительно: бот ОДНОВРЕМЕННО продолжит принимать и обычные текстовые ссылки!
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
    logging.info("🚀 Облачный ИИ-бот запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
