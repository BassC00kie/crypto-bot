# ═══════════════════════════════════════════════════════════════════
#  main.py
#
#  Главный файл бота. Запускает все стратегии параллельно через asyncio.
#  Каждая стратегия работает в своей корутине независимо.
#  Единый риск-менеджер контролирует весь капитал.
#
#  Запуск:  python main.py
#  Стоп:    Ctrl+C
# ═══════════════════════════════════════════════════════════════════

import asyncio
import signal
from datetime import datetime, time as dtime
from logger import logger

# Ядро
from exchanges.exchange_manager import ExchangeManager
from core.balance_manager       import BalanceManager
from core.risk_manager          import RiskManager
from core.signal_queue          import SignalQueue
from core.executor              import Executor

# Стратегии
from strategies.CEXScanner    import CEXScanner
from strategies.DEXScanner    import DEXScanner
from strategies.BotFunding    import FundingScanner
from strategies.BasisScanner  import BasisScanner
from strategies.FutureScanner import FuturesScanner

# Уведомления
from notifications.telegram import TelegramNotifier

# Конфиг
from config import (
    DRY_RUN, TOTAL_CAPITAL_USD, INTERVALS,
    CAPITAL_ALLOCATION, validate_config
)


# ═══════════════════════════════════════════════════════════════════
#  ИНИЦИАЛИЗАЦИЯ
# ═══════════════════════════════════════════════════════════════════

async def init_bot():
    """Создаёт и возвращает все компоненты бота"""

    logger.info("═" * 60)
    logger.info("🚀 CRYPTO ARBITRAGE BOT v2.0")
    logger.info(f"   Время старта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   Режим:        {'🧪 DRY RUN (симуляция)' if DRY_RUN else '🔴 РЕАЛЬНАЯ ТОРГОВЛЯ'}")
    logger.info(f"   Капитал:      ${TOTAL_CAPITAL_USD:,}")
    logger.info("═" * 60)

    # Валидация конфига
    if not validate_config():
        raise SystemExit("❌ Ошибки в конфиге — исправь и перезапусти")

    # ── Ядро ──────────────────────────────────────────────────────
    em = ExchangeManager(mode='async')
    bm = BalanceManager(em)
    rm = RiskManager(bm)
    sq = SignalQueue(maxsize=200)
    ex = Executor(em, bm, rm, sq)

    # Загружаем рынки один раз — чтобы не грузить при каждом запросе цены
    logger.info("📚 Загружаю рынки бирж...")
    await bm.preload_markets()

    # Первоначальная загрузка балансов
    await bm.update()
    bm.status()
    rm.status()

    # ── Стратегии ─────────────────────────────────────────────────
    cex_scanner     = CEXScanner(em, bm, sq)
    dex_scanner     = DEXScanner(em, bm, rm, sq)
    funding_scanner = FundingScanner(em, bm, rm, sq)
    basis_scanner   = BasisScanner(em, bm, rm, sq)
    futures_scanner = FuturesScanner(em, bm, rm, sq)

    # ── Telegram ──────────────────────────────────────────────────
    tg = TelegramNotifier(bm, rm)

    return {
        'em': em, 'bm': bm, 'rm': rm, 'sq': sq, 'ex': ex,
        'cex':     cex_scanner,
        'dex':     dex_scanner,
        'funding': funding_scanner,
        'basis':   basis_scanner,
        'futures': futures_scanner,
        'tg':      tg,
    }


# ═══════════════════════════════════════════════════════════════════
#  СИСТЕМНЫЕ ЦИКЛЫ
# ═══════════════════════════════════════════════════════════════════

async def balance_loop(bm: BalanceManager):
    """Обновляет балансы каждые N секунд"""
    while True:
        try:
            await bm.update()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ Balance loop: {e}")
        await asyncio.sleep(INTERVALS['balance_update'])


async def risk_loop(rm: RiskManager, bm: BalanceManager, tg: TelegramNotifier):
    """Проверяет риск-лимиты каждые N секунд"""
    while True:
        try:
            daily_pnl = bm.daily_pnl_pct()
            max_loss  = -2.0  # из config RISK

            if daily_pnl < max_loss and not rm.is_stopped:
                msg = f"Дневной убыток {daily_pnl:.2f}% > лимит {max_loss}%"
                rm.emergency_stop(msg)
                await tg.notify_risk_alert(msg)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ Risk loop: {e}")
        await asyncio.sleep(INTERVALS['risk_check'])


