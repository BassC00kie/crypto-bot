# ═══════════════════════════════════════════════════════════════════
#  core/balance_manager.py
#
#  Следит за балансами на всех биржах.
#  Отвечает на вопросы:
#    - Хватает ли денег для сделки?
#    - Сколько всего капитала в боте?
#    - Не упал ли баланс ниже минимума?
#
#  Использование:
#      from core.balance_manager import BalanceManager
#      bm = BalanceManager(exchange_manager)
#      await bm.update()
#      can_trade = bm.can_trade('bybit', 'USDT', 100)
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import asyncio
import json
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    EXCHANGES, TOTAL_CAPITAL_USD, CAPITAL_ALLOCATION,
    RESERVE_USD, RISK, DRY_RUN, PATHS
)
from logger import logger


class BalanceManager:
    """
    Центральный менеджер балансов.
    Кэширует балансы и обновляет их по расписанию из config.INTERVALS.
    """

    def __init__(self, exchange_manager):
        self.em              = exchange_manager
        self._balances       = {}       # {биржа: {монета: сумма}}
        self._last_update    = None     # когда последний раз обновляли
        self._reserved       = {}       # {биржа: {монета: зарезервировано под открытые позиции}}
        self._daily_start    = {}       # балансы на начало дня (для подсчёта P&L)
        self._initialized    = False

    # ──────────────────────────────────────────────────────────────
    # ОБНОВЛЕНИЕ БАЛАНСОВ
    # ──────────────────────────────────────────────────────────────

    async def preload_markets(self):
        """
        Загружает рынки на всех биржах один раз при старте.
        Без этого ccxt пытается грузить рынки при каждом запросе.
        """
        for name in self.em.get_connected():
            for obj in [self.em.get(name, auth=True), self.em.get(name, auth=False)]:
                if obj is None:
                    continue
                try:
                    await obj.load_markets()
                    logger.info(f"📚 {name}: рынки загружены")
                except Exception as e:
                    logger.warning(f"⚠️  {name}: ошибка загрузки рынков — {e}")

    async def update(self):
        """
        Запрашивает балансы на всех подключённых биржах.
        Вызывается из main.py по расписанию (каждые 60 сек).
        """
        updated = []

        for name in self.em.get_connected():
            try:
                balance = await self.em.fetch_balance_async(name)
                if balance:
                    self._balances[name] = balance
                    updated.append(name)
            except Exception as e:
                logger.error(f"❌ {name}: ошибка обновления баланса — {e}")

        self._last_update = datetime.now()

        # Запоминаем начальные балансы при первом запуске
        if not self._initialized and updated:
            self._daily_start = {
                name: dict(bal)
                for name, bal in self._balances.items()
            }
            self._initialized = True
            logger.info(f"💾 Начальные балансы сохранены")

        if updated:
            logger.info(
                f"💰 Балансы обновлены: {updated} | "
                f"Итого USDT: ${self.total_usdt():,.2f}"
            )

        return self._balances

    def update_sync(self):
        """Синхронная версия для простых скриптов"""
        for name in self.em.get_connected():
            try:
                balance = self.em.fetch_balance(name)
                if balance:
                    self._balances[name] = balance
            except Exception as e:
                logger.error(f"❌ {name}: ошибка баланса — {e}")

        self._last_update = datetime.now()
        if not self._initialized:
            self._daily_start = {
                name: dict(bal)
                for name, bal in self._balances.items()
            }
            self._initialized = True

        return self._balances

    # ──────────────────────────────────────────────────────────────
    # ПОЛУЧЕНИЕ БАЛАНСА
    # ──────────────────────────────────────────────────────────────

    def get(self, exchange_name: str, coin: str = 'USDT') -> float:
        """
        Возвращает свободный баланс монеты на бирже.

        Пример:
            usdt = bm.get('bybit', 'USDT')   # → 1250.0
            btc  = bm.get('kucoin', 'BTC')   # → 0.05
        """
        exchange_bal = self._balances.get(exchange_name, {})
        total        = exchange_bal.get(coin, 0.0)
        reserved     = self._reserved.get(exchange_name, {}).get(coin, 0.0)
        return max(0.0, total - reserved)

    def get_all(self, coin: str = 'USDT') -> dict:
        """
        Возвращает баланс монеты на всех биржах.

        Пример:
            balances = bm.get_all('USDT')
            # → {'bybit': 1250.0, 'kucoin': 800.0, 'okx': 950.0}
        """
        result = {}
        for name in self.em.get_connected():
            result[name] = self.get(name, coin)
        return result

    def total_usdt(self) -> float:
        """
        Считает суммарный USDT баланс по всем биржам.
        Включает USDT + стоимость других монет (приблизительно).
        """
        total = 0.0
        for name in self.em.get_connected():
            total += self.get(name, 'USDT')
        return total

    def richest_exchange(self, coin: str = 'USDT') -> tuple:
        """
        Возвращает биржу с наибольшим балансом.

        Пример:
            name, amount = bm.richest_exchange('USDT')
            # → ('bybit', 1250.0)
        """
        balances = self.get_all(coin)
        if not balances:
            return None, 0.0
        name = max(balances, key=balances.get)
        return name, balances[name]

    # ──────────────────────────────────────────────────────────────
    # ПРОВЕРКА ВОЗМОЖНОСТИ ТОРГОВЛИ
    # ──────────────────────────────────────────────────────────────

    def can_trade(self, exchange_name: str, coin: str, amount_usd: float) -> tuple:
        """
        Проверяет можно ли открыть сделку на данной бирже.
        Возвращает (можно: bool, причина: str)

        Пример:
            ok, reason = bm.can_trade('bybit', 'USDT', 500)
            if ok:
                # открываем сделку
            else:
                logger.warning(reason)
        """
        balance = self.get(exchange_name, coin)
        min_bal = RISK.get('min_exchange_balance_usd', 50)

        # Проверка 1: хватает ли денег на сделку
        if balance < amount_usd:
            return False, (
                f"{exchange_name}: недостаточно {coin} "
                f"(нужно ${amount_usd:.2f}, есть ${balance:.2f})"
            )

        # Проверка 2: не упадёт ли баланс ниже минимума после сделки
        balance_after = balance - amount_usd
        if balance_after < min_bal:
            return False, (
                f"{exchange_name}: после сделки останется "
                f"${balance_after:.2f} < минимум ${min_bal}"
            )

        # Проверка 3: баланс вообще загружен
        if not self._initialized:
            return False, "Балансы ещё не загружены"

        return True, "ok"

    def can_trade_both(
        self,
        buy_exchange: str,
        sell_exchange: str,
        coin: str,
        amount_usd: float
    ) -> tuple:
        """
        Проверяет можно ли открыть CEX-CEX сделку (нужны деньги на обеих биржах).

        Пример:
            ok, reason = bm.can_trade_both('bybit', 'kucoin', 'USDT', 200)
        """
        ok1, reason1 = self.can_trade(buy_exchange, coin, amount_usd)
        if not ok1:
            return False, reason1

        ok2, reason2 = self.can_trade(sell_exchange, coin, amount_usd)
        if not ok2:
            return False, reason2

        return True, "ok"

    def get_strategy_budget(self, strategy: str) -> float:
        """
        Возвращает доступный бюджет для стратегии согласно config.

        Пример:
            budget = bm.get_strategy_budget('cex_cex')  # → 2000.0
        """
        pct    = CAPITAL_ALLOCATION.get(strategy, 0)
        budget = TOTAL_CAPITAL_USD * pct
        # Не даём тратить резерв
        available = self.total_usdt() - RESERVE_USD
        return min(budget, max(0, available))

    # ──────────────────────────────────────────────────────────────
    # РЕЗЕРВИРОВАНИЕ (блокировка под открытые позиции)
    # ──────────────────────────────────────────────────────────────

    def reserve(self, exchange_name: str, coin: str, amount: float):
        """
        Резервирует средства под открытую позицию.
        Зарезервированные деньги не видны как свободные.

        Пример:
            bm.reserve('bybit', 'USDT', 500)   # открываем позицию
        """
        if exchange_name not in self._reserved:
            self._reserved[exchange_name] = {}
        current = self._reserved[exchange_name].get(coin, 0.0)
        self._reserved[exchange_name][coin] = current + amount
        logger.debug(f"🔒 {exchange_name}: зарезервировано {amount} {coin}")

    def release(self, exchange_name: str, coin: str, amount: float):
        """
        Освобождает зарезервированные средства после закрытия позиции.

        Пример:
            bm.release('bybit', 'USDT', 500)   # закрываем позицию
        """
        if exchange_name not in self._reserved:
            return
        current = self._reserved[exchange_name].get(coin, 0.0)
        self._reserved[exchange_name][coin] = max(0.0, current - amount)
        logger.debug(f"🔓 {exchange_name}: освобождено {amount} {coin}")

    def get_reserved(self, exchange_name: str, coin: str = 'USDT') -> float:
        """Сколько сейчас зарезервировано"""
        return self._reserved.get(exchange_name, {}).get(coin, 0.0)

    # ──────────────────────────────────────────────────────────────
    # P&L (прибыль/убыток за день)
    # ──────────────────────────────────────────────────────────────

    def daily_pnl(self) -> float:
        """
        Считает изменение суммарного баланса с начала дня.
        Используется risk_manager для проверки daily_loss лимита.

        Возвращает сумму в USD (положительная = прибыль, отрицательная = убыток)
        """
        if not self._daily_start:
            return 0.0

        start_total = sum(
            bal.get('USDT', 0.0)
            for bal in self._daily_start.values()
        )
        current_total = self.total_usdt()
        return current_total - start_total

    def daily_pnl_pct(self) -> float:
        """Дневной P&L в процентах от начального баланса"""
        if not self._daily_start:
            return 0.0

        start_total = sum(
            bal.get('USDT', 0.0)
            for bal in self._daily_start.values()
        )
        if start_total == 0:
            return 0.0

        return (self.daily_pnl() / start_total) * 100

    def reset_daily(self):
        """
        Сбрасывает дневную точку отсчёта.
        Вызывается в main.py каждый день в полночь.
        """
        self._daily_start = {
            name: dict(bal)
            for name, bal in self._balances.items()
        }
        logger.info("📅 Дневной баланс сброшен")

    # ──────────────────────────────────────────────────────────────
    # СТАТУС И ЛОГИ
    # ──────────────────────────────────────────────────────────────

    def status(self):
        """Печатает подробный статус балансов"""
        logger.info("═" * 55)
        logger.info("💰 БАЛАНСЫ НА БИРЖАХ")
        logger.info(f"   Обновлено: {self._last_update or 'не обновлялось'}")

        total_usdt = 0.0
        for name in self.em.get_connected():
            usdt     = self.get(name, 'USDT')
            reserved = self.get_reserved(name, 'USDT')
            total_usdt += usdt
            logger.info(
                f"   {name:8s} | USDT: ${usdt:>10,.2f}"
                + (f" (резерв: ${reserved:.2f})" if reserved > 0 else "")
            )

        logger.info(f"   {'─' * 40}")
        logger.info(f"   {'ИТОГО':8s} | USDT: ${total_usdt:>10,.2f}")

        pnl     = self.daily_pnl()
        pnl_pct = self.daily_pnl_pct()
        sign    = "+" if pnl >= 0 else ""
        logger.info(f"   Дневной P&L: {sign}${pnl:.2f} ({sign}{pnl_pct:.3f}%)")
        logger.info("═" * 55)

    def summary(self) -> dict:
        """
        Возвращает словарь с ключевыми метриками.
        Используется в Telegram отчёте.
        """
        return {
            'total_usdt':   self.total_usdt(),
            'by_exchange':  self.get_all('USDT'),
            'daily_pnl':    self.daily_pnl(),
            'daily_pnl_pct': self.daily_pnl_pct(),
            'last_update':  str(self._last_update),
            'reserve_usd':  RESERVE_USD,
        }

    def save_snapshot(self):
        """
        Сохраняет снимок балансов в файл.
        Вызывается периодически для истории.
        """
        snapshot = {
            'timestamp': datetime.now().isoformat(),
            'balances':  self._balances,
            'reserved':  self._reserved,
            'total_usdt': self.total_usdt(),
            'daily_pnl': self.daily_pnl(),
        }
        try:
            path = PATHS.get('stats_file', 'logs/stats.json')
            os.makedirs(os.path.dirname(path), exist_ok=True)

            # Загружаем историю
            history = []
            if os.path.exists(path):
                with open(path, 'r') as f:
                    data = json.load(f)
                    history = data.get('snapshots', [])

            # Добавляем новый снимок, храним последние 100
            history.append(snapshot)
            history = history[-100:]

            with open(path, 'w') as f:
                json.dump({'snapshots': history}, f, indent=2)

        except Exception as e:
            logger.error(f"❌ Ошибка сохранения снимка: {e}")


# ─────────────────────────────────────────────────────────────────
# ТЕСТ
# ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import asyncio
    from exchanges.exchange_manager import ExchangeManager

    async def test():
        print("Тестируем BalanceManager...\n")

        em = ExchangeManager(mode='async')
        bm = BalanceManager(em)

        # Обновляем балансы
        await bm.update()

        # Статус
        bm.status()

        # Проверяем можно ли торговать
        ok, reason = bm.can_trade('bybit', 'USDT', 100)
        print(f"\nМожно торговать $100 на Bybit: {ok} | {reason}")

        ok, reason = bm.can_trade('bybit', 'USDT', 999999)
        print(f"Можно торговать $999999 на Bybit: {ok} | {reason}")

        # Бюджеты стратегий
        print("\nБюджеты стратегий:")
        from config import CAPITAL_ALLOCATION
        for strategy in CAPITAL_ALLOCATION:
            budget = bm.get_strategy_budget(strategy)
            print(f"  {strategy:12s}: ${budget:,.2f}")

        # Закрываем соединения
        await em.close_all()

    asyncio.run(test())