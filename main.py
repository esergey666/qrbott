import asyncio
import logging
import io
import os

# Настройка базового логирования, чтобы видеть всё происходящее на хостинге
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Проверка критически важных библиотек перед запуском
try:
    import numpy as np
    import qrcode
    from PIL import Image, ImageDraw
    logging.info("🎉 Все необходимые библиотеки успешно импортированы!")
except ModuleNotFoundError as e:
    logging.critical(
        f"❌ ОШИБКА ИМПОРТА: {e}\n"
        f"Хостинг не установил библиотеки из файла requirements.txt.\n"
        f"Проверьте, что файл называется именно 'requirements.txt' (маленькими буквами) "
        f"и лежит в корне репозитория GitHub рядом с main.py!"
    )
    # Не даем боту упасть молча, выводим ошибку в топ логов
    raise e

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import CommandStart

# Токен автоматически подтягивается из панели управления Bothost (Переменные окружения)
TOKEN = os.getenv("API_TOKEN")

if not TOKEN:
    logging.error("⚠️ ВНИМАНИЕ: Переменная окружения BOT_TOKEN не найдена! Проверьте настройки хостинга.")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Имя файла макета, который ты загрузил на GitHub (в формате JPG)
TEMPLATE_PATH = "maket.jpg"

# Алгоритм генерации QR-кода без пробелов на JPG макете
def generate_qr_on_template(data: str, output_size=1200) -> io.BytesIO:
    # Проверяем, на месте ли картинка
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Файл макета '{TEMPLATE_PATH}' не найден на сервере!")
        
    # Открываем JPG и конвертируем в RGBA для работы со слоями
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
                left = x * module
