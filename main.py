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
import aiohttp  # Используем для стабильного получения CLG через интернет

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

try:
    import cv2
    import numpy as np
    logging.info("✅ Успешный импорт OpenCV")
except ModuleNotFoundError:
    logging.critical("💥 ОШИБКА: Библиотека 'opencv-contrib-python-headless' не установлена!")
    sys.exit(1)

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

TOKEN = os.getenv("API_TOKEN") or os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

TEMPLATE_PATH = "maket.jpg"

# Твой любимый ИИ-декодер WeChat для QR-кодов
wechat_detector = cv2.wechat_qrcode_WeChatQRCode()


async def fetch_clg_from_web(url: str) -> str:
    """
    Имитирует реальный браузер, переходит по ссылке QR-кода 
    и вытаскивает настоящий 12-значный CLG код из базы Certilogo.
    """
    # Если в ссылке чудом уже есть 12 цифр подряд, просто форматируем их
    match = re.search(r'\d{12}', url)
    if match:
        code = match.group(0)
        return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"

    # Маскируемся под полноценный iPhone, чтобы Certilogo не заблокировал запрос
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0"
    }
    
    try:
        # Инициализируем сессию с поддержкой кук
        jar = aiohttp.CookieJar(ignore_cookies=True)
        async with aiohttp.ClientSession(headers=headers, cookie_jar=jar) as session:
            # Делаем запрос, идя по всем редиректам сайта
            async with session.get(url, allow_redirects=True, timeout=8) as response:
                final_url = str(response.url)
                
                # Проверяем, появился ли CLG в финальной ссылке после перенаправления
                clg_match = re.search(r'\d{12}', final_url)
                if clg_match:
                    code = clg_match.group(0)
                    return f"{code[:3]} {code[3:6]} {code[6:9]} {code[9:]}"
                
                # Если в ссылке нет, ищем внутри текста самой страницы (HTML кода)
                html_text = await response.text()
                
                # Ищем комбинации типа CLG 307 667... или просто 12 цифр
                html_match = re.search(r'CLG\s*(\d{3})\s*(\d{3})\s*(\d{3})\s*(\d{3})', html_text, re.IGNORECASE)
                if html_match:
                    return f"{html_match.group(1)} {html_match.group(2)} {html_match.group(3)} {html_match.group(4)}"
                
                # Резервный поиск любых 12 цифр на странице в тегах
                all_codes = re.findall(r'\b\d{12}\b', html_text)
                if all_codes:
                    code = all_codes[0]
                    return f"{
