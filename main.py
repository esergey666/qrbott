import asyncio
import logging
import io
import os
import numpy as np
import qrcode
from PIL import Image, ImageDraw
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import CommandStart

# Токен подтягивается из настроек хостинга automatically
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    logging.error("Критическая ошибка: Переменная окружения BOT_TOKEN не найдена!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Имя файла макета, который лежит в одной папке с ботом
TEMPLATE_PATH = "maket.png"


# Алгоритм генерации QR-кода без пробелов на макете
def generate_qr_on_template(data: str, output_size=1200) -> io.BytesIO:
    # Открываем локальный файл макета по умолчанию
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Файл {TEMPLATE_PATH} не найден в папке с ботом!")

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


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я готов к работе.\n\n"
        "Отправь мне любую **ссылку** или текст, и я сразу сделаю фирменный QR-код!"
    )


@dp.message(F.text)
async def handle_link(message: Message):
    link_data = message.text
    status_msg = await message.answer("⏳ Генерирую QR-код, подождите...")

    try:
        # Запуск тяжелой обработки в фоновом потоке
        loop = asyncio.get_running_loop()
        result_buffer = await loop.run_in_executor(
            None, generate_qr_on_template, link_data
        )

        document = BufferedInputFile(result_buffer.read(), filename="CUSTOM_QR.png")
        await message.reply_document(
            document=document,
            caption=f"✅ Готово!\nСсылка: `{link_data}`"
        )
        await status_msg.delete()

    except FileNotFoundError:
        await message.answer("❌ Ошибка на сервере: файл макета `maket.png` не найден.")
        await status_msg.delete()
    except Exception as e:
        logging.error(f"Ошибка генерации: {e}")
        await message.answer("💥 Произошла ошибка при генерации QR-кода.")
        await status_msg.delete()


async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())