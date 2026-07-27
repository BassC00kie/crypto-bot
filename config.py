# ═══════════════════════════════════════════════════════════════════
#  CRYPTO ARBITRAGE BOT — config.py
#  Все настройки бота в одном файле.
#  Меняй только этот файл — остальной код трогать не нужно.
# ═══════════════════════════════════════════════════════════════════

import os
from dotenv import load_dotenv

# Загружаем .env из корня проекта
load_dotenv()

# ──────────────────────────────────────────────────────────────────
# РЕЖИМ РАБОТЫ
# ──────────────────────────────────────────────────────────────────
DRY_RUN = True          # True = симуляция без реальных сделок
DEBUG   = False         # True = подробные логи в консоль

# ──────────────────────────────────────────────────────────────────
# CEX БИРЖИ
# enabled: False = биржа не используется (ключи можно не заполнять)
# Ключи читаются из .env — никогда не пиши их прямо здесь!
# ──────────────────────────────────────────────────────────────────
EXCHANGES = {
    'bybit': {
        'enabled':    True,
        'api_key':    os.getenv('BYBIT_API_KEY',    ''),
        'secret':     os.getenv('BYBIT_API_SECRET', ''),
        'password':   '',                                   # не нужен для Bybit
    },
    'kucoin': {
        'enabled':    True,
        'api_key':    os.getenv('KUCOIN_API_KEY',    ''),
        'secret':     os.getenv('KUCOIN_API_SECRET', ''),
        'password':   os.getenv('KUCOIN_PASSPHRASE', ''),   # KuCoin требует passphrase
    },
    'okx': {
        'enabled':    False,
        'api_key':    os.getenv('OKX_API_KEY',    ''),
        'secret':     os.getenv('OKX_API_SECRET', ''),
        'password':   os.getenv('OKX_PASSPHRASE', ''),      # OKX требует passphrase
    },
    'gate': {
        'enabled':    False,
        'api_key':    os.getenv('GATE_API_KEY',    ''),
        'secret':     os.getenv('GATE_API_SECRET', ''),
        'password':   '',
    },
    'bitget': {
        'enabled':    True,
        'api_key':    os.getenv('BITGET_API_KEY',    ''),
        'secret':     os.getenv('BITGET_API_SECRET', ''),
        'password':   os.getenv('BITGET_PASSPHRASE', ''),
    },
}

# ──────────────────────────────────────────────────────────────────
# DEX ПЛАТФОРМЫ
# rpc: адрес ноды (Infura / Alchemy / публичная)
# wallet: адрес твоего кошелька (0x...)
# private_key: приватный ключ — НИКОГДА не публикуй!
# ──────────────────────────────────────────────────────────────────
DEX = {
    'pancakeswap': {
        'enabled':      False,
        'network':      'bsc',
        'rpc':          os.getenv('BSC_NODE_URL', 'https://bsc-dataseed.binance.org/'),
        'wallet':       os.getenv('WALLET_ADDRESS',     ''),
        'private_key':  os.getenv('WALLET_PRIVATE_KEY', ''),
        'router':       '0x10ED43C718714eb63d5aA57B78B54704E256024E',  # PancakeSwap V2 router
        'fee_tier':     500,        # 0.05% — самый ликвидный пул
    },
    'uniswap_arbitrum': {
        'enabled':      False,
        'network':      'arbitrum',
        'rpc':          os.getenv('ARB_NODE_URL', 'https://arb1.arbitrum.io/rpc'),
        'wallet':       os.getenv('WALLET_ADDRESS',     ''),
        'private_key':  os.getenv('WALLET_PRIVATE_KEY', ''),
        'router':       '0xE592427A0AEce92De3Edee1F18E0157C05861564',  # Uniswap V3 router
        'fee_tier':     500,
    },
    'camelot': {
        'enabled':      False,
        'network':      'arbitrum',
        'rpc':          os.getenv('ARB_NODE_URL', 'https://arb1.arbitrum.io/rpc'),
        'wallet':       os.getenv('WALLET_ADDRESS',     ''),
        'private_key':  os.getenv('WALLET_PRIVATE_KEY', ''),
        'router':       '0xc873fEcbd354f5A56E00E710B90EF4201db2448d',
        'fee_tier':     300,
    },
    'quickswap': {
        'enabled':      False,
        'network':      'polygon',
        'rpc':          os.getenv('POLYGON_NODE_URL', 'https://polygon-bor-rpc.publicnode.com'),
        'wallet':       os.getenv('WALLET_ADDRESS',     ''),
        'private_key':  os.getenv('WALLET_PRIVATE_KEY', ''),
        'router':       '0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff',
        'fee_tier':     300,
    },
}

