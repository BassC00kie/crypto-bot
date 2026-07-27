# ═══════════════════════════════════════════════════════════════════
#  strategies/BasisScanner.py
#
#  Basis Trading — зарабатываем на аномальном расхождении
#  между спотом и перпетуальным фьючерсом.
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import asyncio
import json
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COINS, THRESHOLDS, INTERVALS, DRY_RUN
from logger import logger
from core.signal_queue import Signal

BASIS_POSITIONS_FILE = 'logs/basis_positions.json'


class BasisScanner:

    def __init__(self, exchange_manager, balance_manager, risk_manager, signal_queue):
        self.em            = exchange_manager
        self.bm            = balance_manager
        self.rm            = risk_manager
        self.sq            = signal_queue
        self.anomaly_pct   = THRESHOLDS.get('basis_min_deviation_pct', 0.30)
        self.stop_loss_pct = 2.0
        self.take_profit   = 0.3

        # Только биржи с фьючерсами
        self.futures_exchanges = ['bybit', 'okx', 'bitget']

    # ──────────────────────────────────────────────────────────────
    # ГЛАВНЫЕ ЦИКЛЫ
    # ──────────────────────────────────────────────────────────────

    async def run_scan(self):
        logger.info(
            f"📐 Basis Scanner запущен | "
            f"порог: {self.anomaly_pct}% | "
            f"интервал: {INTERVALS['basis_scan']}с"
        )

        while True:
            try:
                await self._scan_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Basis scan ошибка: {e}")

            await asyncio.sleep(INTERVALS['basis_scan'])

    async def run_monitor(self):
        logger.info("📐 Basis Monitor запущен")

        while True:
            try:
                await self._monitor_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Basis monitor ошибка: {e}")

            await asyncio.sleep(INTERVALS['basis_monitor'])

    # ──────────────────────────────────────────────────────────────
    # СКАНИРОВАНИЕ
    # ──────────────────────────────────────────────────────────────

    async def _scan_cycle(self):
        results = []
        coins   = COINS.get('basis', ['BTC', 'ETH', 'SOL', 'BNB', 'XRP'])

        for exchange_name in self.futures_exchanges:
            if not self.em.is_connected(exchange_name):
                continue

            tasks = [self._check_basis(exchange_name, coin) for coin in coins]
            res   = await asyncio.gather(*tasks, return_exceptions=True)
            for r in res:
                if r and not isinstance(r, Exception):
                    results.append(r)

        results.sort(key=lambda x: x['basis_pct'], reverse=True)

        positions    = self._load_positions()
        open_keys    = {f"{p['coin']}_{p.get('exchange','bybit')}" for p in positions}

        for r in results:
            key = f"{r['coin']}_{r['exchange']}"
            if key not in open_keys:
                logger.info(
                    f"🔥 Basis: {r['coin']:6s} | {r['exchange']:8s} | "
                    f"базис: {r['basis_pct']:.3f}%"
                )
                await self._send_signal(r)

    async def _check_basis(self, exchange_name: str, coin: str) -> dict | None:
        spot_sym = self.em.resolve_symbol(exchange_name, coin, 'spot')
        fut_sym  = self.em.resolve_symbol(exchange_name, coin, 'futures')

        try:
            spot_price, fut_price = await asyncio.gather(
                self.em.fetch_price_async(exchange_name, spot_sym),
                self.em.fetch_price_async(exchange_name, fut_sym),
            )

            if fut_sym is None or not spot_price or not fut_price:
                return None

            basis_usd = fut_price - spot_price
            basis_pct = (basis_usd / spot_price) * 100

            if basis_pct < self.anomaly_pct:
                return None

            budget    = self.bm.get_strategy_budget('basis')
            trade_usd = min(budget * 0.4, 800)

            return {
                'coin':       coin,
                'exchange':   exchange_name,
                'spot':       spot_price,
                'futures':    fut_price,
                'basis_usd':  basis_usd,
                'basis_pct':  basis_pct,
                'trade_usd':  trade_usd,
            }

        except Exception as e:
            logger.debug(f"Basis {exchange_name} {coin}: {e}")
            return None

    async def _send_signal(self, r: dict):
        signal = Signal(
            strategy            = 'basis',
            coin                = r['coin'],
            action              = 'open',
            exchange            = r['exchange'],
            amount_usd          = r['trade_usd'],
            spread_pct          = r['basis_pct'],
            expected_profit_usd = r['trade_usd'] * (r['basis_pct'] / 100),
            priority            = 6,
            expires_sec         = 900,
        )
        await self.sq.put(signal)
        self._save_position(r)

    # ──────────────────────────────────────────────────────────────
    # МОНИТОРИНГ
    # ──────────────────────────────────────────────────────────────

    async def _monitor_cycle(self):
        positions = self._load_positions()
        if not positions:
            return

        logger.info(f"📐 Мониторинг {len(positions)} basis позиций")

        for position in positions:
            coin     = position['coin']
            exchange = position.get('exchange', 'bybit')

            try:
                data = await self._check_basis(exchange, coin)
                if not data:
                    continue

                basis_now = data['basis_pct']
                trailing  = position.get('trailing_stop', self.stop_loss_pct)
                take      = self.take_profit

                # Обновляем трейлинг стоп
                if basis_now < trailing - 0.3:
                    position['trailing_stop'] = basis_now + 0.3
                    logger.info(f"  {coin}: трейлинг → {position['trailing_stop']:.3f}%")

                if basis_now <= take:
                    logger.info(f"  {coin}: тейк-профит! базис {basis_now:.3f}%")
                    await self._close_position(position, 'take_profit')
                elif basis_now >= trailing:
                    logger.info(f"  {coin}: стоп-лосс! базис {basis_now:.3f}%")
                    await self._close_position(position, 'stop_loss')
                else:
                    logger.info(
                        f"  {coin:6s} | базис: {basis_now:.3f}% | "
                        f"вход: {position['entry_basis_pct']:.3f}% | держим"
                    )

            except Exception as e:
                logger.error(f"❌ Basis monitor {coin}: {e}")

        self._save_all_positions(positions)

    async def _close_position(self, position: dict, reason: str):
        coin     = position['coin']
        exchange = position.get('exchange', 'bybit')
        amount   = position.get('amount_coin', 0)

        logger.info(f"🔴 Закрываем basis {coin} | {reason}")

        await self.em.create_market_sell_async(exchange, f"{coin}/USDT", amount)

        if not DRY_RUN:
            try:
                ex_obj = self.em.get(exchange, auth=True)
                await ex_obj.create_market_buy_order(
                    f"{coin}/USDT:USDT", amount,
                    params={"reduceOnly": True}
                )
            except Exception as e:
                logger.error(f"❌ Закрытие basis шорта {coin}: {e}")
        else:
            logger.info(f"🧪 [DRY] Закрыть basis шорт {amount} {coin}")

        entry  = position.get('entry_basis_pct', 0) / 100
        profit = position.get('trade_usd', 0) * entry
        self.rm.on_position_closed('basis', profit)
        self.bm.release(exchange, 'USDT', position.get('trade_usd', 0))
        self._remove_position(coin, exchange)

    # ──────────────────────────────────────────────────────────────
    # ХРАНЕНИЕ ПОЗИЦИЙ
    # ──────────────────────────────────────────────────────────────

    def _load_positions(self) -> list:
        if not os.path.exists(BASIS_POSITIONS_FILE):
            return []
        with open(BASIS_POSITIONS_FILE, 'r') as f:
            return json.load(f)

    def _save_all_positions(self, positions: list):
        os.makedirs(os.path.dirname(BASIS_POSITIONS_FILE), exist_ok=True)
        with open(BASIS_POSITIONS_FILE, 'w') as f:
            json.dump(positions, f, indent=2)

    def _save_position(self, r: dict):
        positions = self._load_positions()
        positions.append({
            'coin':            r['coin'],
            'exchange':        r['exchange'],
            'entry_basis_pct': r['basis_pct'],
            'trailing_stop':   r['basis_pct'] + (self.stop_loss_pct - self.anomaly_pct),
            'trade_usd':       r['trade_usd'],
            'amount_coin':     round(r['trade_usd'] / r['spot'], 6),
            'opened_at':       datetime.now().isoformat(),
        })
        self._save_all_positions(positions)

    def _remove_position(self, coin: str, exchange: str):
        positions = self._load_positions()
        positions = [
            p for p in positions
            if not (p['coin'] == coin and p.get('exchange') == exchange)
        ]
        self._save_all_positions(positions)