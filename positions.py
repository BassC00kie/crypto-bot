import json
import os
from datetime import datetime

POSITIONS_FILE = 'positions.json'
TRADES_FILE    = 'trades.log'

# ===========================
# РАБОТА С ФАЙЛОМ ПОЗИЦИЙ
# ===========================

def load_positions():
    """Загружаем позиции из файла"""
    if not os.path.exists(POSITIONS_FILE):
        return []
    with open(POSITIONS_FILE, 'r') as f:
        return json.load(f)

def save_positions(positions):
    """Сохраняем позиции в файл"""
    with open(POSITIONS_FILE, 'w') as f:
        json.dump(positions, f, indent=2)

def add_position(coin, amount, price, funding_rate):
    """Добавляем новую позицию"""
    positions = load_positions()

    position = {
        'coin':              coin,
        'amount':            amount,
        'entry_price':       price,
        'entry_rate':        funding_rate,
        'opened_at':         datetime.now().isoformat(),
        'funding_collected': 0.0,
    }

    positions.append(position)
    save_positions(positions)
    print(f"💾 Позиция сохранена: {coin}")
    return position

def remove_position(coin):
    """Удаляем позицию после закрытия"""
    positions = load_positions()
    positions = [p for p in positions if p['coin'] != coin]
    save_positions(positions)

# ===========================
# ИСТОРИЯ СДЕЛОК
# ===========================

def log_trade(position, profit):
    """Записываем закрытую сделку в лог"""
    with open(TRADES_FILE, 'a') as f:
        line = (
            f"{datetime.now().isoformat()} | "
            f"{position['coin']} | "
            f"opened: {position['opened_at']} | "
            f"funding: ${position['funding_collected']:.4f} | "
            f"profit: ${profit:.4f}\n"
        )
        f.write(line)
    print(f"📝 Сделка записана в {TRADES_FILE}")
