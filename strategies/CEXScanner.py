# ═══════════════════════════════════════════════════════════════════
#  strategies/CEXScanner.py
#
#  CEX-CEX арбитраж — ищет спреды между 5 биржами.
#  Поддерживает: Bybit, KuCoin, OKX, Gate.io, Bitget
#  Монеты: Tier1 (топ) + Tier2 (альты) + Tier3 (мемкоины)
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import asyncio
import itertools
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COINS, THRESHOLDS, INTERVALS, DRY_RUN
from logger import logger
from core.signal_queue import Signal


class CEXScanner:
    """
    Сканирует спреды между всеми парами бирж параллельно.
    При нахождении возможности кладёт Signal в очередь.
    """

    def __init__(self, exchange_manager, balance_manager, signal_queue):
        self.em      = exchange_manager
        self.bm      = balance_manager
        self.sq      = signal_queue
        self.min_spread = THRESHOLDS.get('cex_cex_min_spread_pct', 0.15)

    # ──────────────────────────────────────────────────────────────
    # ГЛАВНЫЙ ЦИКЛ (вызывается из main.py)
    # ──────────────────────────────────────────────────────────────

    async def run(self):
        """
        Бесконечный цикл сканирования.
        Запускается как asyncio задача в main.py.
        """
        logger.info(
            f"🔄 CEX Scanner запущен | "
            f"мин. спред: {self.min_spread}% | "
            f"интервал: {INTERVALS['cex_cex_scan']}с"
        )

        while True:
            try:
                await self._scan_cycle()
            except asyncio.CancelledError:
                logger.info("🔄 CEX Scanner остановлен")
                break
            except Exception as e:
                logger.error(f"❌ CEX Scanner ошибка: {e}")

            await asyncio.sleep(INTERVALS['cex_cex_scan'])

    # ──────────────────────────────────────────────────────────────
    # ОДИН ЦИКЛ СКАНИРОВАНИЯ
    # ──────────────────────────────────────────────────────────────

    async def _scan_cycle(self):
        """Сканирует все монеты по всем парам бирж"""
        exchanges  = self.em.get_connected()

        if len(exchanges) < 2:
            logger.warning("⚠️  CEX-CEX: нужно минимум 2 биржи")
            return

        # Сначала сканируем Tier1 (топ монеты — всегда)
        tier1_coins = COINS['cex_cex'].get('tier1', [])
        tier2_coins = COINS['cex_cex'].get('tier2', [])
        tier3_coins = COINS['cex_cex'].get('tier3', [])

        results = []

        # Tier 1 — всегда
        r1 = await self._scan_coins(tier1_coins, exchanges)
        results.extend(r1)

        # Tier 2 — если нет хороших сигналов в Tier1
        best_tier1 = max((r['net'] for r in r1), default=0)
        if best_tier1 < 1.0:  # меньше $1 прибыли — смотрим дальше
            r2 = await self._scan_coins(tier2_coins, exchanges)
            results.extend(r2)

        # Tier 3 — только если совсем нет сигналов
        if not results:
            r3 = await self._scan_coins(tier3_coins, exchanges)
            results.extend(r3)

        # Сортируем по чистой прибыли
        results.sort(key=lambda x: x['net'], reverse=True)

        # Лучшие возможности → в очередь
        signals_sent = 0
        for r in results[:3]:  # топ 3 возможности
            if r['net'] > 0:
                await self._send_signal(r)
                signals_sent += 1

        if signals_sent:
            logger.info(f"🔄 CEX-CEX: найдено {signals_sent} возможностей")
        elif results:
            logger.debug(
                f"🔄 CEX-CEX: лучший спред {results[0]['spread_pct']:.4f}% "
                f"— ниже порога {self.min_spread}%"
            )

    async def _scan_coins(self, coins: list, exchanges: list) -> list:
        """Сканирует список монет по всем парам бирж параллельно"""
        tasks = [
            self._check_coin(coin, exchanges)
            for coin in coins
        ]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for r in results_nested:
            if isinstance(r, Exception):
                continue
            if r:
                results.extend(r)
        return results

    async def _check_coin(self, coin: str, exchanges: list) -> list:
        """
        Проверяет одну монету на всех парах бирж.
        Возвращает список возможностей.
        """
        symbol = self.em.resolve_symbol(exchange_name, coin, 'spot')

        # Получаем цены на всех биржах параллельно
        # Для каждой биржи резолвим правильный символ
        async def fetch_for_exchange(ex_name):
            resolved = self.em.resolve_symbol(ex_name, coin, 'spot')
            if resolved is None:
                return ex_name, None
            price = await self.em.fetch_price_async(ex_name, resolved)
            return ex_name, price

        tasks   = [fetch_for_exchange(name) for name in exchanges]
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)
        prices  = {}
        for r in results_raw:
            if not isinstance(r, Exception) and r[1]:
                prices[r[0]] = r[1]

        if len(prices) < 2:
            return []

        opportunities = []

        # Проверяем все пары бирж
        for ex1, ex2 in itertools.combinations(prices.keys(), 2):
            price1 = prices[ex1]
            price2 = prices[ex2]

            if not price1 or not price2:
                continue

            # Определяем направление
            if price1 < price2:
                buy_ex, sell_ex     = ex1, ex2
                buy_price, sell_price = price1, price2
            else:
                buy_ex, sell_ex     = ex2, ex1
                buy_price, sell_price = price2, price1

            spread_usd = sell_price - buy_price
            spread_pct = (spread_usd / buy_price) * 100

            # Считаем комиссии
            total_fee_pct = self.em.get_total_fee(buy_ex, sell_ex, 'spot') * 100
            net_spread    = spread_pct - total_fee_pct

            # Считаем прибыль в $
            budget    = self.bm.get_strategy_budget('cex_cex')
            trade_usd = min(budget * 0.2, 500)  # не более 20% бюджета на сделку
            gross_usd = trade_usd * (spread_pct / 100)
            fees_usd  = trade_usd * (total_fee_pct / 100)
            net_usd   = gross_usd - fees_usd

            if spread_pct >= self.min_spread and net_usd > 0:
                opportunities.append({
                    'coin':        coin,
                    'symbol':      symbol,
                    'buy_ex':      buy_ex,
                    'sell_ex':     sell_ex,
                    'buy_price':   buy_price,
                    'sell_price':  sell_price,
                    'spread_pct':  spread_pct,
                    'net_spread':  net_spread,
                    'trade_usd':   trade_usd,
                    'gross':       gross_usd,
                    'fees':        fees_usd,
                    'net':         net_usd,
                })

                logger.info(
                    f"🔥 {coin:6s} | "
                    f"{buy_ex:8s}→{sell_ex:8s} | "
                    f"спред: {spread_pct:.4f}% | "
                    f"чистая: ${net_usd:.4f}"
                )

        return opportunities

    async def _send_signal(self, opportunity: dict):
        """Создаёт Signal и кладёт в очередь"""
        # Приоритет: чем больше прибыль, тем выше приоритет (меньше число)
        profit  = opportunity['net']
        priority = max(1, min(5, int(5 - profit)))

        signal = Signal(
            strategy             = 'cex_cex',
            coin                 = opportunity['coin'],
            action               = 'open',
            buy_exchange         = opportunity['buy_ex'],
            sell_exchange        = opportunity['sell_ex'],
            amount_usd           = opportunity['trade_usd'],
            spread_pct           = opportunity['spread_pct'],
            expected_profit_usd  = opportunity['net'],
            priority             = priority,
            expires_sec          = 15,  # спред живёт секунды
        )

        await self.sq.put(signal)

    # ──────────────────────────────────────────────────────────────
    # РУЧНОЙ ЗАПУСК (для тестов без main.py)
    # ──────────────────────────────────────────────────────────────

    async def scan_once(self) -> list:
        """
        Одиночное сканирование без цикла.
        Используй для тестов.

        Пример:
            results = await scanner.scan_once()
        """
        exchanges = self.em.get_connected()
        all_coins = (
            COINS['cex_cex'].get('tier1', []) +
            COINS['cex_cex'].get('tier2', [])
        )
        return await self._scan_coins(all_coins, exchanges)


# ─────────────────────────────────────────────────────────────────
# ТЕСТ
# ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import asyncio
    from exchanges.exchange_manager import ExchangeManager
    from core.balance_manager import BalanceManager
    from core.signal_queue import SignalQueue

    async def test():
        print("Тестируем CEX Scanner...\n")

        em = ExchangeManager(mode='async')
        bm = BalanceManager(em)
        sq = SignalQueue()

        await bm.update()

        scanner = CEXScanner(em, bm, sq)
        results = await scanner.scan_once()

        if results:
            print(f"\nНайдено возможностей: {len(results)}")
            for r in sorted(results, key=lambda x: x['net'], reverse=True)[:5]:
                print(
                    f"  {r['coin']:6s} | "
                    f"{r['buy_ex']:8s}→{r['sell_ex']:8s} | "
                    f"спред: {r['spread_pct']:.4f}% | "
                    f"чистая: ${r['net']:.4f}"
                )
        else:
            print("Возможностей не найдено")

        print(f"\nСигналов в очереди: {sq.size}")
        await em.close_all()

    asyncio.run(test())