# ──────────────────────────────────────────────────────────────────
# КАПИТАЛ
# Меняешь TOTAL_CAPITAL_USD — проценты пересчитываются автоматически
# reserve: деньги которые бот НИКОГДА не трогает (газ, экстренные случаи)
# ──────────────────────────────────────────────────────────────────
TOTAL_CAPITAL_USD = 5000

CAPITAL_ALLOCATION = {
    'cex_cex':  0.40,   # 40% на межбиржевой арбитраж
    'funding':  0.30,   # 30% на фандинг арбитраж
    'dex_cex':  0.15,   # 15% на DEX-CEX арбитраж
    'basis':    0.10,   # 10% на basis trading
    'futures':  0.05,   # 5%  на фьючерсный арбитраж
    # сумма должна быть ровно 1.0
}

RESERVE_USD = 200       # резерв — не входит в TOTAL_CAPITAL_USD

# ──────────────────────────────────────────────────────────────────
# ЧАСТОТА ПРОВЕРКИ (в секундах)
# Каждая стратегия работает в своём цикле независимо
# ──────────────────────────────────────────────────────────────────
INTERVALS = {

    # ── CEX-CEX арбитраж ──────────────────────────────────────────
    # Спреды живут секунды — проверяем часто
    'cex_cex_scan':         10,     # сканирование спредов между биржами
    'cex_cex_monitor':      5,      # мониторинг открытых позиций

    # ── Фандинг арбитраж ──────────────────────────────────────────
    # Фандинг платится каждые 8ч — не нужно проверять каждую секунду
    'funding_scan':         300,    # сканирование ставок (5 минут)
    'funding_monitor':      60,     # мониторинг открытых позиций (1 минута)
    'funding_close_check':  3600,   # проверка нужно ли закрывать (1 час)

    # ── Basis Trading ─────────────────────────────────────────────
    # Базис меняется медленно
    'basis_scan':           300,    # сканирование базиса (5 минут)
    'basis_monitor':        120,    # мониторинг позиций (2 минуты)

    # ── DEX-CEX арбитраж ──────────────────────────────────────────
    # Блок BSC = 3 сек, Arbitrum = 0.25 сек — проверяем быстро
    'dex_cex_scan':         15,     # сканирование спредов DEX vs CEX
    'dex_cex_gas_check':    30,     # обновление цены газа

    # ── Фьючерсный арбитраж ───────────────────────────────────────
    # Базис до экспирации меняется медленно
    'futures_scan':         3600,   # сканирование (1 час)
    'futures_monitor':      1800,   # мониторинг позиций (30 минут)

    # ── Системные циклы ───────────────────────────────────────────
    'balance_update':       60,     # обновление балансов на всех биржах
    'risk_check':           10,     # проверка риск-лимитов
    'positions_save':       30,     # сохранение позиций в файл
    'telegram_report':      86400,  # ежедневный отчёт в Telegram (24ч)

}

# ──────────────────────────────────────────────────────────────────
# МОНЕТЫ ПО СТРАТЕГИЯМ
# Tier 1 — всегда сканируем
# Tier 2 — сканируем если нет сигналов в Tier 1
# Tier 3 — только при аномальных спредах
# ──────────────────────────────────────────────────────────────────
COINS = {

    'cex_cex': {
        'tier1': ['BTC', 'ETH', 'SOL', 'XRP', 'BNB'],
        'tier2': ['DOGE', 'ADA', 'AVAX', 'DOT', 'POL',
                  'LINK', 'UNI', 'ATOM', 'LTC', 'APT'],
        'tier3': ['SUI', 'ARB', 'OP', 'INJ', 'HYPE',
                  'WIF', 'PEPE', 'BONK'],
    },

    'funding': {
        'tier1': ['BTC', 'ETH', 'SOL', 'BNB', 'XRP'],
        'tier2': ['DOGE', 'PEPE', 'WIF', 'HYPE', 'SUI',
                  'APT', 'INJ', 'TIA'],
        'tier3': ['BONK', 'SHIB', 'FLOKI', 'WLD'],
    },

    'dex_cex': {
        'bsc':      ['BNB', 'ETH', 'BTC', 'CAKE', 'USDT'],
        'arbitrum': ['ETH', 'ARB', 'LINK', 'UNI', 'USDC'],
        'polygon':  ['POL', 'ETH', 'USDC', 'USDT'],
    },

    'basis':   ['BTC', 'ETH', 'SOL', 'BNB', 'XRP'],

    'futures': ['BTC', 'ETH', 'SOL', 'BNB', 'XRP'],

}

