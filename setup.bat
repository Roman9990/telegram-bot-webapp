@echo off
echo 🚀 Настройка Telegram бота с WebApp интеграцией
echo ================================================

REM Проверка Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не установлен. Установите Python 3.7+ и повторите.
    pause
    exit /b 1
)

echo ✓ Python найден
python --version

REM Создание виртуального окружения
echo 📦 Создание виртуального окружения...
python -m venv venv

REM Активация виртуального окружения
echo ⚡ Активация виртуального окружения...
call venv\Scripts\activate.bat

REM Установка зависимостей
echo 📚 Установка зависимостей...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo ✅ Установка завершена!
echo.
echo 📝 Следующие шаги:
echo 1. Замените 'ВАШ_ТОКЕН_БОТА_ЗДЕСЬ' в bot.py на реальный токен
echo 2. Настройте GitHub Pages для WebApp файлов
echo 3. Обновите URL в bot.py на ваши GitHub Pages URL
echo 4. Настройте GROUP_ID, LOG_GROUP_ID и OWNER_ID
echo 5. Запустите бота командой: python bot.py
echo.
echo 🔗 Подробная инструкция в README.md
echo.
pause