"""
Упрощенная настройка логирования для бота.
"""

import os
import sys
from loguru import logger


def setup_logger():
    """Упрощенная настройка логгера."""
    
    # Удаляем стандартный обработчик
    logger.remove()
    
    # Создаем директорию для логов
    logs_dir = "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    # Простой формат логов
    log_format = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}"
    
    # Консольный вывод
    logger.add(
        sys.stdout,
        format=log_format,
        level="INFO",
        colorize=True
    )
    
    # Единый лог файл (все сообщения)
    logger.add(
        f"{logs_dir}/bot.log",
        format=log_format,
        level="INFO",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8"
    )

    # Отдельный файл только для ошибок
    logger.add(
        f"{logs_dir}/error.log",
        format=log_format,
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        filter=lambda record: record["level"].name == "ERROR"
    )
    
    logger.info("Логирование настроено")