# ──────────────────────────────────────────────────────────────────
# ПОРОГИ ВХОДА
# Минимальные условия чтобы бот открыл сделку
# ──────────────────────────────────────────────────────────────────
THRESHOLDS = {

    # CEX-CEX: минимальный спред после вычета комиссий обеих бирж
    'cex_cex_min_spread_pct':       0.15,   # %

    # Фандинг: минимальная дневная доходность (3 выплаты × X)
    'funding_min_daily_pct':        0.03,   # %

    # DEX-CEX: минимальный спред и минимальная прибыль после газа
    'dex_cex_min_spread_pct':       0.20,   # %
    'dex_cex_min_profit_usd':       2.0,    # $ — защита от газа

    # Basis: минимальное отклонение базиса от нормы
    'basis_min_deviation_pct':      0.30,   # %

    # Futures: минимальная годовая доходность Cash-and-Carry
    'futures_min_annual_pct':       8.0,    # %

    # Газ: максимально допустимая цена газа для DEX сделок
    'max_gas_usd':                  5.0,    # $ — при дороже пропускаем

}

# ──────────────────────────────────────────────────────────────────
# РИСК-МЕНЕДЖМЕНТ
# Глобальные лимиты на весь бот
# ──────────────────────────────────────────────────────────────────
RISK = {

    # Максимальный размер одной позиции от общего капитала
    'max_position_pct':             0.10,   # 10%

    # Если за день потеряли X% — бот останавливается полностью
    'max_daily_loss_pct':           0.02,   # 2%

    # Максимум одновременно открытых позиций по всем стратегиям
    'max_open_positions':           10,

    # Если цена монеты резко прыгнула — не входим в сделку
    'max_price_change_pct':         5.0,    # %

    # Минимальный баланс на бирже — ниже этого не торгуем
    'min_exchange_balance_usd':     50,     # $

    # Максимальное количество неудачных сделок подряд → пауза
    'max_consecutive_losses':       3,

    # Пауза после серии потерь (секунды)
    'loss_pause_seconds':           3600,   # 1 час

}

# ──────────────────────────────────────────────────────────────────
# TELEGRAM УВЕДОМЛЕНИЯ
# ──────────────────────────────────────────────────────────────────
TELEGRAM = {
    'enabled':      False,
    'bot_token':    '',         # токен от @BotFather
    'chat_id':      '',         # твой chat_id

    # Что присылать
    'notify_trade_open':    True,   # открытие сделки
    'notify_trade_close':   True,   # закрытие сделки
    'notify_error':         True,   # ошибки
    'notify_daily_report':  True,   # ежедневный отчёт
    'notify_risk_alert':    True,   # срабатывание риск-лимитов
}

# ──────────────────────────────────────────────────────────────────
# ПУТИ К ФАЙЛАМ
# ──────────────────────────────────────────────────────────────────
PATHS = {
    'trades_log':       'logs/trades.log',
    'errors_log':       'logs/errors.log',
    'bot_log':          'logs/bot.log',
    'positions_file':   'logs/positions.json',
    'stats_file':       'logs/stats.json',
}

