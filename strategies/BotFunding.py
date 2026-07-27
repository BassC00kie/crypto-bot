# ═══════════════════════════════════════════════════════════════════
#  strategies/BotFunding.py
#
#  Фандинг арбитраж — зарабатываем на ставке финансирования.
#  Стратегия: купить спот + шорт фьючерс = дельта-нейтральная позиция.
#  Получаем фандинг каждые 8 часов пока ставка положительная.
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COINS, THRESHOLDS, INTERVALS, DRY_RUN
from logger import logger
from core.signal_queue import Signal
from positions import load_positions, add_position, remove_position, save_positions, log_trade


class FundingScanner:
    """
    Сканирует ставки фандинга на всех биржах.
    Мониторит открытые позиции.
    Закрывает когда ставка падает ниже порога.
    """

    def __init__(self, exchange_manager, balance_manager, risk_manager, signal_queue):
        self.em          = exchange_manager
        self.bm          = balance_manager
        self.rm          = risk_manager
        self.sq          = signal_queue
        self.min_daily   = THRESHOLDS.get('funding_min_daily_pct', 0.03)

        # Биржи поддерживающие перпетуальные фьючерсы
        self.funding_exchanges = ['bybit', 'okx', 'gate', 'bitget']

    # ──────────────────────────────────────────────────────────────
    # ГЛАВНЫЕ ЦИКЛЫ
    # ──────────────────────────────────────────────────────────────

    async def run_scan(self):
        """Цикл сканирования новых возможностей"""
        logger.info(
            f"💰 Funding Scanner запущен | "
            f"мин. дневная: {self.min_daily}% | "
            f"интервал: {INTERVALS['funding_scan']}с"
        )

        while True:
            try:
                await self._scan_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Funding scan ошибка: {e}")

            await asyncio.sleep(INTERVALS['funding_scan'])

    async def run_monitor(self):
        """Цикл мониторинга открытых позиций"""
        logger.info("💰 Funding Monitor запущен")

        while True:
            try:
                await self._monitor_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Funding monitor ошибка: {e}")

            await asyncio.sleep(INTERVALS['funding_monitor'])

    # ──────────────────────────────────────────────────────────────
    # СКАНИРОВАНИЕ
    # ──────────────────────────────────────────────────────────────

    async def _scan_cycle(self):
        """Сканирует все монеты на всех биржах"""
        results = []

        # Монеты по тирам
        all_coins = (
            COINS['funding'].get('tier1', []) +
            COINS['funding'].get('tier2', []) +
            COINS['funding'].get('tier3', [])
        )

        for exchange_name in self.funding_exchanges:
            if not self.em.is_connected(exchange_name):
                continue

            tasks = [
                self._check_funding(exchange_name, coin)
                for coin in all_coins
            ]
            exchange_results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in exchange_results:
                if r and not isinstance(r, Exception):
                    results.append(r)

        # Сортируем по дневной доходности
        results.sort(key=lambda x: x['daily_pct'], reverse=True)

        # Фильтруем уже открытые позиции
        open_positions = load_positions()
        open_coins     = {f"{p['coin']}_{p.get('exchange', 'bybit')}" for p in open_positions}

        for r in results:
            key = f"{r['coin']}_{r['exchange']}"
            if key not in open_coins:
                await self._send_signal(r)
                logger.info(
                    f"💰 Funding: {r['coin']:6s} | "
                    f"{r['exchange']:8s} | "
                    f"ставка: {r['rate_pct']:.4f}% | "
                    f"день: {r['daily_pct']:.4f}%"
                )

    async def _check_funding(self, exchange_name: str, coin: str) -> dict | None:
        """Проверяет фандинг одной монеты на одной бирже"""
        symbol = self.em.resolve_symbol(exchange_name, coin, 'futures')

        # Проверяем что символ реально есть на бирже
        if symbol is None:
            return None

        try:
            rate = await self.em.fetch_funding_rate_async(exchange_name, symbol)
            if rate is None or rate <= 0:
                return None

            rate_pct  = rate * 100
            daily_pct = rate_pct * 3     # 3 выплаты в день

            if daily_pct < self.min_daily:
                return None

            # Считаем прибыль
            budget     = self.bm.get_strategy_budget('funding')
            trade_usd  = min(budget * 0.3, 1000)  # не более 30% бюджета
            fee_open   = self.em.get_fee(exchange_name, 'spot')
            fee_close  = self.em.get_fee(exchange_name, 'futures')
            fees_total = trade_usd * (fee_open + fee_close) * 2

            # Прибыль за 7 дней
            gross_7d = trade_usd * (daily_pct / 100) * 7
            net_7d   = gross_7d - fees_total

            if net_7d <= 0:
                return None

            return {
                'coin':       coin,
                'exchange':   exchange_name,
                'symbol':     symbol,
                'rate':       rate,
                'rate_pct':   rate_pct,
                'daily_pct':  daily_pct,
                'trade_usd':  trade_usd,
                'gross_7d':   gross_7d,
                'fees':       fees_total,
                'net_7d':     net_7d,
            }

        except Exception as e:
            logger.debug(f"Funding {exchange_name} {coin}: {e}")
            return None

    async def _send_signal(self, opportunity: dict):
        """Отправляет сигнал в очередь"""
        signal = Signal(
            strategy            = 'funding',
            coin                = opportunity['coin'],
            action              = 'open',
            exchange            = opportunity['exchange'],
            amount_usd          = opportunity['trade_usd'],
            spread_pct          = opportunity['daily_pct'],
            expected_profit_usd = opportunity['net_7d'],
            priority            = 7,         # фандинг — низкий приоритет (долгосрочный)
            expires_sec         = 3600,      # сигнал актуален 1 час
        )
        await self.sq.put(signal)

    # ──────────────────────────────────────────────────────────────
    # МОНИТОРИНГ ОТКРЫТЫХ ПОЗИЦИЙ
    # ──────────────────────────────────────────────────────────────

    async def _monitor_cycle(self):
        """Проверяет все открытые фандинг позиции"""
        positions = load_positions()
        if not positions:
            return

        logger.info(f"💰 Мониторинг {len(positions)} funding позиций")
        min_hold = THRESHOLDS.get('funding_min_daily_pct', 0.005)

        for position in positions:
            coin     = position['coin']
            exchange = position.get('exchange', 'bybit')
            symbol   = self.em.resolve_symbol(exchange_name, coin, 'futures')

            try:
                # Проверяем движение цены
                spot_price  = await self.em.fetch_price_async(exchange, f"{coin}/USDT")
                entry_price = position.get('entry_price', spot_price)

                if spot_price and entry_price:
                    change_pct = abs(spot_price - entry_price) / entry_price * 100
                    if not self.rm.on_price_spike(coin, change_pct):
                        await self._close_position(position, 'price_spike')
                        continue

                # Проверяем текущий фандинг
                rate = await self.em.fetch_funding_rate_async(exchange, symbol)
                if rate is None:
                    continue

                daily_pct = rate * 3 * 100

                # Обновляем накопленный фандинг
                funding_earned              = position.get('trade_usd', 0) * rate
                position['funding_collected'] = position.get('funding_collected', 0) + funding_earned

                logger.info(
                    f"  {coin:6s} | {exchange:8s} | "
                    f"день: {daily_pct:.4f}% | "
                    f"собрано: ${position['funding_collected']:.4f}"
                )

                # Закрываем если фандинг упал ниже порога
                if daily_pct < min_hold:
                    logger.info(f"  {coin}: фандинг {daily_pct:.4f}% < {min_hold}% → закрываем")
                    await self._close_position(position, 'low_funding')

            except Exception as e:
                logger.error(f"❌ Мониторинг {coin}: {e}")

        save_positions(positions)

    async def _close_position(self, position: dict, reason: str):
        """Закрывает фандинг позицию"""
        coin       = position['coin']
        exchange   = position.get('exchange', 'bybit')
        amount     = position.get('amount', 0)
        spot_sym   = f"{coin}/USDT"
        fut_sym    = self.em.resolve_symbol(exchange_name, coin, 'futures')

        logger.info(f"🔴 Закрываем funding {coin} | причина: {reason}")

        # Закрываем спот
        await self.em.create_market_sell_async(exchange, spot_sym, amount)

        # Закрываем шорт
        if not DRY_RUN:
            try:
                ex_obj = self.em.get(exchange, auth=True)
                await ex_obj.create_market_buy_order(
                    fut_sym, amount,
                    params={"reduceOnly": True}
                )
            except Exception as e:
                logger.error(f"❌ Закрытие шорта {coin}: {e}")
        else:
            logger.info(f"🧪 [DRY] Закрыть шорт {amount} {fut_sym}")

        # Считаем прибыль
        fee_pct  = (self.em.get_fee(exchange, 'spot') + self.em.get_fee(exchange, 'futures')) * 2
        fees     = position.get('trade_usd', 0) * fee_pct
        profit   = position.get('funding_collected', 0) - fees

        logger.info(f"  Собрано фандинга: ${position.get('funding_collected', 0):.4f}")
        logger.info(f"  Комиссии:        -${fees:.4f}")
        logger.info(f"  Итог:             ${profit:.4f}")

        # Освобождаем баланс
        self.bm.release(exchange, 'USDT', position.get('trade_usd', 0) * 2)
        self.rm.on_position_closed('funding', profit)

        remove_position(coin)
        log_trade(position, profit)


# ─────────────────────────────────────────────────────────────────
# ТЕСТ
# ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    from exchanges.exchange_manager import ExchangeManager
    from core.balance_manager import BalanceManager
    from core.risk_manager import RiskManager
    from core.signal_queue import SignalQueue

    async def test():
        print("Тестируем Funding Scanner...\n")

        em = ExchangeManager(mode='async')
        bm = BalanceManager(em)
        rm = RiskManager(bm)
        sq = SignalQueue()

        await bm.update()

        scanner = FundingScanner(em, bm, rm, sq)
        await scanner._scan_cycle()

        print(f"\nСигналов в очереди: {sq.size}")
        await em.close_all()

    asyncio.run(test())