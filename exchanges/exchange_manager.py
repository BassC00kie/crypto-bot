# ═══════════════════════════════════════════════════════════════════
#  exchanges/exchange_manager.py
#
#  Центральный менеджер подключений ко всем CEX биржам.
#  Все стратегии берут биржи отсюда — не создают свои подключения.
#
#  Использование в стратегиях:
#      from exchanges.exchange_manager import ExchangeManager
#      em = ExchangeManager()
#      bybit  = em.get('bybit')
#      kucoin = em.get('kucoin')
#      all    = em.get_all()
# ═══════════════════════════════════════════════════════════════════

import ccxt
import ccxt.async_support as ccxt_async
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import EXCHANGES, DRY_RUN, DEBUG
from logger import logger


class ExchangeManager:
    """
    Создаёт и хранит подключения ко всем включённым биржам.
    Поддерживает два режима:
      - sync:  обычные объекты ccxt (для простых скриптов)
      - async: ccxt.async_support (для asyncio в main.py)
    """

    # Маппинг названий из config → классы ccxt
    SYNC_CLASSES = {
        'bybit':  ccxt.bybit,
        'kucoin': ccxt.kucoin,
        'okx':    ccxt.okx,
        'gate':   ccxt.gateio,
        'bitget': ccxt.bitget,
    }

    ASYNC_CLASSES = {
        'bybit':  ccxt_async.bybit,
        'kucoin': ccxt_async.kucoin,
        'okx':    ccxt_async.okx,
        'gate':   ccxt_async.gateio,
        'bitget': ccxt_async.bitget,
    }

    # Комиссии каждой биржи (спот, maker/taker)
    FEES = {
        'bybit':  {'spot': 0.001,  'futures': 0.0006},
        'kucoin': {'spot': 0.001,  'futures': 0.0006},
        'okx':    {'spot': 0.0008, 'futures': 0.0005},
        'gate':   {'spot': 0.002,  'futures': 0.0005},
        'bitget': {'spot': 0.001,  'futures': 0.0006},
    }

    def __init__(self, mode: str = 'sync'):
        """
        mode: 'sync' или 'async'
        """
        self.mode       = mode
        self._exchanges = {}   # биржи с авторизацией (для торговли)
        self._public    = {}   # биржи без ключей (только чтение цен)
        self._connected = []   # список успешно подключённых

        self._init_exchanges()

    # ──────────────────────────────────────────────────────────────
    # ИНИЦИАЛИЗАЦИЯ
    # ──────────────────────────────────────────────────────────────

    def _init_exchanges(self):
        """Создаём подключения ко всем включённым биржам"""
        classes = self.ASYNC_CLASSES if self.mode == 'async' else self.SYNC_CLASSES

        for name, cfg in EXCHANGES.items():
            if not cfg.get('enabled', False):
                if DEBUG:
                    logger.debug(f"⏭️  {name}: отключён в config")
                continue

            try:
                exchange_class = classes.get(name)
                if not exchange_class:
                    logger.warning(f"⚠️  {name}: неизвестная биржа")
                    continue

                # Параметры подключения
                params = {
                    'enableRateLimit': True,
                    'timeout':         10000,
                    'options': {
                        'adjustForTimeDifference': True,
                    },
                }

                # Добавляем API ключи если есть
                if cfg.get('api_key'):
                    params['apiKey'] = cfg['api_key']
                if cfg.get('secret'):
                    params['secret'] = cfg['secret']
                if cfg.get('password'):
                    params['password'] = cfg['password']

                # Создаём авторизованный объект
                exchange_obj = exchange_class(params)
                self._exchanges[name] = exchange_obj

                # Публичный объект (только для чтения цен)
                pub_class = classes.get(name)
                pub_obj   = pub_class({
                    'enableRateLimit': True,
                    'timeout':         10000,
                    'options': {
                        'adjustForTimeDifference': True,
                    },
                })
                self._public[name] = pub_obj

                self._connected.append(name)
                logger.info(f"✅ {name}: подключён")

            except Exception as e:
                logger.error(f"❌ {name}: ошибка подключения — {e}")

        if not self._connected:
            logger.error("❌ Ни одна биржа не подключилась!")
        else:
            logger.info(f"📡 Подключено бирж: {len(self._connected)} → {self._connected}")

    # ──────────────────────────────────────────────────────────────
    # ПОЛУЧЕНИЕ БИРЖ
    # ──────────────────────────────────────────────────────────────

    def get(self, name: str, auth: bool = True):
        """
        Возвращает объект биржи по имени.

        auth=True  → биржа с API ключами (для торговли)
        auth=False → биржа без ключей (только цены)

        Пример:
            bybit = em.get('bybit')           # авторизованный
            bybit = em.get('bybit', auth=False)  # публичный
        """
        if auth:
            exchange = self._exchanges.get(name)
        else:
            exchange = self._public.get(name)

        if not exchange:
            logger.warning(f"⚠️  Биржа '{name}' не найдена или отключена")
        return exchange

    def get_all(self, auth: bool = True) -> dict:
        """
        Возвращает словарь всех подключённых бирж.

        Пример:
            for name, exchange in em.get_all().items():
                price = exchange.fetch_ticker('BTC/USDT')
        """
        return self._exchanges if auth else self._public

    def get_connected(self) -> list:
        """Возвращает список названий подключённых бирж"""
        return self._connected.copy()

    def is_connected(self, name: str) -> bool:
        """Проверяет подключена ли биржа"""
        return name in self._connected

    def resolve_symbol(self, exchange_name: str, coin: str, market: str = 'spot') -> str | None:
        """
        Возвращает правильный символ для биржи.
        Проверяет загруженные рынки — если символа нет, возвращает None.

        Логика:
        1. Берём маппинг из SYMBOL_MAP если есть
        2. Строим символ (например 1000PEPE/USDT:USDT)
        3. Проверяем что такой символ реально есть на бирже
        4. Если нет — возвращаем None (стратегия тихо пропустит)

        Пример:
            em.resolve_symbol('bybit', 'PEPE', 'futures')  → '1000PEPE/USDT:USDT'
            em.resolve_symbol('bybit', 'BONK', 'futures')  → None (нет такого рынка)
            em.resolve_symbol('kucoin', 'PEPE', 'spot')    → 'PEPE/USDT'
        """
        from config import SYMBOL_MAP

        # Берём маппинг для монеты если есть, иначе используем оригинал
        mapped_coin = SYMBOL_MAP.get(coin, {}).get(exchange_name, coin)
        suffix      = '/USDT:USDT' if market == 'futures' else '/USDT'
        symbol      = f"{mapped_coin}{suffix}"

        # Проверяем что символ реально есть на бирже
        exchange = self._public.get(exchange_name)
        if exchange and hasattr(exchange, 'markets') and exchange.markets:
            if symbol not in exchange.markets:
                # Пробуем с оригинальным названием монеты (без маппинга)
                fallback = f"{coin}{suffix}"
                if fallback in exchange.markets:
                    return fallback
                # Ни один вариант не найден
                logger.debug(f"⏭️  {exchange_name}: {symbol} не найден — пропускаем")
                return None

        return symbol

    def resolve_coin(self, exchange_name: str, coin: str) -> str:
        """
        Возвращает только название монеты без суффикса.

        Пример:
            em.resolve_coin('bybit', 'PEPE')  → '1000PEPE'
            em.resolve_coin('kucoin', 'PEPE') → 'PEPE'
        """
        from config import SYMBOL_MAP
        return SYMBOL_MAP.get(coin, {}).get(exchange_name, coin)

    def get_fee(self, exchange_name: str, market: str = 'spot') -> float:
        """
        Возвращает комиссию биржи.
        market: 'spot' или 'futures'

        Пример:
            fee = em.get_fee('bybit', 'spot')  # → 0.001
        """
        return self.FEES.get(exchange_name, {}).get(market, 0.001)

    def get_total_fee(self, ex1: str, ex2: str, market: str = 'spot') -> float:
        """
        Суммарная комиссия двух бирж (для CEX-CEX арбитража).
        Включает открытие + закрытие на обеих биржах.

        Пример:
            total = em.get_total_fee('bybit', 'kucoin')  # → 0.004
        """
        fee1 = self.get_fee(ex1, market)
        fee2 = self.get_fee(ex2, market)
        return (fee1 + fee2) * 2  # туда + обратно

    # ──────────────────────────────────────────────────────────────
    # ПОЛУЧЕНИЕ ЦЕН
    # ──────────────────────────────────────────────────────────────

    def fetch_price(self, exchange_name: str, symbol: str) -> float | None:
        """
        Получает цену пары на бирже (синхронно).
        Использует публичный объект — не тратит лимиты API ключей.

        Пример:
            price = em.fetch_price('bybit', 'BTC/USDT')
        """
        exchange = self.get(exchange_name, auth=False)
        if not exchange:
            return None
        try:
            ticker = exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            logger.warning(f"⚠️  {exchange_name} {symbol}: ошибка цены — {e}")
            return None

    async def fetch_price_async(self, exchange_name: str, symbol: str) -> float | None:
        """
        Асинхронная версия fetch_price для использования в asyncio.

        Пример:
            price = await em.fetch_price_async('bybit', 'BTC/USDT')
        """
        exchange = self.get(exchange_name, auth=False)
        if not exchange:
            return None
        try:
            ticker = await exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            logger.warning(f"⚠️  {exchange_name} {symbol}: ошибка цены — {e}")
            return None

    def fetch_prices_all(self, symbol: str) -> dict:
        """
        Получает цену одной монеты на всех подключённых биржах.
        Возвращает словарь {биржа: цена}.

        Используется в CEX-CEX сканере.

        Пример:
            prices = em.fetch_prices_all('BTC/USDT')
            # → {'bybit': 65000.0, 'kucoin': 65050.0, 'okx': 64980.0}
        """
        prices = {}
        for name in self._connected:
            price = self.fetch_price(name, symbol)
            if price:
                prices[name] = price
        return prices

    async def fetch_prices_all_async(self, symbol: str) -> dict:
        """
        Асинхронная версия fetch_prices_all.
        Запрашивает все биржи параллельно — быстрее!

        Пример:
            prices = await em.fetch_prices_all_async('BTC/USDT')
        """
        import asyncio

        async def fetch_one(name):
            price = await self.fetch_price_async(name, symbol)
            return name, price

        tasks   = [fetch_one(name) for name in self._connected]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        prices = {}
        for result in results:
            if isinstance(result, Exception):
                continue
            name, price = result
            if price:
                prices[name] = price

        return prices

    # ──────────────────────────────────────────────────────────────
    # БАЛАНС
    # ──────────────────────────────────────────────────────────────

    def fetch_balance(self, exchange_name: str) -> dict | None:
        """
        Получает баланс на бирже (синхронно).
        Возвращает словарь {монета: {'free': X, 'used': Y, 'total': Z}}

        Пример:
            balance = em.fetch_balance('bybit')
            usdt = balance['USDT']['free']
        """
        exchange = self.get(exchange_name, auth=True)
        if not exchange:
            return None

        if DRY_RUN:
            # В DRY_RUN возвращаем мок-баланс
            return self._mock_balance()

        try:
            balance = exchange.fetch_balance()
            return balance['total']
        except Exception as e:
            logger.error(f"❌ {exchange_name}: ошибка баланса — {e}")
            return None

    async def fetch_balance_async(self, exchange_name: str) -> dict | None:
        """Асинхронная версия fetch_balance"""
        exchange = self.get(exchange_name, auth=True)
        if not exchange:
            return None

        if DRY_RUN:
            return self._mock_balance()

        try:
            balance = await exchange.fetch_balance()
            return balance['total']
        except Exception as e:
            logger.error(f"❌ {exchange_name}: ошибка баланса — {e}")
            return None

    def fetch_all_balances(self) -> dict:
        """
        Получает балансы на всех подключённых биржах.
        Возвращает {биржа: {монета: сумма}}

        Пример:
            balances = em.fetch_all_balances()
            bybit_usdt = balances['bybit']['USDT']
        """
        all_balances = {}
        for name in self._connected:
            balance = self.fetch_balance(name)
            if balance:
                all_balances[name] = balance
        return all_balances

    def _mock_balance(self) -> dict:
        """
        Единый мок-баланс для DRY_RUN режима.
        Используется ВСЕМИ стратегиями через exchange_manager.
        Никакой стратегии не нужно хардкодить свои балансы.
        """
        from config import TOTAL_CAPITAL_USD, CAPITAL_ALLOCATION
        dex_budget = TOTAL_CAPITAL_USD * CAPITAL_ALLOCATION.get('dex_cex', 0.15)
        return {
            # ── Стейблы ───────────────────────────────────────────
            'USDT':      1000.0,
            'USDC':       500.0,
            # ── Топ монеты ────────────────────────────────────────
            'BTC':          0.05,
            'ETH':          1.0,
            'SOL':         10.0,
            'BNB':          3.0,
            'XRP':        500.0,
            'DOGE':      2000.0,
            'ADA':       1000.0,
            'AVAX':        10.0,
            'DOT':        100.0,
            'LTC':          2.0,
            # ── Альты ─────────────────────────────────────────────
            'LINK':        50.0,
            'UNI':         50.0,
            'ATOM':        50.0,
            'APT':         30.0,
            'SUI':        200.0,
            'ARB':        200.0,
            'OP':         100.0,
            'INJ':         20.0,
            'TIA':         50.0,
            'WLD':        100.0,
            'HYPE':        50.0,
            # ── Мемкоины ──────────────────────────────────────────
            'WIF':        200.0,
            'PEPE':   1000000.0,
            '1000PEPE':  1000.0,   # Bybit формат
            'BONK':   5000000.0,
            '1000BONK':  5000.0,   # Bybit формат
            'SHIB':  10000000.0,
            '1000SHIB':  10000.0,
            'FLOKI':  500000.0,
            '1000FLOKI': 500.0,
            # ── DEX токены ────────────────────────────────────────
            'CAKE':       100.0,
            'POL':        500.0,
            'MATIC':      500.0,
        }

    def get_mock_wallet_usdt(self) -> float:
        """
        Мок USDT баланс MetaMask кошелька для DRY_RUN.
        Используется в DEXScanner вместо хардкода $250.
        """
        from config import TOTAL_CAPITAL_USD, CAPITAL_ALLOCATION
        return TOTAL_CAPITAL_USD * CAPITAL_ALLOCATION.get('dex_cex', 0.15)

    # ──────────────────────────────────────────────────────────────
    # ОРДЕРА
    # ──────────────────────────────────────────────────────────────

    def create_market_buy(self, exchange_name: str, symbol: str, amount: float) -> dict | None:
        """
        Создаёт рыночный ордер на покупку.

        Пример:
            order = em.create_market_buy('bybit', 'BTC/USDT', 0.001)
        """
        if DRY_RUN:
            logger.info(f"🧪 [DRY] {exchange_name}: BUY {amount} {symbol}")
            return {'id': 'dry_run', 'status': 'simulated'}

        exchange = self.get(exchange_name, auth=True)
        if not exchange:
            return None
        try:
            order = exchange.create_market_buy_order(symbol, amount)
            logger.info(f"✅ {exchange_name}: BUY {amount} {symbol} → {order['id']}")
            return order
        except Exception as e:
            logger.error(f"❌ {exchange_name}: ошибка BUY {symbol} — {e}")
            return None

    def create_market_sell(self, exchange_name: str, symbol: str, amount: float) -> dict | None:
        """
        Создаёт рыночный ордер на продажу.

        Пример:
            order = em.create_market_sell('kucoin', 'BTC/USDT', 0.001)
        """
        if DRY_RUN:
            logger.info(f"🧪 [DRY] {exchange_name}: SELL {amount} {symbol}")
            return {'id': 'dry_run', 'status': 'simulated'}

        exchange = self.get(exchange_name, auth=True)
        if not exchange:
            return None
        try:
            order = exchange.create_market_sell_order(symbol, amount)
            logger.info(f"✅ {exchange_name}: SELL {amount} {symbol} → {order['id']}")
            return order
        except Exception as e:
            logger.error(f"❌ {exchange_name}: ошибка SELL {symbol} — {e}")
            return None

    async def create_market_buy_async(self, exchange_name: str, symbol: str, amount: float) -> dict | None:
        """Асинхронная версия create_market_buy"""
        if DRY_RUN:
            logger.info(f"🧪 [DRY] {exchange_name}: BUY {amount} {symbol}")
            return {'id': 'dry_run', 'status': 'simulated'}

        exchange = self.get(exchange_name, auth=True)
        if not exchange:
            return None
        try:
            order = await exchange.create_market_buy_order(symbol, amount)
            logger.info(f"✅ {exchange_name}: BUY {amount} {symbol} → {order['id']}")
            return order
        except Exception as e:
            logger.error(f"❌ {exchange_name}: ошибка BUY {symbol} — {e}")
            return None

    async def create_market_sell_async(self, exchange_name: str, symbol: str, amount: float) -> dict | None:
        """Асинхронная версия create_market_sell"""
        if DRY_RUN:
            logger.info(f"🧪 [DRY] {exchange_name}: SELL {amount} {symbol}")
            return {'id': 'dry_run', 'status': 'simulated'}

        exchange = self.get(exchange_name, auth=True)
        if not exchange:
            return None
        try:
            order = await exchange.create_market_sell_order(symbol, amount)
            logger.info(f"✅ {exchange_name}: SELL {amount} {symbol} → {order['id']}")
            return order
        except Exception as e:
            logger.error(f"❌ {exchange_name}: ошибка SELL {symbol} — {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # ФАНДИНГ
    # ──────────────────────────────────────────────────────────────

    def fetch_funding_rate(self, exchange_name: str, symbol: str) -> float | None:
        """
        Получает текущую ставку фандинга.

        Пример:
            rate = em.fetch_funding_rate('bybit', 'BTC/USDT:USDT')
        """
        exchange = self.get(exchange_name, auth=False)
        if not exchange:
            return None
        try:
            data = exchange.fetch_funding_rate(symbol)
            return data['fundingRate']
        except Exception as e:
            logger.warning(f"⚠️  {exchange_name} {symbol}: ошибка фандинга — {e}")
            return None

    async def fetch_funding_rate_async(self, exchange_name: str, symbol: str) -> float | None:
        """Асинхронная версия fetch_funding_rate"""
        exchange = self.get(exchange_name, auth=False)
        if not exchange:
            return None
        try:
            data = await exchange.fetch_funding_rate(symbol)
            return data['fundingRate']
        except Exception as e:
            logger.warning(f"⚠️  {exchange_name} {symbol}: ошибка фандинга — {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # ЗАКРЫТИЕ СОЕДИНЕНИЙ (важно для asyncio)
    # ──────────────────────────────────────────────────────────────

    async def close_all(self):
        """
        Закрывает все async соединения.
        Вызывай в main.py при завершении бота.

        Пример:
            await em.close_all()
        """
        if self.mode != 'async':
            return

        for name, exchange in self._exchanges.items():
            try:
                await exchange.close()
            except Exception:
                pass

        for name, exchange in self._public.items():
            try:
                await exchange.close()
            except Exception:
                pass

        logger.info("🔌 Все соединения закрыты")

    # ──────────────────────────────────────────────────────────────
    # СТАТУС
    # ──────────────────────────────────────────────────────────────

    def status(self):
        """Печатает статус всех подключений"""
        logger.info("═" * 50)
        logger.info("📡 EXCHANGE MANAGER СТАТУС")
        logger.info(f"   Режим:     {self.mode}")
        logger.info(f"   DRY_RUN:   {DRY_RUN}")
        logger.info(f"   Подключено: {len(self._connected)} бирж")
        for name in self._connected:
            fee_spot    = self.get_fee(name, 'spot') * 100
            fee_futures = self.get_fee(name, 'futures') * 100
            logger.info(f"   ✅ {name:8s} | спот: {fee_spot:.2f}% | фьюч: {fee_futures:.3f}%")
        logger.info("═" * 50)


# ─────────────────────────────────────────────────────────────────
# СИНГЛТОН — один менеджер на весь бот
# ─────────────────────────────────────────────────────────────────

_sync_manager  = None
_async_manager = None


def get_exchange_manager(mode: str = 'sync') -> ExchangeManager:
    """
    Возвращает единственный экземпляр ExchangeManager.
    Все стратегии используют один и тот же объект.

    Пример:
        from exchanges.exchange_manager import get_exchange_manager
        em = get_exchange_manager('async')
        bybit = em.get('bybit')
    """
    global _sync_manager, _async_manager

    if mode == 'async':
        if _async_manager is None:
            _async_manager = ExchangeManager(mode='async')
        return _async_manager
    else:
        if _sync_manager is None:
            _sync_manager = ExchangeManager(mode='sync')
        return _sync_manager


# ─────────────────────────────────────────────────────────────────
# ТЕСТ — запускай напрямую чтобы проверить подключения
# ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("Тестируем подключения к биржам...\n")

    em = ExchangeManager(mode='sync')
    em.status()

    print("\nПроверяем цены BTC/USDT:")
    prices = em.fetch_prices_all('BTC/USDT')
    for exchange, price in prices.items():
        print(f"  {exchange:8s}: ${price:,.2f}")

    print("\nПроверяем балансы:")
    balances = em.fetch_all_balances()
    for exchange, balance in balances.items():
        usdt = balance.get('USDT', 0)
        print(f"  {exchange:8s}: USDT = ${usdt:,.2f}")