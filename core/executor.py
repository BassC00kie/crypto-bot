# ═══════════════════════════════════════════════════════════════════
#  core/executor.py
#
#  Исполнитель сделок. Забирает сигналы из SignalQueue
#  и выполняет ордера через ExchangeManager.
#  Перед каждой сделкой проверяет RiskManager и BalanceManager.
#
#  Использование:
#      from core.executor import Executor
#      executor = Executor(exchange_manager, balance_manager, risk_manager, signal_queue)
#      await executor.run()  # запускается как asyncio задача
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DRY_RUN, RISK
from logger import logger
from core.signal_queue import Signal


class Executor:
    """
    Читает сигналы из очереди и исполняет сделки.
    Работает как бесконечный asyncio loop в main.py.
    """

    def __init__(self, exchange_manager, balance_manager, risk_manager, signal_queue):
        self.em = exchange_manager
        self.bm = balance_manager
        self.rm = risk_manager
        self.sq = signal_queue

    # ──────────────────────────────────────────────────────────────
    # ГЛАВНЫЙ ЦИКЛ
    # ──────────────────────────────────────────────────────────────

    async def run(self):
        """
        Бесконечный цикл — читает сигналы и исполняет.
        Запускается через asyncio.create_task() в main.py
        """
        logger.info("⚡ Executor запущен")

        while True:
            try:
                signal = await self.sq.get(timeout=1.0)
                if signal:
                    await self._execute(signal)
            except asyncio.CancelledError:
                logger.info("⚡ Executor остановлен")
                break
            except Exception as e:
                logger.error(f"❌ Executor ошибка: {e}")
                await asyncio.sleep(1)

    # ──────────────────────────────────────────────────────────────
    # РОУТЕР — выбирает нужный обработчик по стратегии
    # ──────────────────────────────────────────────────────────────

    async def _execute(self, signal: Signal):
        """Роутит сигнал к нужному обработчику"""
        logger.info(f"⚡ Исполняю: {signal}")

        handlers = {
            'cex_cex': self._execute_cex_cex,
            'funding':  self._execute_funding,
            'basis':    self._execute_basis,
            'dex_cex':  self._execute_dex_cex,
            'futures':  self._execute_futures,
        }

        handler = handlers.get(signal.strategy)
        if not handler:
            logger.error(f"❌ Неизвестная стратегия: {signal.strategy}")
            return

        try:
            await handler(signal)
        except Exception as e:
            logger.error(f"❌ Ошибка исполнения {signal.strategy}: {e}")

    # ──────────────────────────────────────────────────────────────
    # CEX-CEX АРБИТРАЖ
    # ──────────────────────────────────────────────────────────────

    async def _execute_cex_cex(self, signal: Signal):
        """
        Одновременно покупает на одной бирже и продаёт на другой.
        """
        coin          = signal.coin
        buy_ex        = signal.buy_exchange
        sell_ex       = signal.sell_exchange
        amount_usd    = signal.amount_usd
        symbol        = f"{coin}/USDT"

        # Проверка риска
        ok, reason = self.rm.check_trade_cex_cex(buy_ex, sell_ex, amount_usd)
        if not ok:
            logger.warning(f"⚠️  CEX-CEX отклонено: {reason}")
            return

        # Считаем количество монет
        price       = await self.em.fetch_price_async(buy_ex, symbol)
        if not price:
            logger.error(f"❌ Не удалось получить цену {symbol} на {buy_ex}")
            return

        coin_amount = round(amount_usd / price, 6)

        logger.info(
            f"🔄 CEX-CEX | {coin} | "
            f"BUY {buy_ex} → SELL {sell_ex} | "
            f"{coin_amount} {coin} | ${amount_usd:.2f}"
        )

        # Исполняем параллельно
        buy_task  = self.em.create_market_buy_async(buy_ex, symbol, coin_amount)
        sell_task = self.em.create_market_sell_async(sell_ex, symbol, coin_amount)

        buy_order, sell_order = await asyncio.gather(
            buy_task, sell_task, return_exceptions=True
        )

        if isinstance(buy_order, Exception):
            logger.error(f"❌ BUY {buy_ex} ошибка: {buy_order}")
            return

        if isinstance(sell_order, Exception):
            logger.error(f"❌ SELL {sell_ex} ошибка: {sell_order}")
            return

        if buy_order and sell_order:
            # Резервируем баланс
            self.bm.reserve(buy_ex,  'USDT', amount_usd)
            self.bm.reserve(sell_ex, 'USDT', amount_usd)
            self.rm.on_position_opened('cex_cex', amount_usd)

            logger.info(
                f"✅ CEX-CEX исполнено | "
                f"спред: {signal.spread_pct:.3f}% | "
                f"ожид. прибыль: ${signal.expected_profit_usd:.4f}"
            )

            # Сразу освобождаем (CEX-CEX сделка мгновенная)
            await asyncio.sleep(0.5)
            self.bm.release(buy_ex,  'USDT', amount_usd)
            self.bm.release(sell_ex, 'USDT', amount_usd)
            self.rm.on_position_closed('cex_cex', signal.expected_profit_usd)

    # ──────────────────────────────────────────────────────────────
    # ФАНДИНГ АРБИТРАЖ
    # ──────────────────────────────────────────────────────────────

    async def _execute_funding(self, signal: Signal):
        """
        Открывает спот + шорт фьючерс для сбора фандинга.
        """
        coin       = signal.coin
        exchange   = signal.exchange or 'bybit'
        amount_usd = signal.amount_usd
        spot_sym   = f"{coin}/USDT"
        fut_sym    = f"{coin}/USDT:USDT"

        ok, reason = self.rm.check_trade('funding', exchange, amount_usd)
        if not ok:
            logger.warning(f"⚠️  Funding отклонено: {reason}")
            return

        price       = await self.em.fetch_price_async(exchange, spot_sym)
        if not price:
            return

        coin_amount = round(amount_usd / price, 6)

        logger.info(
            f"💰 FUNDING | {coin} | "
            f"SPOT BUY + SHORT | "
            f"{coin_amount} {coin} | ${amount_usd:.2f}"
        )

        # Покупаем спот
        spot_order = await self.em.create_market_buy_async(
            exchange, spot_sym, coin_amount
        )

        # Открываем шорт на фьючерсе
        short_order = None
        if not DRY_RUN:
            try:
                ex_obj = self.em.get(exchange, auth=True)
                short_order = await ex_obj.create_market_sell_order(
                    fut_sym, coin_amount,
                    params={"reduceOnly": False}
                )
            except Exception as e:
                logger.error(f"❌ SHORT ошибка: {e}")
        else:
            logger.info(f"🧪 [DRY] SHORT {coin_amount} {fut_sym}")
            short_order = {'id': 'dry_run', 'status': 'simulated'}

        if spot_order and short_order:
            self.bm.reserve(exchange, 'USDT', amount_usd * 2)
            self.rm.on_position_opened('funding', amount_usd)
            logger.info(f"✅ Funding позиция открыта: {coin}")

    # ──────────────────────────────────────────────────────────────
    # BASIS TRADING
    # ──────────────────────────────────────────────────────────────

    async def _execute_basis(self, signal: Signal):
        """Открывает basis позицию: спот + шорт фьючерс"""
        coin       = signal.coin
        exchange   = signal.exchange or 'bybit'
        amount_usd = signal.amount_usd
        spot_sym   = f"{coin}/USDT"
        fut_sym    = f"{coin}/USDT:USDT"

        ok, reason = self.rm.check_trade('basis', exchange, amount_usd)
        if not ok:
            logger.warning(f"⚠️  Basis отклонено: {reason}")
            return

        price       = await self.em.fetch_price_async(exchange, spot_sym)
        if not price:
            return

        coin_amount = round(amount_usd / price, 6)

        logger.info(
            f"📐 BASIS | {coin} | "
            f"спред: {signal.spread_pct:.3f}% | "
            f"{coin_amount} {coin}"
        )

        spot_order = await self.em.create_market_buy_async(
            exchange, spot_sym, coin_amount
        )

        if not DRY_RUN:
            try:
                ex_obj      = self.em.get(exchange, auth=True)
                short_order = await ex_obj.create_market_sell_order(
                    fut_sym, coin_amount,
                    params={"reduceOnly": False}
                )
            except Exception as e:
                logger.error(f"❌ BASIS SHORT ошибка: {e}")
                return
        else:
            logger.info(f"🧪 [DRY] BASIS SHORT {coin_amount} {fut_sym}")

        if spot_order:
            self.bm.reserve(exchange, 'USDT', amount_usd)
            self.rm.on_position_opened('basis', amount_usd)
            logger.info(f"✅ Basis позиция открыта: {coin}")

    # ──────────────────────────────────────────────────────────────
    # DEX-CEX АРБИТРАЖ
    # ──────────────────────────────────────────────────────────────

    async def _execute_dex_cex(self, signal: Signal):
        """
        DEX-CEX: покупаем на DEX, продаём на CEX (или наоборот).
        Детальная логика в DEXScanner.
        """
        logger.info(
            f"🔀 DEX-CEX | {signal.coin} | "
            f"спред: {signal.spread_pct:.3f}% | "
            f"${signal.amount_usd:.2f}"
        )

        ok, reason = self.rm.check_trade('dex_cex', signal.exchange or 'bybit', signal.amount_usd)
        if not ok:
            logger.warning(f"⚠️  DEX-CEX отклонено: {reason}")
            return

        # Основная логика DEX исполнения остаётся в DEXScanner
        # (там web3 транзакции, газ и т.д.)
        # Executor только проверяет риски и регистрирует позицию
        self.rm.on_position_opened('dex_cex', signal.amount_usd)
        logger.info(f"✅ DEX-CEX сигнал принят: {signal.coin}")

    # ──────────────────────────────────────────────────────────────
    # ФЬЮЧЕРСНЫЙ АРБИТРАЖ
    # ──────────────────────────────────────────────────────────────

    async def _execute_futures(self, signal: Signal):
        """Cash-and-Carry: спот + шорт срочного фьючерса"""
        coin       = signal.coin
        exchange   = signal.exchange or 'bybit'
        amount_usd = signal.amount_usd

        ok, reason = self.rm.check_trade('futures', exchange, amount_usd)
        if not ok:
            logger.warning(f"⚠️  Futures отклонено: {reason}")
            return

        logger.info(
            f"📈 FUTURES | {coin} | "
            f"годовых: {signal.spread_pct:.2f}% | "
            f"${amount_usd:.2f}"
        )

        self.rm.on_position_opened('futures', amount_usd)
        logger.info(f"✅ Futures позиция зарегистрирована: {coin}")