# ──────────────────────────────────────────────────────────────────
# МАППИНГ СИМВОЛОВ ПО БИРЖАМ
# Некоторые монеты называются по-разному на разных биржах.
# Ключ — универсальное название, значение — как называется на бирже.
# ──────────────────────────────────────────────────────────────────
SYMBOL_MAP = {
    # Bybit/Bitget используют 1000X для дешёвых монет
    'PEPE':  {'bybit': '1000PEPE',  'kucoin': 'PEPE',  'okx': 'PEPE',  'gate': 'PEPE',  'bitget': 'PEPE'},
    'BONK':  {'bybit': '1000BONK',  'kucoin': 'BONK',  'okx': 'BONK',  'gate': 'BONK',  'bitget': 'BONK'},
    'SHIB':  {'bybit': '1000SHIB',  'kucoin': 'SHIB',  'okx': 'SHIB',  'gate': 'SHIB',  'bitget': 'SHIB'},
    'FLOKI': {'bybit': '1000FLOKI', 'kucoin': 'FLOKI', 'okx': 'FLOKI', 'gate': 'FLOKI', 'bitget': 'FLOKI'},
    'LUNC':  {'bybit': '1000LUNC',  'kucoin': 'LUNC',  'okx': 'LUNC',  'gate': 'LUNC',  'bitget': 'LUNC'},
    'XEC':   {'bybit': '1000XEC',   'kucoin': 'XEC',   'okx': 'XEC',   'gate': 'XEC',   'bitget': 'XEC'},
    # Переименованные
    'POL':   {'bybit': 'POL', 'kucoin': 'POL', 'okx': 'POL', 'gate': 'POL', 'bitget': 'POL'},
    'WIF':   {'bybit': 'WIF', 'kucoin': 'WIF', 'okx': 'WIF', 'gate': 'WIF', 'bitget': 'WIF'},
}

# Монеты которых нет на конкретных биржах — бот пропустит тихо без ошибки
UNSUPPORTED_FUTURES = {
    'bybit':  ['BONK', 'SHIB', 'FLOKI'],
    'bitget': ['BONK'],
    'kucoin': [],
    'okx':    [],
    'gate':   [],
}

# ──────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ
# Возвращает реальный капитал для каждой стратегии в $
# ──────────────────────────────────────────────────────────────────
def get_strategy_capital(strategy: str) -> float:
    """
    Пример:
        get_strategy_capital('cex_cex')  →  2000.0  (при капитале $5000)
    """
    pct = CAPITAL_ALLOCATION.get(strategy, 0)
    return round(TOTAL_CAPITAL_USD * pct, 2)


def get_all_capitals() -> dict:
    """Возвращает капитал для всех стратегий в $"""
    return {
        strategy: get_strategy_capital(strategy)
        for strategy in CAPITAL_ALLOCATION
    }


# ──────────────────────────────────────────────────────────────────
# ПРОВЕРКА КОНФИГА при запуске
# ──────────────────────────────────────────────────────────────────
def validate_config():
    """Проверяет что конфиг заполнен корректно. Вызывается из main.py"""
    errors = []

    # Проверяем что сумма аллокаций = 1.0
    total = sum(CAPITAL_ALLOCATION.values())
    if abs(total - 1.0) > 0.001:
        errors.append(f"CAPITAL_ALLOCATION сумма = {total:.3f}, должна быть 1.0")

    # Проверяем что включена хотя бы одна биржа
    enabled_exchanges = [k for k, v in EXCHANGES.items() if v['enabled']]
    if len(enabled_exchanges) < 2:
        errors.append("Нужно включить минимум 2 биржи для CEX-CEX арбитража")

    # Проверяем API ключи для включённых бирж
    for name, cfg in EXCHANGES.items():
        if cfg['enabled'] and not DRY_RUN:
            if not cfg['api_key'] or not cfg['secret']:
                errors.append(f"Биржа {name}: не заполнены API ключи")

    # Проверяем DEX кошельки
    for name, cfg in DEX.items():
        if cfg['enabled'] and not DRY_RUN:
            if not cfg['wallet'] or not cfg['private_key']:
                errors.append(f"DEX {name}: не заполнен кошелёк или приватный ключ")

    if errors:
        print("❌ Ошибки в конфиге:")
        for e in errors:
            print(f"   • {e}")
        return False

    print("✅ Конфиг проверен — всё в порядке")
    print(f"   Режим: {'DRY RUN (симуляция)' if DRY_RUN else '🔴 РЕАЛЬНАЯ ТОРГОВЛЯ'}")
    print(f"   Биржи: {', '.join(enabled_exchanges)}")
    print(f"   Капитал: ${TOTAL_CAPITAL_USD:,}")
    for strategy, amount in get_all_capitals().items():
        print(f"   {strategy:12s}: ${amount:,.0f}")
    return True


if __name__ == '__main__':
    validate_config()