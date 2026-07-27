# ═══════════════════════════════════════════════════════════════════
#  core/risk_manager.py
#
#  Глобальный риск-менеджер — защищает весь капитал бота.
#  Проверяет каждую сделку перед исполнением.
#  При превышении лимитов останавливает бота полностью.
#
#  Использование:
#      from core.risk_manager import RiskManager
#      rm = RiskManager(balance_manager)
#      ok, reason = rm.check_trade('cex_cex', 'bybit', 500)
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import json
from datetime import datetime, date
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import RISK, TOTAL_CAPITAL_USD, CAPITAL_ALLOCATION, PATHS, DRY_RUN
from logger import logger


class RiskManager:

    def __init__(self, balance_manager):
        self.bm                   = balance_manager
        self._bot_stopped         = False      # глобальный стоп бота
        self._stop_reason         = ''
        self._open_positions      = 0          # счётчик открытых позиций
        self._consecutive_losses  = 0          # убытков подряд
        self._pause_until         = None       # пауза после серии потерь
        self._daily_trades        = 0
        self._daily_profit        = 0.0
        self._stats_by_strategy   = {          # статистика по стратегиям
            s: {'trades': 0, 'profit': 0.0, 'losses': 0}
            for s in CAPITAL_ALLOCATION
        }

    # ──────────────────────────────────────────────────────────────
    # ГЛАВНАЯ ПРОВЕРКА — вызывается перед каждой сделкой
    # ──────────────────────────────────────────────────────────────

    def check_trade(
        self,
        strategy:      str,
        exchange_name: str,
        amount_usd:    float,
        coin:          str = 'USDT',
    ) -> tuple:
        """
        Проверяет можно ли открыть сделку.
        Возвращает (разрешено: bool, причина: str)

        Пример:
            ok, reason = rm.check_trade('cex_cex', 'bybit', 300)
            if not ok:
                logger.warning(reason)
                return
        """
        # 1. Глобальный стоп
        if self._bot_stopped:
            return False, f"🛑 Бот остановлен: {self._stop_reason}"

        # 2. Пауза после серии потерь
        if self._pause_until and datetime.now() < self._pause_until:
            remaining = (self._pause_until - datetime.now()).seconds // 60
            return False, f"⏸️  Пауза после потерь — ещё {remaining} мин"

        # 3. Лимит открытых позиций
        max_pos = RISK.get('max_open_positions', 10)
        if self._open_positions >= max_pos:
            return False, f"📊 Лимит позиций: {self._open_positions}/{max_pos}"

        # 4. Максимальный размер одной позиции
        max_pct    = RISK.get('max_position_pct', 0.10)
        max_amount = TOTAL_CAPITAL_USD * max_pct
        if amount_usd > max_amount:
            return False, (
                f"💰 Размер ${amount_usd:.2f} > макс ${max_amount:.2f} "
                f"({max_pct*100:.0f}% капитала)"
            )

        # 5. Дневной лимит потерь
        daily_loss_pct = self.bm.daily_pnl_pct()
        max_loss       = RISK.get('max_daily_loss_pct', 0.02) * 100
        if daily_loss_pct < -max_loss:
            self._stop_bot(f"Дневной убыток {daily_loss_pct:.2f}% > -{max_loss}%")
            return False, f"📉 Дневной лимит потерь достигнут: {daily_loss_pct:.2f}%"

        # 6. Проверка баланса через balance_manager
        ok, reason = self.bm.can_trade(exchange_name, coin, amount_usd)
        if not ok:
            return False, reason

        return True, "ok"

    def check_trade_cex_cex(
        self,
        buy_exchange:  str,
        sell_exchange: str,
        amount_usd:    float,
    ) -> tuple:
        """
        Специальная проверка для CEX-CEX арбитража.
        Нужны деньги на обеих биржах одновременно.
        """
        if self._bot_stopped:
            return False, f"🛑 Бот остановлен: {self._stop_reason}"

        ok, reason = self.check_trade('cex_cex', buy_exchange, amount_usd)
        if not ok:
            return False, reason

        ok, reason = self.bm.can_trade(sell_exchange, 'USDT', amount_usd)
        if not ok:
            return False, f"{sell_exchange}: {reason}"

        return True, "ok"

    # ──────────────────────────────────────────────────────────────
    # ОБНОВЛЕНИЕ ПОСЛЕ СДЕЛОК
    # ──────────────────────────────────────────────────────────────

    def on_position_opened(self, strategy: str, amount_usd: float):
        """
        Вызывай когда открываешь позицию.

        Пример:
            rm.on_position_opened('cex_cex', 300)
        """
        self._open_positions += 1
        self._daily_trades   += 1
        self._stats_by_strategy[strategy]['trades'] += 1
        logger.debug(
            f"📈 Позиция открыта | стратегия: {strategy} | "
            f"размер: ${amount_usd:.2f} | "
            f"всего позиций: {self._open_positions}"
        )

    def on_position_closed(self, strategy: str, profit: float):
        """
        Вызывай когда закрываешь позицию.

        Пример:
            rm.on_position_closed('cex_cex', 1.50)   # прибыль $1.50
            rm.on_position_closed('funding', -0.30)  # убыток $0.30
        """
        self._open_positions  = max(0, self._open_positions - 1)
        self._daily_profit   += profit

        if profit < 0:
            self._consecutive_losses += 1
            self._stats_by_strategy[strategy]['losses'] += 1
            logger.warning(
                f"📉 Убыток #{self._consecutive_losses}: "
                f"${profit:.4f} | стратегия: {strategy}"
            )
            self._check_consecutive_losses()
        else:
            self._consecutive_losses = 0

        self._stats_by_strategy[strategy]['profit'] += profit
        self._save_stats()

    def on_price_spike(self, coin: str, change_pct: float) -> bool:
        """
        Проверяет не слишком ли резко двинулась цена.
        Возвращает True если движение в норме.

        Пример:
            if not rm.on_price_spike('BTC', 6.5):
                # не входим в сделку
        """
        max_change = RISK.get('max_price_change_pct', 5.0)
        if abs(change_pct) > max_change:
            logger.warning(
                f"⚠️  {coin}: резкое движение {change_pct:.1f}% "
                f"> лимит {max_change}% — пропускаем"
            )
            return False
        return True

    # ──────────────────────────────────────────────────────────────
    # ОСТАНОВКА БОТА
    # ──────────────────────────────────────────────────────────────

    def _stop_bot(self, reason: str):
        """Останавливает всего бота"""
        self._bot_stopped = True
        self._stop_reason = reason
        logger.error(f"🛑 БОТ ОСТАНОВЛЕН: {reason}")

    def _check_consecutive_losses(self):
        """Проверяет не пора ли взять паузу после серии потерь"""
        max_losses   = RISK.get('max_consecutive_losses', 3)
        pause_sec    = RISK.get('loss_pause_seconds', 3600)

        if self._consecutive_losses >= max_losses:
            self._pause_until        = datetime.now()
            self._consecutive_losses = 0
            from datetime import timedelta
            self._pause_until = datetime.now() + timedelta(seconds=pause_sec)
            logger.warning(
                f"⏸️  {max_losses} убытков подряд — пауза "
                f"{pause_sec//60} минут до {self._pause_until.strftime('%H:%M')}"
            )

    def resume(self):
        """Ручное снятие паузы (через Telegram команду /resume)"""
        self._pause_until        = None
        self._consecutive_losses = 0
        logger.info("▶️  Пауза снята вручную")

    def emergency_stop(self, reason: str = "ручная остановка"):
        """Экстренная остановка через Telegram /stop"""
        self._stop_bot(reason)

    def restart(self):
        """Перезапуск после остановки через Telegram /start"""
        if not DRY_RUN:
            logger.warning("⚠️  Перезапуск в режиме реальной торговли!")
        self._bot_stopped        = False
        self._stop_reason        = ''
        self._consecutive_losses = 0
        self._pause_until        = None
        logger.info("▶️  Бот перезапущен")

    @property
    def is_running(self) -> bool:
        """True если бот работает и не на паузе"""
        if self._bot_stopped:
            return False
        if self._pause_until and datetime.now() < self._pause_until:
            return False
        return True

    @property
    def is_stopped(self) -> bool:
        return self._bot_stopped

    # ──────────────────────────────────────────────────────────────
    # ДНЕВНОЙ СБРОС
    # ──────────────────────────────────────────────────────────────

    def reset_daily(self):
        """Сбрасывает дневные счётчики. Вызывается в полночь."""
        self._daily_trades  = 0
        self._daily_profit  = 0.0
        self.bm.reset_daily()
        logger.info("📅 Дневные счётчики сброшены")

    # ──────────────────────────────────────────────────────────────
    # СТАТУС И СТАТИСТИКА
    # ──────────────────────────────────────────────────────────────

    def status(self):
        """Печатает статус риск-менеджера"""
        logger.info("═" * 55)
        logger.info("🛡️  РИСК-МЕНЕДЖЕР")
        logger.info(f"   Статус бота:      {'🟢 работает' if self.is_running else '🔴 остановлен'}")
        if self._stop_reason:
            logger.info(f"   Причина стопа:    {self._stop_reason}")
        if self._pause_until:
            logger.info(f"   Пауза до:         {self._pause_until.strftime('%H:%M:%S')}")
        logger.info(f"   Открытых позиций: {self._open_positions}")
        logger.info(f"   Убытков подряд:   {self._consecutive_losses}")
        logger.info(f"   Сделок сегодня:   {self._daily_trades}")
        logger.info(f"   Прибыль сегодня:  ${self._daily_profit:.4f}")

        daily_pnl_pct = self.bm.daily_pnl_pct()
        max_loss      = RISK.get('max_daily_loss_pct', 0.02) * 100
        logger.info(
            f"   Дневной P&L:      {daily_pnl_pct:+.3f}% "
            f"(лимит -{max_loss}%)"
        )
        logger.info("═" * 55)

    def summary(self) -> dict:
        """Для Telegram отчёта"""
        return {
            'is_running':          self.is_running,
            'is_stopped':          self._bot_stopped,
            'stop_reason':         self._stop_reason,
            'open_positions':      self._open_positions,
            'consecutive_losses':  self._consecutive_losses,
            'daily_trades':        self._daily_trades,
            'daily_profit':        self._daily_profit,
            'daily_pnl_pct':       self.bm.daily_pnl_pct(),
            'stats_by_strategy':   self._stats_by_strategy,
        }

    def _save_stats(self):
        """Сохраняет статистику в файл"""
        try:
            path = PATHS.get('stats_file', 'logs/stats.json')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {}
            if os.path.exists(path):
                with open(path, 'r') as f:
                    data = json.load(f)
            data['risk_stats'] = {
                'updated':            datetime.now().isoformat(),
                'daily_trades':       self._daily_trades,
                'daily_profit':       self._daily_profit,
                'stats_by_strategy':  self._stats_by_strategy,
            }
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения статистики: {e}")