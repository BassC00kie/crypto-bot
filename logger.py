import logging
import os
from datetime import datetime

LOG_FILE = 'bot.log'

def setup_logger():
    """Настраиваем логгер — пишет и в файл и в консоль"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),      # в файл
            logging.StreamHandler()             # в консоль
        ]
    )
    return logging.getLogger('bot')

logger = setup_logger()