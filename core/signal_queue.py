# ═══════════════════════════════════════════════════════════════════
#  core/signal_queue.py
#
#  Очередь сигналов от всех стратегий.
#  Стратегии кладут сигналы сюда → executor забирает и исполняет.
#  Это разделяет сканирование и исполнение — чисто и безопасно.
#
#  Использование:
#      from core.signal_queue import SignalQueue, Signal
#      sq = SignalQueue()
#      await sq.put(Signal('cex_cex', 'BTC', ...))
#      signal = await sq.get()
# ═══════════════════════════════════════════════════════════════════

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from logger import logger


@dataclass
class Signal:
    """
    Торговый сигнал от стратегии.
    Содержит всё что нужно executor'у для исполнения.
    """
    strategy:     str             # 'cex_cex' | 'funding' | 'basis' | 'dex_cex' | 'futures'
    coin:         str             # 'BTC'
    action:       str             # 'open' | 'close'

    # CEX-CEX
    buy_exchange:  Optional[str]  = None    # биржа где покупаем
    sell_exchange: Optional[str]  = None    # биржа где продаём
    amount_usd:    float          = 0.0

    # Для фандинга и basis
    exchange:      Optional[str]  = None
    amount_coin:   float          = 0.0

    # Метрики сигнала
    spread_pct:    float          = 0.0
    expected_profit_usd: float    = 0.0
    priority:      int            = 5       # 1=высший, 10=низший

    # Служебное
    created_at:    datetime       = field(default_factory=datetime.now)
    expires_sec:   int            = 30      # сигнал устаревает через N секунд

    def is_expired(self) -> bool:
        age = (datetime.now() - self.created_at).total_seconds()
        return age > self.expires_sec

    def __lt__(self, other):
        """Для сортировки по приоритету в PriorityQueue"""
        return self.priority < other.priority

    def __repr__(self):
        return (
            f"Signal({self.strategy} | {self.coin} | {self.action} | "
            f"спред: {self.spread_pct:.3f}% | "
            f"прибыль: ${self.expected_profit_usd:.4f})"
        )


class SignalQueue:
    """
    Асинхронная очередь сигналов с приоритетами.
    Сигналы с меньшим priority исполняются первыми.
    """

    def __init__(self, maxsize: int = 100):
        self._queue    = asyncio.PriorityQueue(maxsize=maxsize)
        self._counter  = 0          # для стабильной сортировки
        self._stats    = {
            'total_received': 0,
            'total_expired':  0,
            'total_executed': 0,
            'by_strategy':    {},
        }

    async def put(self, signal: Signal):
        """
        Кладёт сигнал в очередь.
        Вызывается из стратегий когда найдена возможность.

        Пример:
            await sq.put(Signal(
                strategy='cex_cex',
                coin='BTC',
                action='open',
                buy_exchange='bybit',
                sell_exchange='kucoin',
                amount_usd=300,
                spread_pct=0.25,
                expected_profit_usd=0.45,
                priority=2,
            ))
        """
        if self._queue.full():
            logger.warning("⚠️  SignalQueue переполнена — старый сигнал удалён")
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

        # PriorityQueue сортирует по первому элементу кортежа
        self._counter += 1
        await self._queue.put((signal.priority, self._counter, signal))

        self._stats['total_received'] += 1
        strat = signal.strategy
        self._stats['by_strategy'][strat] = \
            self._stats['by_strategy'].get(strat, 0) + 1

        logger.debug(f"📨 Сигнал добавлен: {signal}")

    async def get(self, timeout: float = 1.0) -> Optional[Signal]:
        """
        Забирает следующий сигнал из очереди.
        Возвращает None если очередь пуста или сигнал устарел.
        Вызывается из executor'а.
        """
        try:
            priority, counter, signal = await asyncio.wait_for(
                self._queue.get(), timeout=timeout
            )

            if signal.is_expired():
                self._stats['total_expired'] += 1
                logger.debug(f"⏰ Сигнал устарел: {signal}")
                return None

            self._stats['total_executed'] += 1
            return signal

        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения сигнала: {e}")
            return None

    def put_nowait(self, signal: Signal):
        """Синхронная версия put (если asyncio недоступен)"""
        self._counter += 1
        try:
            self._queue.put_nowait((signal.priority, self._counter, signal))
            self._stats['total_received'] += 1
        except asyncio.QueueFull:
            logger.warning("⚠️  SignalQueue полна")

    @property
    def size(self) -> int:
        return self._queue.qsize()

    @property
    def is_empty(self) -> bool:
        return self._queue.empty()

    def status(self):
        logger.info("═" * 50)
        logger.info("📨 SIGNAL QUEUE")
        logger.info(f"   В очереди:    {self.size}")
        logger.info(f"   Получено:     {self._stats['total_received']}")
        logger.info(f"   Исполнено:    {self._stats['total_executed']}")
        logger.info(f"   Устарело:     {self._stats['total_expired']}")
        logger.info("   По стратегиям:")
        for strat, count in self._stats['by_strategy'].items():
            logger.info(f"     {strat:12s}: {count}")
        logger.info("═" * 50)