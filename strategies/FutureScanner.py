# ═══════════════════════════════════════════════════════════════════
#  strategies/FutureScanner.py
#
#  Фьючерсный арбитраж (Cash-and-Carry).
#  Покупаем спот + шорт срочного фьючерса → держим до экспирации.
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import asyncio
import json
from datetime import datetime, timezone
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COINS, THRESHOLDS, INTERVALS, DRY_RUN
from logger import logger
from core.signal_queue import Signal

FUTURES_POSITIONS_FILE = 'logs/futures_positions.json'


class FuturesScanner:

    def __init__(self, exchange_manager, balance_manager, risk_manager, signal_queue):
        self.em          = exchange_manager
        self.bm          = balance_manager
        self.rm          = risk_manager
        self.sq          = signal_queue
        self.min_annual  = THRESHOLDS.get('futures_min_annual_pct', 8.0)
        self.exchanges   = ['bybit', 'okx']

    # ──────────────────────────────────────────────────────────────
    # ГЛАВНЫЕ ЦИКЛЫ
    # ──────────────────────────────────────────────────────────────

    async def run_scan(self):
        logger.info(
            f"📈 Futures Scanner запущен | "
            f"мин. годовых: {self.min_annual}% | "
            f"интервал: {INTERVALS['futures_scan']}с"
        )

        while True:
            try:
                await self._scan_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Futures scan ошибка: {e}")

            await asyncio.sleep(INTERVALS['futures_scan'])

    async def run_monitor(self):
        logger.info("📈 Futures Monitor запущен")

        while True:
            try:
                await self._monitor_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Futures monitor ошибка: {e}")

            await asyncio.sleep(INTERVALS['futures_monitor'])

    # ──────────────────────────────────────────────────────────────
    # СКАНИРОВАНИЕ
    # ──────────────────────────────────────────────────────────────

    async def _scan_cycle(self):
        results      = []
        coins        = COINS.get('futures', ['BTC', 'ETH', 'SOL', 'BNB', 'XRP'])
        positions    = self._load_positions()
        open_symbols = {p['symbol'] for p in positions}

        for exchange_name in self.exchanges:
            if not self.em.is_connected(exchange_name):
                continue

            try:
                ex_obj  = self.em.get(exchange_name, auth=False)
                markets = await ex_obj.load_markets()
            except Exception as e:
                logger.error(f"❌ Загрузка рынков {exchange_name}: {e}")
                continue

            for symbol, market in markets.items():
                if not (market.get('future') and market.get('expiry')):
                    continue
                if market.get('base') not in coins:
                    continue
                if symbol in open_symbols:
                    continue

                try:
                    days = self._days_to_expiry(market['expiry'])
                    if days < 7:
                        continue

                    coin        = market['base']
                    spot_price  = await self.em.fetch_price_async(exchange_name, f"{coin}/USDT")
                    fut_price   = await self.em.fetch_price_async(exchange_name, symbol)

                    if not spot_price or not fut_price:
                        continue

                    basis_pct = (fut_price - spot_price) / spot_price * 100
                    annual    = basis_pct * (365 / days)

                    if basis_pct <= 0 or annual < self.min_annual:
                        continue

                    budget    = self.bm.get_strategy_budget('futures')
                    trade_usd = min(budget * 0.5, 1000)

                    fee_pct   = (self.em.get_fee(exchange_name, 'spot') +
                                 self.em.get_fee(exchange_name, 'futures')) * 2
                    gross     = trade_usd * (basis_pct / 100)
                    fees      = trade_usd * fee_pct
                    net       = gross - fees

                    if net <= 0:
                        continue

                    results.append({
                        'coin':       coin,
                        'exchange':   exchange_name,
                        'symbol':     symbol,
                        'spot':       spot_price,
                        'futures':    fut_price,
                        'basis_pct':  basis_pct,
                        'days':       days,
                        'annual':     annual,
                        'trade_usd':  trade_usd,
                        'net':        net,
                    })

                    logger.info(
                        f"📈 {symbol:20s} | "
                        f"базис: {basis_pct:.3f}% | "
                        f"годовых: {annual:.2f}% | "
                        f"дней: {days:.0f}"
                    )

                except Exception as e:
                    logger.debug(f"Futures {symbol}: {e}")

        results.sort(key=lambda x: x['annual'], reverse=True)

        for r in results[:2]:
            await self._send_signal(r)
            self._save_position(r)

    def _days_to_expiry(self, expiry_ms: int) -> float:
        now  = datetime.now(timezone.utc).timestamp() * 1000
        days = (expiry_ms - now) / (1000 * 60 * 60 * 24)
        return max(days, 0.1)

    async def _send_signal(self, r: dict):
        signal = Signal(
            strategy            = 'futures',
            coin                = r['coin'],
            action              = 'open',
            exchange            = r['exchange'],
            amount_usd          = r['trade_usd'],
            spread_pct          = r['annual'],
            expected_profit_usd = r['net'],
            priority            = 8,
            expires_sec         = 7200,
        )
        await self.sq.put(signal)

    # ──────────────────────────────────────────────────────────────
    # МОНИТОРИНГ
    # ──────────────────────────────────────────────────────────────

    async def _monitor_cycle(self):
        positions = self._load_positions()
        if not positions:
            return

        logger.info(f"📈 Мониторинг {len(positions)} futures позиций")

        for position in positions:
            symbol   = position['symbol']
            coin     = position['coin']
            exchange = position.get('exchange', 'bybit')

            try:
                fut_price  = await self.em.fetch_price_async(exchange, symbol)
                spot_price = await self.em.fetch_price_async(exchange, f"{coin}/USDT")

                if not fut_price or not spot_price:
                    continue

                basis_now = (fut_price - spot_price) / spot_price * 100
                days_left = position.get('days', 30)

                logger.info(
                    f"  {symbol:20s} | "
                    f"базис: {basis_now:.3f}% | "
                    f"дней: {days_left:.0f}"
                )

                if basis_now <= 0.1:
                    logger.info(f"  {coin}: базис схлопнулся → закрываем")
                    await self._close_position(position, 'basis_collapsed')
                elif days_left <= 3:
                    logger.info(f"  {coin}: до экспирации {days_left:.0f} дн → закрываем")
                    await self._close_position(position, 'near_expiry')

            except Exception as e:
                logger.error(f"❌ Futures monitor {symbol}: {e}")

    async def _close_position(self, position: dict, reason: str):
        coin     = position['coin']
        exchange = position.get('exchange', 'bybit')
        symbol   = position['symbol']
        amount   = position.get('amount_coin', 0)

        logger.info(f"🔴 Закрываем futures {symbol} | {reason}")

        await self.em.create_market_sell_async(exchange, f"{coin}/USDT", amount)

        if not DRY_RUN:
            try:
                ex_obj = self.em.get(exchange, auth=True)
                await ex_obj.create_market_buy_order(
                    symbol, amount,
                    params={"reduceOnly": True}
                )
            except Exception as e:
                logger.error(f"❌ Закрытие futures шорта: {e}")
        else:
            logger.info(f"🧪 [DRY] Закрыть futures шорт {amount} {symbol}")

        gross  = position.get('trade_usd', 0) * (position.get('basis_pct', 0) / 100)
        fee    = position.get('trade_usd', 0) * 0.0012
        profit = gross - fee

        self.rm.on_position_closed('futures', profit)
        self.bm.release(exchange, 'USDT', position.get('trade_usd', 0))
        self._remove_position(symbol)

    # ──────────────────────────────────────────────────────────────
    # ХРАНЕНИЕ ПОЗИЦИЙ
    # ──────────────────────────────────────────────────────────────

    def _load_positions(self) -> list:
        if not os.path.exists(FUTURES_POSITIONS_FILE):
            return []
        with open(FUTURES_POSITIONS_FILE, 'r') as f:
            return json.load(f)

    def _save_position(self, r: dict):
        positions = self._load_positions()
        positions.append({
            'coin':       r['coin'],
            'exchange':   r['exchange'],
            'symbol':     r['symbol'],
            'spot':       r['spot'],
            'futures':    r['futures'],
            'basis_pct':  r['basis_pct'],
            'days':       r['days'],
            'annual':     r['annual'],
            'trade_usd':  r['trade_usd'],
            'amount_coin': round(r['trade_usd'] / r['spot'], 6),
            'opened_at':  datetime.now().isoformat(),
        })
        os.makedirs(os.path.dirname(FUTURES_POSITIONS_FILE), exist_ok=True)
        with open(FUTURES_POSITIONS_FILE, 'w') as f:
            json.dump(positions, f, indent=2)

    def _remove_position(self, symbol: str):
        positions = [p for p in self._load_positions() if p['symbol'] != symbol]
        with open(FUTURES_POSITIONS_FILE, 'w') as f:
            json.dump(positions, f, indent=2)