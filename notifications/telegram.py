# ═══════════════════════════════════════════════════════════════════
#  notifications/telegram.py
#
#  Telegram бот для управления и уведомлений.
#  Защита: только твой chat_id может отправлять команды.
#  Чужие сообщения игнорируются — бот даже не отвечает.
#
#  Команды:
#    /status   — статус бота и балансы
#    /stop     — остановить торговлю
#    /start    — возобновить торговлю
#    /resume   — снять паузу после серии потерь
#    /report   — отчёт за день
#    /help     — список команд
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import asyncio
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import TELEGRAM, INTERVALS
from logger import logger

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("⚠️  python-telegram-bot не установлен. Запусти: pip install python-telegram-bot")


class TelegramNotifier:
    """
    Отправляет уведомления и принимает команды.
    Whitelist: только TELEGRAM_CHAT_ID из .env имеет доступ.
    """

    def __init__(self, balance_manager=None, risk_manager=None):
        self.bm      = balance_manager
        self.rm      = risk_manager
        self.token   = TELEGRAM.get('bot_token', '')
        self.chat_id = int(TELEGRAM.get('chat_id', 0)) if TELEGRAM.get('chat_id') else 0
        self.enabled = TELEGRAM.get('enabled', False) and TELEGRAM_AVAILABLE
        self.app     = None

        if self.enabled and not self.token:
            logger.warning("⚠️  Telegram включён но токен не задан в .env")
            self.enabled = False

        if self.enabled and not self.chat_id:
            logger.warning("⚠️  Telegram включён но chat_id не задан в .env")
            self.enabled = False

    # ──────────────────────────────────────────────────────────────
    # ЗАПУСК
    # ──────────────────────────────────────────────────────────────

    async def start(self):
        """Запускает Telegram бота. Вызывается из main.py"""
        if not self.enabled:
            logger.info("📵 Telegram отключён")
            return

        try:
            self.app = Application.builder().token(self.token).build()

            # Регистрируем команды
            self.app.add_handler(CommandHandler("start",  self._cmd_start))
            self.app.add_handler(CommandHandler("stop",   self._cmd_stop))
            self.app.add_handler(CommandHandler("status", self._cmd_status))
            self.app.add_handler(CommandHandler("resume", self._cmd_resume))
            self.app.add_handler(CommandHandler("report", self._cmd_report))
            self.app.add_handler(CommandHandler("help",   self._cmd_help))

            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling(drop_pending_updates=True)

            logger.info(f"✅ Telegram бот запущен | chat_id: {self.chat_id}")
            await self.send("🤖 Бот запущен и готов к работе\n/help — список команд")

        except Exception as e:
            logger.error(f"❌ Telegram старт ошибка: {e}")
            self.enabled = False

    async def stop(self):
        """Останавливает Telegram бота"""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    # ──────────────────────────────────────────────────────────────
    # WHITELIST — главная защита
    # ──────────────────────────────────────────────────────────────

    def _is_authorized(self, update: "Update") -> bool:
        """
        Проверяет что сообщение пришло от тебя.
        Чужие сообщения тихо игнорируются.
        """
        user_chat_id = update.effective_chat.id
        if user_chat_id != self.chat_id:
            # Не отвечаем — чужой даже не узнает что бот существует
            logger.warning(f"⚠️  Telegram: неизвестный chat_id {user_chat_id} — игнорируем")
            return False
        return True

    # ──────────────────────────────────────────────────────────────
    # КОМАНДЫ
    # ──────────────────────────────────────────────────────────────

    async def _cmd_help(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return

        text = (
            "📋 *Команды бота*\n\n"
            "/status — балансы и открытые позиции\n"
            "/report — отчёт за день (P&L)\n"
            "/stop   — остановить торговлю\n"
            "/start  — возобновить торговлю\n"
            "/resume — снять паузу после потерь\n"
            "/help   — эта справка"
        )
        await update.message.reply_text(text, parse_mode='Markdown')

    async def _cmd_status(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return

        lines = ["📊 *Статус бота*\n"]

        if self.bm:
            summary = self.bm.summary()
            lines.append(f"💰 Всего USDT: *${summary['total_usdt']:,.2f}*")
            lines.append(f"📈 Дневной P&L: *{summary['daily_pnl_pct']:+.3f}%* (${summary['daily_pnl']:+.2f})")
            lines.append("\n*По биржам:*")
            for ex, amount in summary['by_exchange'].items():
                if amount > 0:
                    lines.append(f"  {ex}: ${amount:,.2f}")

        if self.rm:
            rm_sum = self.rm.summary()
            lines.append(f"\n🛡 Статус: {'🟢 работает' if rm_sum['is_running'] else '🔴 остановлен'}")
            lines.append(f"📊 Открытых позиций: {rm_sum['open_positions']}")
            lines.append(f"📝 Сделок сегодня: {rm_sum['daily_trades']}")

        lines.append(f"\n🕐 {datetime.now().strftime('%H:%M:%S')}")
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    async def _cmd_report(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return

        lines = [f"📋 *Отчёт за {datetime.now().strftime('%d.%m.%Y')}*\n"]

        if self.rm:
            rm_sum = self.rm.summary()
            lines.append(f"💰 Прибыль за день: *${rm_sum['daily_profit']:+.4f}*")
            lines.append(f"📊 Сделок: {rm_sum['daily_trades']}")
            lines.append("\n*По стратегиям:*")
            for strat, stats in rm_sum['stats_by_strategy'].items():
                if stats['trades'] > 0:
                    lines.append(
                        f"  {strat}: {stats['trades']} сделок | "
                        f"${stats['profit']:+.4f}"
                    )

        if self.bm:
            summary = self.bm.summary()
            lines.append(f"\n💹 Итого капитал: ${summary['total_usdt']:,.2f}")

        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    async def _cmd_stop(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return

        if self.rm:
            self.rm.emergency_stop("остановлен через Telegram")
            await update.message.reply_text("🛑 Торговля остановлена\nОткрытые позиции продолжают мониториться.")
        else:
            await update.message.reply_text("⚠️ Risk manager недоступен")

    async def _cmd_start(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return

        if self.rm:
            self.rm.restart()
            await update.message.reply_text("▶️ Торговля возобновлена")
        else:
            await update.message.reply_text("⚠️ Risk manager недоступен")

    async def _cmd_resume(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return

        if self.rm:
            self.rm.resume()
            await update.message.reply_text("▶️ Пауза снята")
        else:
            await update.message.reply_text("⚠️ Risk manager недоступен")

    # ──────────────────────────────────────────────────────────────
    # ОТПРАВКА УВЕДОМЛЕНИЙ (вызывается из стратегий)
    # ──────────────────────────────────────────────────────────────

    async def send(self, text: str):
        """Отправляет произвольное сообщение тебе"""
        if not self.enabled or not self.app:
            return
        try:
            await self.app.bot.send_message(
                chat_id    = self.chat_id,
                text       = text,
                parse_mode = 'Markdown',
            )
        except Exception as e:
            logger.error(f"❌ Telegram send ошибка: {e}")

    async def notify_trade_open(self, strategy: str, coin: str, amount_usd: float, details: str = ''):
        """Уведомление об открытии сделки"""
        if not TELEGRAM.get('notify_trade_open', True):
            return
        text = (
            f"📈 *Сделка открыта*\n"
            f"Стратегия: {strategy}\n"
            f"Монета: {coin}\n"
            f"Сумма: ${amount_usd:.2f}\n"
        )
        if details:
            text += details
        await self.send(text)

    async def notify_trade_close(self, strategy: str, coin: str, profit: float):
        """Уведомление о закрытии сделки"""
        if not TELEGRAM.get('notify_trade_close', True):
            return
        emoji = "✅" if profit >= 0 else "❌"
        text  = (
            f"{emoji} *Сделка закрыта*\n"
            f"Стратегия: {strategy}\n"
            f"Монета: {coin}\n"
            f"Прибыль: *${profit:+.4f}*"
        )
        await self.send(text)

    async def notify_error(self, error: str):
        """Уведомление об ошибке"""
        if not TELEGRAM.get('notify_error', True):
            return
        await self.send(f"⚠️ *Ошибка*\n{error}")

    async def notify_risk_alert(self, message: str):
        """Уведомление о срабатывании риск-лимита"""
        if not TELEGRAM.get('notify_risk_alert', True):
            return
        await self.send(f"🛑 *Риск-алерт*\n{message}")

    async def send_daily_report(self):
        """Ежедневный отчёт — вызывается из main.py в полночь"""
        if not TELEGRAM.get('notify_daily_report', True):
            return

        lines = [f"📋 *Ежедневный отчёт*\n{datetime.now().strftime('%d.%m.%Y')}\n"]

        if self.bm:
            summary = self.bm.summary()
            lines.append(f"💰 Капитал: ${summary['total_usdt']:,.2f}")
            lines.append(f"📈 P&L за день: *{summary['daily_pnl_pct']:+.3f}%* (${summary['daily_pnl']:+.2f})")

        if self.rm:
            rm_sum = self.rm.summary()
            lines.append(f"📊 Сделок: {rm_sum['daily_trades']}")
            lines.append(f"💵 Прибыль: ${rm_sum['daily_profit']:+.4f}")

        await self.send("\n".join(lines))

    # ──────────────────────────────────────────────────────────────
    # ЦИКЛ ЕЖЕДНЕВНОГО ОТЧЁТА
    # ──────────────────────────────────────────────────────────────

    async def run_daily_report(self):
        """Отправляет отчёт каждые 24 часа. Запускается из main.py"""
        while True:
            await asyncio.sleep(INTERVALS.get('telegram_report', 86400))
            try:
                await self.send_daily_report()
            except Exception as e:
                logger.error(f"❌ Ежедневный отчёт ошибка: {e}")