async def midnight_reset_loop(rm: RiskManager, bm: BalanceManager, tg: TelegramNotifier):
    """В полночь сбрасывает дневные счётчики и отправляет отчёт"""
    while True:
        try:
            now  = datetime.now()
            # Следующая полночь
            secs = ((24 - now.hour) * 3600) - (now.minute * 60) - now.second
            await asyncio.sleep(secs)

            # Отправляем отчёт за день
            await tg.send_daily_report()

            # Сбрасываем счётчики
            rm.reset_daily()
            bm.reset_daily()

            logger.info("📅 Полночь — дневные счётчики сброшены")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ Midnight reset: {e}")


async def status_loop(bm: BalanceManager, rm: RiskManager, sq: SignalQueue):
    """Каждые 30 минут печатает статус в логи"""
    while True:
        try:
            await asyncio.sleep(1800)
            bm.status()
            rm.status()
            sq.status()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ Status loop: {e}")


# ═══════════════════════════════════════════════════════════════════
#  ГЛАВНАЯ ФУНКЦИЯ
# ═══════════════════════════════════════════════════════════════════

async def main():
    # Инициализация
    components = await init_bot()
    em  = components['em']
    bm  = components['bm']
    rm  = components['rm']
    sq  = components['sq']
    ex  = components['ex']
    tg  = components['tg']
    cex = components['cex']
    dex = components['dex']
    funding = components['funding']
    basis   = components['basis']
    futures = components['futures']

    # Запускаем Telegram
    await tg.start()

    # Подключаем Telegram к DEX Scanner
    dex.set_telegram(tg)

    logger.info("\n🎯 Запускаю все стратегии параллельно...\n")

    # ── Все задачи ────────────────────────────────────────────────
    tasks = [

        # Ядро
        asyncio.create_task(ex.run(),               name="executor"),
        asyncio.create_task(balance_loop(bm),        name="balance"),
        asyncio.create_task(risk_loop(rm, bm, tg),   name="risk"),
        asyncio.create_task(midnight_reset_loop(rm, bm, tg), name="midnight"),
        asyncio.create_task(status_loop(bm, rm, sq), name="status"),

        # CEX-CEX — каждые 10 сек
        asyncio.create_task(cex.run(),               name="cex_cex"),

        # DEX-CEX — каждые 15 сек + мониторинг газа
        asyncio.create_task(dex.run(),               name="dex_cex"),
        asyncio.create_task(dex.run_gas_monitor(),   name="dex_gas"),

        # Фандинг — сканирование каждые 5 мин, мониторинг каждую минуту
        asyncio.create_task(funding.run_scan(),      name="funding_scan"),
        asyncio.create_task(funding.run_monitor(),   name="funding_monitor"),

        # Basis — сканирование каждые 5 мин, мониторинг каждые 2 мин
        asyncio.create_task(basis.run_scan(),        name="basis_scan"),
        asyncio.create_task(basis.run_monitor(),     name="basis_monitor"),

        # Futures — сканирование каждый час, мониторинг каждые 30 мин
        asyncio.create_task(futures.run_scan(),      name="futures_scan"),
        asyncio.create_task(futures.run_monitor(),   name="futures_monitor"),
    ]

    logger.info(f"✅ Запущено задач: {len(tasks)}")
    logger.info("   Нажми Ctrl+C для остановки\n")

    # Обработка Ctrl+C
    stop_event = asyncio.Event()

    def _handle_stop():
        logger.info("\n🛑 Получен сигнал остановки...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_stop)
        except NotImplementedError:
            pass  # Windows не поддерживает add_signal_handler

    # Ждём сигнала остановки
    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass

    # ── Graceful shutdown ─────────────────────────────────────────
    logger.info("🔄 Останавливаю все задачи...")

    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    # Финальный отчёт
    await tg.send("🛑 Бот остановлен\n" + f"Время работы до {datetime.now().strftime('%H:%M:%S')}")
    await tg.stop()
    await em.close_all()

    bm.status()
    rm.status()

    logger.info("✅ Бот остановлен корректно")


# ═══════════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен вручную")
    except Exception as e:
        logger.error(f"💀 Критическая ошибка: {e}")
        raise