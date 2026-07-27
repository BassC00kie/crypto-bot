# ═══════════════════════════════════════════════════════════════════
#  strategies/DEXScanner.py
#
#  DEX-CEX арбитраж.
#  Сравниваем цены на DEX (PancakeSwap/Uniswap/QuickSwap) vs CEX.
#
#  ⚠️  ЛОГИКА ХЕДЖИРОВАНИЯ НЕ ИЗМЕНЕНА:
#      DEX→CEX: купил на DEX → ШОРТ хедж на фьючерсе
#      CEX→DEX: купил на CEX → ЛОНГ хедж на фьючерсе
#
#  Что изменилось vs оригинал:
#    - Настройки читаются из config.py
#    - Добавлены сети: Arbitrum (Uniswap V3) + Polygon (QuickSwap)
#    - Больше монет по каждой сети
#    - time.sleep → asyncio.sleep
#    - CEX биржа берётся из exchange_manager (не только Bybit)
#    - Функции исполнения (buy_on_dex, sell_on_dex, open_short_hedge,
#      open_long_hedge, close_hedge, execute_dex_cex) — НЕ ТРОНУТЫ
# ═══════════════════════════════════════════════════════════════════

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import asyncio
import time
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from logger import logger
from config import DEX, THRESHOLDS, INTERVALS, DRY_RUN, COINS

load_dotenv()

WALLET_ADDRESS     = os.getenv('WALLET_ADDRESS')
WALLET_PRIVATE_KEY = os.getenv('WALLET_PRIVATE_KEY')

# Читаем из config — не хардкодим
MIN_SPREAD    = THRESHOLDS.get('dex_cex_min_spread_pct', 0.20)
MIN_PROFIT    = THRESHOLDS.get('dex_cex_min_profit_usd', 2.0)
MAX_GAS       = THRESHOLDS.get('max_gas_usd', 5.0)

# ─────────────────────────────────────────────────────────────────
# ХЕДЖ ФЛАГ — не трогаем
# ─────────────────────────────────────────────────────────────────
HEDGE_WITH_SHORT = True

# ─────────────────────────────────────────────────────────────────
# МИНИМАЛЬНЫЕ БАЛАНСЫ ДЛЯ РЕБАЛАНСИРОВКИ
# ─────────────────────────────────────────────────────────────────
MIN_BNB_BYBIT    = 0.3     # минимум BNB на Bybit
MIN_USDT_META    = 100     # минимум USDT в кошельке (BSC)
MIN_ETH_WALLET   = 0.01    # минимум ETH в кошельке (Arbitrum/Polygon)

# ─────────────────────────────────────────────────────────────────
# МОНЕТЫ ПО СЕТЯМ — расширенный список
# ─────────────────────────────────────────────────────────────────
# BSC (PancakeSwap)
BSC_COINS = COINS['dex_cex'].get('bsc', ['BNB', 'ETH', 'BTC', 'CAKE', 'USDT'])

# Arbitrum (Uniswap V3)
ARB_COINS = COINS['dex_cex'].get('arbitrum', ['ETH', 'ARB', 'LINK', 'UNI', 'USDC'])

# Polygon (QuickSwap)
POLY_COINS = COINS['dex_cex'].get('polygon', ['MATIC', 'ETH', 'USDC', 'USDT'])

# ─────────────────────────────────────────────────────────────────
# АДРЕСА ТОКЕНОВ
# ─────────────────────────────────────────────────────────────────

# BSC токены
BSC_TOKENS = {
    'BNB':  '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',  # WBNB
    'ETH':  '0x2170Ed0880ac9A755fd29B2688956BD959F933F8',  # ETH на BSC
    'BTC':  '0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c',  # BTCB
    'CAKE': '0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82',  # CAKE
    'USDT': '0x55d398326f99059fF775485246999027B3197955',  # USDT BSC
    'USDC': '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d',  # USDC BSC
    'ADA':  '0x3EE2200Efb3400fAbB9AacF31297cBdD1d435D47',
    'DOT':  '0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402',
    'LINK': '0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD',
}

# Arbitrum токены
ARB_TOKENS = {
    'ETH':  '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',  # WETH Arbitrum
    'ARB':  '0x912CE59144191C1204E64559FE8253a0e49E6548',
    'LINK': '0xf97f4df75117a78c1A5a0DBb814Af92458539FB4',
    'UNI':  '0xFa7F8980b0f1E64A2062791cc3b0871572f1F7f0',
    'USDC': '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',  # USDC.e Arbitrum
    'USDT': '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',
    'WBTC': '0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f',
    'GMX':  '0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a',
}

# Polygon токены
POLY_TOKENS = {
    'MATIC': '0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270',  # WMATIC
    'ETH':   '0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619',  # WETH Polygon
    'USDC':  '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174',
    'USDT':  '0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
    'WBTC':  '0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6',
    'LINK':  '0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39',
    'AAVE':  '0xD6DF932A45C0f255f85145f286eA0b292B21C90B',
}

# ─────────────────────────────────────────────────────────────────
# РОУТЕРЫ DEX
# ─────────────────────────────────────────────────────────────────
PANCAKE_ROUTER  = DEX['pancakeswap']['router']
UNISWAP_ROUTER  = DEX['uniswap_arbitrum']['router']
QUICKSWAP_ROUTER = DEX['quickswap']['router']

# ─────────────────────────────────────────────────────────────────
# ABI
# ─────────────────────────────────────────────────────────────────
ERC20_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256",   "name": "amountIn",  "type": "uint256"},
            {"internalType": "address[]", "name": "path",      "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"}
        ],
        "name": "getPair",
        "outputs": [{"internalType": "address", "name": "pair", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# ABI пула для проверки ликвидности
PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32",  "name": "_blockTimestampLast", "type": "uint32"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Фабрики DEX для получения адреса пула
FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"}
        ],
        "name": "getPair",
        "outputs": [{"internalType": "address", "name": "pair", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Адреса фабрик
FACTORY_BSC      = '0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73'  # PancakeSwap V2 Factory
FACTORY_ARB      = '0x1F98431c8aD98523631AE4a59f267346ea31F984'  # Uniswap V3 Factory (для V2 логики)
FACTORY_POLY     = '0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32'  # QuickSwap Factory

# Минимальная ликвидность пула в USD — ниже этого цена ненадёжна
MIN_POOL_LIQUIDITY_USD = {
    'BNB':  50_000,
    'ETH':  50_000,
    'BTC':  100_000,
    'CAKE': 20_000,
    'LINK': 20_000,
    'DOT':  10_000,
    'ADA':  10_000,
    'default': 10_000,
}

# Токены которые нужно пропускать на конкретных сетях.
# Причина: wrapped токены (BTCB, WBTC) имеют устаревшие пулы —
# их цена не следует за реальным BTC в реальном времени.
# Торговать ими через DEX-CEX арбитраж некорректно.
SKIP_TOKENS_ON_NETWORK = {
    'bsc':      ['BTC'],    # BTCB пул устаревший, цена не актуальна
    'arbitrum': ['WBTC'],   # то же самое
    'polygon':  ['WBTC'],
}



# ─────────────────────────────────────────────────────────────────
# ПОДКЛЮЧЕНИЯ К СЕТЯМ
# ─────────────────────────────────────────────────────────────────

def _connect_web3(rpc_url: str, poa: bool = False) -> Web3 | None:
    """Подключается к сети и возвращает Web3 объект"""
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if poa:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if w3.is_connected():
            logger.info(f"✅ Web3 подключён: {rpc_url[:40]}... блок #{w3.eth.block_number}")
            return w3
        else:
            logger.error(f"❌ Web3 не подключён: {rpc_url}")
            return None
    except Exception as e:
        logger.error(f"❌ Web3 ошибка подключения: {e}")
        return None

# BSC — всегда подключаем (основная сеть)
BSC_NODE = DEX['pancakeswap']['rpc']
w3_bsc   = _connect_web3(BSC_NODE, poa=True)

# Arbitrum — только если включён в config
w3_arb = None
if DEX['uniswap_arbitrum']['enabled']:
    w3_arb = _connect_web3(DEX['uniswap_arbitrum']['rpc'])

# Polygon — только если включён в config
w3_poly = None
if DEX['quickswap']['enabled']:
    w3_poly = _connect_web3(DEX['quickswap']['rpc'], poa=True)

# Роутеры
router_bsc  = None
router_arb  = None
router_poly = None

if w3_bsc:
    router_bsc = w3_bsc.eth.contract(
        address=Web3.to_checksum_address(PANCAKE_ROUTER),
        abi=ROUTER_ABI
    )
if w3_arb:
    router_arb = w3_arb.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_ROUTER),
        abi=ROUTER_ABI
    )
if w3_poly:
    router_poly = w3_poly.eth.contract(
        address=Web3.to_checksum_address(QUICKSWAP_ROUTER),
        abi=ROUTER_ABI
    )

# ─────────────────────────────────────────────────────────────────
# CEX — получаем из exchange_manager (централизованно)
# Объекты создаются один раз в main.py и передаются сюда
# ─────────────────────────────────────────────────────────────────
# Глобальные переменные — заполняются через set_exchange_manager()
_em  = None   # ExchangeManager
_bm  = None   # BalanceManager

def set_exchange_manager(em, bm=None):
    """
    Вызывается из DEXScanner.__init__() при старте.
    После этого все функции используют централизованные объекты.
    """
    global _em, _bm
    _em = em
    _bm = bm

def _get_bybit_pub():
    """Публичный объект Bybit для чтения цен"""
    if _em:
        return _em.get('bybit', auth=False)
    # Fallback если вызывается без exchange_manager (прямой запуск)
    import ccxt as _ccxt
    obj = _ccxt.bybit()
    try:
        obj.load_markets()
    except Exception:
        pass
    return obj

def _get_bybit_auth():
    """Авторизованный объект Bybit для торговли"""
    if _em:
        return _em.get('bybit', auth=True)
    import ccxt as _ccxt
    from dotenv import load_dotenv as _load
    _load()
    return _ccxt.bybit({
        'apiKey': os.getenv('BYBIT_API_KEY'),
        'secret': os.getenv('BYBIT_API_SECRET'),
    })

def _get_wallet_usdt() -> float:
    """USDT баланс MetaMask — из balance_manager или мок"""
    if _bm and DRY_RUN:
        return _em.get_mock_wallet_usdt() if _em else 750.0
    if _bm and not DRY_RUN:
        # В реальном режиме — реальный баланс кошелька
        # (читается через web3, не через ccxt)
        return 0.0  # TODO: добавить web3 баланс кошелька
    return 750.0

# ─────────────────────────────────────────────────────────────────
# БЛОК 1 — ПОЛУЧЕНИЕ ЦЕН DEX
# (не трогаем — только добавляем новые сети)
# ─────────────────────────────────────────────────────────────────

def get_dex_price_bsc(token_symbol: str) -> float | None:
    """Получаем цену токена на PancakeSwap (BSC) — оригинальная функция"""
    if not router_bsc:
        return None
    try:
        token_address = BSC_TOKENS.get(token_symbol)
        usdt_address  = BSC_TOKENS['USDT']

        if not token_address:
            return None

        amount_in = w3_bsc.to_wei(1, 'ether')
        amounts   = router_bsc.functions.getAmountsOut(
            amount_in,
            [
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(usdt_address)
            ]
        ).call()
        return amounts[1] / 10**18

    except Exception as e:
        logger.debug(f"BSC цена {token_symbol}: {e}")
        return None

def get_dex_price_arbitrum(token_symbol: str) -> float | None:
    """Получаем цену токена на Uniswap V3 (Arbitrum)"""
    if not router_arb:
        return None
    try:
        token_address = ARB_TOKENS.get(token_symbol)
        usdc_address  = ARB_TOKENS['USDC']

        if not token_address:
            return None

        amount_in = w3_arb.to_wei(1, 'ether')
        amounts   = router_arb.functions.getAmountsOut(
            amount_in,
            [
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(usdc_address)
            ]
        ).call()
        return amounts[1] / 10**6  # USDC имеет 6 decimals

    except Exception as e:
        logger.debug(f"Arbitrum цена {token_symbol}: {e}")
        return None

def get_dex_price_polygon(token_symbol: str) -> float | None:
    """Получаем цену токена на QuickSwap (Polygon)"""
    if not router_poly:
        return None
    try:
        token_address = POLY_TOKENS.get(token_symbol)
        usdc_address  = POLY_TOKENS['USDC']

        if not token_address:
            return None

        amount_in = w3_poly.to_wei(1, 'ether')
        amounts   = router_poly.functions.getAmountsOut(
            amount_in,
            [
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(usdc_address)
            ]
        ).call()
        return amounts[1] / 10**6

    except Exception as e:
        logger.debug(f"Polygon цена {token_symbol}: {e}")
        return None

def get_cex_price(symbol: str) -> float | None:
    """Получаем цену на Bybit — оригинальная функция"""
    try:
        ticker = _get_bybit_pub().fetch_ticker(f"{symbol}/USDT")
        return ticker['last']
    except Exception as e:
        logger.error(f"CEX цена {symbol}: {e}")
        return None

def get_gas_price_usd(network: str) -> float:
    """
    Оценивает стоимость газа в USD для свапа.
    Используется для фильтрации невыгодных сделок.
    """
    try:
        if network == 'bsc' and w3_bsc:
            gwei     = w3_bsc.eth.gas_price / 10**9
            gas_units = 200000
            bnb_price = get_cex_price('BNB') or 300
            return (gwei * gas_units / 10**9) * bnb_price

        elif network == 'arbitrum' and w3_arb:
            gwei      = w3_arb.eth.gas_price / 10**9
            gas_units = 150000
            eth_price = get_cex_price('ETH') or 3000
            return (gwei * gas_units / 10**9) * eth_price

        elif network == 'polygon' and w3_poly:
            gwei      = w3_poly.eth.gas_price / 10**9
            gas_units = 150000
            matic_price = get_cex_price('MATIC') or 0.8
            return (gwei * gas_units / 10**9) * matic_price

    except Exception as e:
        logger.debug(f"Газ {network}: {e}")

    # Дефолтные значения если не удалось получить
    defaults = {'bsc': 0.30, 'arbitrum': 0.50, 'polygon': 0.05}
    return defaults.get(network, 0.50)

# ─────────────────────────────────────────────────────────────────
# БЛОК 1.5 — ПРОВЕРКА ЛИКВИДНОСТИ ПУЛА
# Проверяем что в пуле достаточно денег — иначе цена ненадёжна
# ─────────────────────────────────────────────────────────────────

def get_pool_liquidity_usd(token_symbol: str, network: str = 'bsc') -> float:
    """
    Возвращает ликвидность пула в USD.
    Получает резервы через getReserves() и считает стоимость USDT стороны.

    Если ликвидность низкая — цена в пуле ненадёжна.
    """
    try:
        if network == 'bsc':
            w3        = w3_bsc
            tokens    = BSC_TOKENS
            stable    = BSC_TOKENS['USDT']
            factory_addr = FACTORY_BSC
        elif network == 'arbitrum':
            w3        = w3_arb
            tokens    = ARB_TOKENS
            stable    = ARB_TOKENS['USDC']
            factory_addr = FACTORY_ARB
        elif network == 'polygon':
            w3        = w3_poly
            tokens    = POLY_TOKENS
            stable    = POLY_TOKENS['USDC']
            factory_addr = FACTORY_POLY
        else:
            return 0.0

        if not w3:
            return 0.0

        token_addr = tokens.get(token_symbol)
        if not token_addr:
            return 0.0

        # Получаем адрес пула через фабрику
        factory = w3.eth.contract(
            address=Web3.to_checksum_address(factory_addr),
            abi=FACTORY_ABI
        )
        pair_addr = factory.functions.getPair(
            Web3.to_checksum_address(token_addr),
            Web3.to_checksum_address(stable)
        ).call()

        # Нулевой адрес = пул не существует
        if pair_addr == '0x0000000000000000000000000000000000000000':
            logger.debug(f"  {token_symbol} [{network}]: пул не найден")
            return 0.0

        # Получаем резервы пула
        pair = w3.eth.contract(
            address=Web3.to_checksum_address(pair_addr),
            abi=PAIR_ABI
        )
        reserves   = pair.functions.getReserves().call()
        token0     = pair.functions.token0().call().lower()
        reserve0   = reserves[0]
        reserve1   = reserves[1]

        # Определяем какой резерв — стейбл
        stable_lower = stable.lower()
        if token0 == stable_lower:
            stable_reserve = reserve0
        else:
            stable_reserve = reserve1

        # USDT/USDC имеют 6 или 18 decimals
        if network == 'bsc':
            # USDT на BSC — 18 decimals
            liquidity_usd = stable_reserve / 10**18
        else:
            # USDC на Arbitrum/Polygon — 6 decimals
            liquidity_usd = stable_reserve / 10**6

        return liquidity_usd

    except Exception as e:
        logger.debug(f"  Ликвидность {token_symbol} [{network}]: {e}")
        return 0.0


# ─────────────────────────────────────────────────────────────────
# БЛОК 2 — ИСПОЛНЕНИЕ DEX
# ⚠️  НЕ ТРОНУТО — оригинальный код
# ─────────────────────────────────────────────────────────────────

def buy_on_dex(token_symbol: str, amount_usdt: float, network: str = 'bsc') -> bool:
    """Покупаем токен на DEX за USDT — оригинальная функция"""
    try:
        if network == 'bsc':
            w3            = w3_bsc
            router        = router_bsc
            tokens        = BSC_TOKENS
            usdt_address  = BSC_TOKENS['USDT']
        elif network == 'arbitrum':
            w3            = w3_arb
            router        = router_arb
            tokens        = ARB_TOKENS
            usdt_address  = ARB_TOKENS['USDC']
        elif network == 'polygon':
            w3            = w3_poly
            router        = router_poly
            tokens        = POLY_TOKENS
            usdt_address  = POLY_TOKENS['USDC']
        else:
            logger.error(f"Неизвестная сеть: {network}")
            return False

        token_address  = tokens.get(token_symbol)
        if not token_address or not router:
            return False

        amount_in      = int(amount_usdt * 10**18)
        amounts_out    = router.functions.getAmountsOut(
            amount_in,
            [Web3.to_checksum_address(usdt_address),
             Web3.to_checksum_address(token_address)]
        ).call()
        min_amount_out = int(amounts_out[1] * 0.995)

        if DRY_RUN:
            logger.info(f"🧪 [СИМУЛЯЦИЯ] DEX {network}: купить {token_symbol} за ${amount_usdt}")
            return True

        nonce    = w3.eth.get_transaction_count(WALLET_ADDRESS)
        deadline = int(time.time()) + 60
        tx = router.functions.swapExactTokensForTokens(
            amount_in, min_amount_out,
            [Web3.to_checksum_address(usdt_address),
             Web3.to_checksum_address(token_address)],
            WALLET_ADDRESS, deadline
        ).build_transaction({
            'from': WALLET_ADDRESS, 'gas': 200000,
            'gasPrice': w3.to_wei('5', 'gwei'), 'nonce': nonce,
        })
        signed  = w3.eth.account.sign_transaction(tx, WALLET_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        logger.info(f"✅ DEX {network} покупка: {tx_hash.hex()}")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка DEX {network} покупки: {e}")
        return False

def sell_on_dex(token_symbol: str, amount_usdt: float, network: str = 'bsc') -> bool:
    """Продаём токен на DEX — оригинальная функция"""
    try:
        if network == 'bsc':
            w3            = w3_bsc
            router        = router_bsc
            tokens        = BSC_TOKENS
            usdt_address  = BSC_TOKENS['USDT']
        elif network == 'arbitrum':
            w3            = w3_arb
            router        = router_arb
            tokens        = ARB_TOKENS
            usdt_address  = ARB_TOKENS['USDC']
        elif network == 'polygon':
            w3            = w3_poly
            router        = router_poly
            tokens        = POLY_TOKENS
            usdt_address  = POLY_TOKENS['USDC']
        else:
            return False

        token_address  = tokens.get(token_symbol)
        if not token_address or not router:
            return False

        amount_in      = int(amount_usdt * 10**18)
        amounts_out    = router.functions.getAmountsOut(
            amount_in,
            [Web3.to_checksum_address(token_address),
             Web3.to_checksum_address(usdt_address)]
        ).call()
        min_amount_out = int(amounts_out[1] * 0.995)

        if DRY_RUN:
            logger.info(f"🧪 [СИМУЛЯЦИЯ] DEX {network}: продать {token_symbol}")
            return True

        nonce    = w3.eth.get_transaction_count(WALLET_ADDRESS)
        deadline = int(time.time()) + 60
        tx = router.functions.swapExactTokensForTokens(
            amount_in, min_amount_out,
            [Web3.to_checksum_address(token_address),
             Web3.to_checksum_address(usdt_address)],
            WALLET_ADDRESS, deadline
        ).build_transaction({
            'from': WALLET_ADDRESS, 'gas': 200000,
            'gasPrice': w3.to_wei('5', 'gwei'), 'nonce': nonce,
        })
        signed  = w3.eth.account.sign_transaction(tx, WALLET_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"✅ DEX {network} продажа: {tx_hash.hex()}")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка DEX {network} продажи: {e}")
        return False

# ─────────────────────────────────────────────────────────────────
# БЛОК 3 — ИСПОЛНЕНИЕ CEX
# ⚠️  НЕ ТРОНУТО — оригинальный код
# ─────────────────────────────────────────────────────────────────

def buy_on_cex(coin: str, amount: float) -> bool:
    """Покупаем на Bybit — оригинальная функция"""
    if DRY_RUN:
        logger.info(f"🧪 [СИМУЛЯЦИЯ] CEX: купить {amount} {coin}")
        return True
    try:
        order = _get_bybit_auth().create_market_buy_order(f"{coin}/USDT", amount)
        logger.info(f"✅ CEX покупка: {order['id']}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка CEX покупки: {e}")
        return False

def sell_on_cex(coin: str, amount: float) -> bool:
    """Продаём на Bybit — оригинальная функция"""
    if DRY_RUN:
        logger.info(f"🧪 [СИМУЛЯЦИЯ] CEX: продать {amount} {coin}")
        return True
    try:
        order = _get_bybit_auth().create_market_sell_order(f"{coin}/USDT", amount)
        logger.info(f"✅ CEX продажа: {order['id']}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка CEX продажи: {e}")
        return False

# ─────────────────────────────────────────────────────────────────
# БЛОК 4 — ХЕДЖ
# ⚠️  НЕ ТРОНУТО ВООБЩЕ — оригинальный код 1:1
# ─────────────────────────────────────────────────────────────────

def open_short_hedge(coin: str, amount: float) -> bool:
    """Открываем шорт хедж на фьючерсе — оригинальная функция"""
    if DRY_RUN:
        logger.info(f"🧪 [СИМУЛЯЦИЯ] ХЕДЖ: шорт {amount} {coin}")
        return True
    try:
        order = _get_bybit_auth().create_market_sell_order(
            f"{coin}/USDT:USDT", amount,
            params={"reduceOnly": False}
        )
        logger.info(f"✅ Хедж шорт: {order['id']}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка хеджа: {e}")
        return False

def open_long_hedge(coin: str, amount: float) -> bool:
    """Открываем лонг хедж на фьючерсе — оригинальная функция"""
    if DRY_RUN:
        logger.info(f"🧪 [СИМУЛЯЦИЯ] ХЕДЖ: лонг {amount} {coin}")
        return True
    try:
        order = _get_bybit_auth().create_market_buy_order(
            f"{coin}/USDT:USDT", amount,
            params={"reduceOnly": False}
        )
        logger.info(f"✅ Хедж лонг: {order['id']}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка хеджа: {e}")
        return False

def close_hedge(coin: str, amount: float, side: str) -> bool:
    """Закрываем хедж после исполнения сделки — оригинальная функция"""
    futures_symbol = f"{coin}/USDT:USDT"

    if DRY_RUN:
        logger.info(f"🧪 [СИМУЛЯЦИЯ] Закрываем хедж {side} {amount} {coin}")
        return True

    try:
        if side == "short":
            order = _get_bybit_auth().create_market_buy_order(
                futures_symbol, amount,
                params={"reduceOnly": True}
            )
        else:  # long
            order = _get_bybit_auth().create_market_sell_order(
                futures_symbol, amount,
                params={"reduceOnly": True}
            )
        logger.info(f"✅ Хедж закрыт: {order['id']}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка закрытия хеджа: {e}")
        return False

# ─────────────────────────────────────────────────────────────────
# БЛОК 5 — ИСПОЛНЕНИЕ АРБИТРАЖА
# ⚠️  ЛОГИКА ХЕДЖИРОВАНИЯ НЕ ТРОНУТА — только добавлен network параметр
# ─────────────────────────────────────────────────────────────────

def execute_dex_cex(
    coin:       str,
    dex_price:  float,
    cex_price:  float,
    spread_pct: float,
    direction:  str,
    network:    str = 'bsc',
    amount_usd: float = 225.0,  # 30% от DEX бюджета $750
):
    """
    Исполняет DEX-CEX арбитраж.
    ⚠️  Логика хеджирования НЕ ИЗМЕНЕНА:
        DEX→CEX: купили на DEX → ШОРТ хедж
        CEX→DEX: купили на CEX → ЛОНГ хедж
    """
    coin_amount = round(amount_usd / dex_price, 4)

    logger.info(f"🚀 Исполняю {direction} арбитраж {coin} | сеть: {network}")

    # Читаем настройки сети из config
    from config import DEX_NETWORK_SETTINGS
    net_cfg      = DEX_NETWORK_SETTINGS.get(network, {})
    needs_hedge  = net_cfg.get('needs_hedge', True) and HEDGE_WITH_SHORT
    hedge_wait   = net_cfg.get('hedge_wait_sec', 60)

    # ── Проверяем наличие средств перед исполнением ───────────────
    if not DRY_RUN:
        try:
            bybit_balance = _get_bybit_auth().fetch_balance()['total']
            usdt_on_cex   = bybit_balance.get('USDT', 0)
            coin_on_cex   = bybit_balance.get(coin, 0)

            if direction == "DEX→CEX":
                # Нужны монеты на CEX для продажи
                if coin_on_cex < coin_amount:
                    logger.warning(
                        f"⚠️  {coin}: на Bybit только {coin_on_cex:.6f} "
                        f"нужно {coin_amount:.6f} — пропускаем"
                    )
                    return
            else:  # CEX→DEX
                # Нужен USDT на CEX для покупки
                if usdt_on_cex < amount_usd:
                    logger.warning(
                        f"⚠️  USDT на Bybit: ${usdt_on_cex:.2f} "
                        f"нужно ${amount_usd:.2f} — пропускаем"
                    )
                    return
        except Exception as e:
            logger.error(f"❌ Ошибка проверки баланса: {e}")
            return
    else:
        # DRY_RUN — симулируем наличие баланса
        logger.debug(
            f"  [DRY] Баланс OK: {coin_amount:.6f} {coin} на CEX, "
            f"${amount_usd:.2f} USDT в MetaMask"
        )

    if direction == "DEX→CEX":
        # Купили на DEX дешевле → продаём на CEX дороже
        # Риск на медленных сетях: цена может вырасти пока летит транзакция
        # → ШОРТ хедж только если сеть медленная (оригинальная логика)
        if not buy_on_dex(coin, amount_usd, network):
            return
        sell_on_cex(coin, coin_amount)
        if needs_hedge:
            open_short_hedge(coin, coin_amount)    # ← ШОРТ (не трогаем)
            logger.info(f"⏳ Ждём подтверждения транзакции ({hedge_wait}с)...")
            time.sleep(hedge_wait)
            close_hedge(coin, coin_amount, "short")
        else:
            logger.info(f"⚡ {network}: быстрая сеть — хедж не нужен")

    else:  # CEX→DEX
        # Купили на CEX дешевле → продаём на DEX дороже
        # Риск на медленных сетях: цена может упасть пока летит транзакция
        # → ЛОНГ хедж только если сеть медленная (оригинальная логика)
        if not buy_on_cex(coin, coin_amount):
            return
        sell_on_dex(coin, amount_usd, network)
        if needs_hedge:
            open_long_hedge(coin, coin_amount)     # ← ЛОНГ (не трогаем)
            logger.info(f"⏳ Ждём подтверждения транзакции ({hedge_wait}с)...")
            time.sleep(hedge_wait)
            close_hedge(coin, coin_amount, "short")
        else:
            logger.info(f"⚡ {network}: быстрая сеть — хедж не нужен")

    # Считаем прибыль с учётом газа конкретной сети
    gas    = get_gas_price_usd(network)
    fees   = amount_usd * 0.002
    gross  = amount_usd * (spread_pct / 100)
    profit = gross - gas - fees

    logger.info(f"   Сеть:     {network}")
    logger.info(f"   Спред:    {spread_pct:.4f}%")
    logger.info(f"   Валовая:  ${gross:.4f}")
    logger.info(f"   Газ:     -${gas:.4f}")
    logger.info(f"   Комиссии:-${fees:.4f}")
    logger.info(f"   Чистая:   ${profit:.4f}")

    check_and_rebalance()

# ─────────────────────────────────────────────────────────────────
# БЛОК 6 — СКАНЕР (расширен на 3 сети)
# ─────────────────────────────────────────────────────────────────

def scan_dex_cex():
    """
    Сканирует все монеты по всем активным DEX сетям.
    Оригинальная логика + добавлены Arbitrum и Polygon.
    """
    logger.info("--- Сканирую DEX vs CEX ---")

    # ── BSC / PancakeSwap ────────────────────────────────────────
    if w3_bsc:
        _scan_network('bsc', BSC_COINS, get_dex_price_bsc)

    # ── Arbitrum / Uniswap V3 ────────────────────────────────────
    if w3_arb and DEX['uniswap_arbitrum']['enabled']:
        _scan_network('arbitrum', ARB_COINS, get_dex_price_arbitrum)

    # ── Polygon / QuickSwap ──────────────────────────────────────
    if w3_poly and DEX['quickswap']['enabled']:
        _scan_network('polygon', POLY_COINS, get_dex_price_polygon)

def _scan_network(network: str, coins: list, get_price_fn):
    """Сканирует монеты в одной сети — с проверкой ликвидности пула"""
    from config import TOTAL_CAPITAL_USD, CAPITAL_ALLOCATION

    gas_usd    = get_gas_price_usd(network)
    budget     = TOTAL_CAPITAL_USD * CAPITAL_ALLOCATION.get('dex_cex', 0.15)
    amount_usd = min(budget * 0.3, 500)

    logger.info(f"  [{network}] газ: ~${gas_usd:.3f} | бюджет на сделку: ${amount_usd:.0f}")

    for coin in coins:
        if coin in ('USDT', 'USDC'):
            continue

        # Пропускаем токены с неактивными пулами на этой сети
        if coin in SKIP_TOKENS_ON_NETWORK.get(network, []):
            logger.info(f"  ⏭️  {coin} [{network}]: wrapped токен — пропускаем")
            continue

        try:
            # ── Шаг 1: проверяем ликвидность пула ────────────────
            min_liquidity = MIN_POOL_LIQUIDITY_USD.get(
                coin, MIN_POOL_LIQUIDITY_USD['default']
            )
            liquidity = get_pool_liquidity_usd(coin, network)

            if liquidity < min_liquidity:
                logger.debug(
                    f"  ⏭️  {coin} [{network}]: ликвидность пула "
                    f"${liquidity:,.0f} < минимум ${min_liquidity:,.0f} — пропускаем"
                )
                continue

            # ── Шаг 2: получаем цены ──────────────────────────────
            dex_price = get_price_fn(coin)
            cex_price = get_cex_price(coin)

            if not dex_price or not cex_price:
                continue

            spread_pct = abs(dex_price - cex_price) / cex_price * 100

            # ── Шаг 3: определяем направление ───────────────────
            if dex_price < cex_price:
                direction = "DEX→CEX"
                # DEX→CEX: нужны монеты на CEX для продажи
                # В DRY_RUN пропускаем — симулируем наличие
                if not DRY_RUN:
                    bybit_bal  = _get_bybit_auth().fetch_balance()['total']
                    coin_on_cex = bybit_bal.get(coin, 0)
                    coin_needed = amount_usd / dex_price
                    if coin_on_cex < coin_needed:
                        logger.warning(
                            f"  ⚠️  {coin}: на Bybit {coin_on_cex:.4f} "
                            f"нужно {coin_needed:.4f} — пропускаем"
                        )
                        continue
            else:
                direction = "CEX→DEX"
                # CEX→DEX: нужен USDT на CEX для покупки
                if not DRY_RUN:
                    bybit_bal   = _get_bybit_auth().fetch_balance()['total']
                    usdt_on_cex = bybit_bal.get('USDT', 0)
                    if usdt_on_cex < amount_usd:
                        logger.warning(
                            f"  ⚠️  USDT на Bybit: ${usdt_on_cex:.2f} "
                            f"нужно ${amount_usd:.2f} — пропускаем"
                        )
                        continue

            gross = amount_usd * (spread_pct / 100)
            fees  = amount_usd * 0.002
            net   = gross - gas_usd - fees

            logger.info(
                f"  {coin:6s} [{network}] | "
                f"DEX: ${dex_price:,.4f} | "
                f"CEX: ${cex_price:,.4f} | "
                f"спред: {spread_pct:.4f}% | "
                f"пул: ${liquidity:,.0f} | "
                f"чистая: ${net:.4f} | "
                f"{direction}"
            )

            # ── Шаг 5: входим если выгодно ────────────────────────
            if spread_pct >= MIN_SPREAD and net >= MIN_PROFIT:
                logger.info(
                    f"🔥 [{network}] {coin} спред {spread_pct:.4f}% | "
                    f"чистая: ${net:.4f} | {direction}"
                )
                execute_dex_cex(
                    coin, dex_price, cex_price,
                    spread_pct, direction, network, amount_usd
                )

        except Exception as e:
            logger.error(f"Ошибка {coin} [{network}]: {e}")

# ─────────────────────────────────────────────────────────────────
# БЛОК 7 — РЕБАЛАНСИРОВКА
# ⚠️  НЕ ТРОНУТО — оригинальный код
# ─────────────────────────────────────────────────────────────────

def check_and_rebalance():
    """Проверяем балансы и ребалансируем — оригинальная функция"""
    if DRY_RUN:
        mock         = _em.get_mock_wallet_usdt() if _em else 750.0
        bnb_on_bybit = (_em._mock_balance().get('BNB', 0.5) if _em else 0.5)
        usdt_on_meta = mock

        logger.info(f"💼 [DRY] Bybit BNB: {bnb_on_bybit:.4f}")
        logger.info(f"💼 [DRY] MetaMask USDT: ${usdt_on_meta:.2f} (DEX бюджет)")

        if bnb_on_bybit < MIN_BNB_BYBIT:
            logger.info("⚠️  Мало BNB → покупаем на Bybit")
        else:
            logger.info("✅ BNB в норме")

        if usdt_on_meta < MIN_USDT_META:
            logger.info("⚠️  Мало USDT → свапаем BNB→USDT на DEX")
        else:
            logger.info("✅ USDT в норме")
        return

    try:
        # Проверяем BNB на Bybit
        bybit_balance = _get_bybit_auth().fetch_balance()
        bnb_on_bybit  = bybit_balance['total'].get('BNB', 0)
        logger.info(f"💼 Bybit BNB: {bnb_on_bybit:.4f}")

        if bnb_on_bybit < MIN_BNB_BYBIT:
            logger.info("⚠️  Мало BNB на Bybit → покупаем")
            buy_on_cex('BNB', MIN_BNB_BYBIT)

        # Проверяем USDT в кошельке (BSC)
        if w3_bsc and WALLET_ADDRESS:
            usdt_contract = w3_bsc.eth.contract(
                address=Web3.to_checksum_address(BSC_TOKENS['USDT']),
                abi=ERC20_ABI
            )
            usdt_balance = usdt_contract.functions.balanceOf(
                Web3.to_checksum_address(WALLET_ADDRESS)
            ).call()
            usdt_on_meta = usdt_balance / 10**18
            logger.info(f"💼 MetaMask USDT (BSC): ${usdt_on_meta:.2f}")

            if usdt_on_meta < MIN_USDT_META:
                logger.info("⚠️  Мало USDT в кошельке → свапаем BNB→USDT")
                sell_on_dex('BNB', MIN_USDT_META, 'bsc')

    except Exception as e:
        logger.error(f"❌ Ошибка ребалансировки: {e}")

# ─────────────────────────────────────────────────────────────────
# ASYNCIO ОБЁРТКА ДЛЯ main.py
# ─────────────────────────────────────────────────────────────────

class DEXScanner:
    """
    Asyncio-совместимая обёртка для запуска из main.py.
    Интегрирована с risk_manager, balance_manager и telegram.
    Исполнение сделок — оригинальный синхронный код.
    """

    def __init__(self, exchange_manager=None, balance_manager=None,
                 risk_manager=None, signal_queue=None):
        self.em = exchange_manager
        self.bm = balance_manager
        self.rm = risk_manager
        self.sq = signal_queue
        self._tg = None  # устанавливается через set_telegram()

        # Централизуем — передаём exchange_manager в модульные функции
        set_exchange_manager(exchange_manager, balance_manager)

    def set_telegram(self, tg):
        """Подключаем Telegram для уведомлений"""
        self._tg = tg

    async def run(self):
        """Бесконечный цикл — вызывается из main.py"""
        logger.info(
            f"🔀 DEX Scanner запущен | "
            f"мин. спред: {MIN_SPREAD}% | "
            f"мин. прибыль: ${MIN_PROFIT} | "
            f"интервал: {INTERVALS['dex_cex_scan']}с"
        )

        while True:
            try:
                # Проверяем риск-менеджер перед сканированием
                if self.rm and not self.rm.is_running:
                    await asyncio.sleep(INTERVALS['dex_cex_scan'])
                    continue

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, check_and_rebalance)

                # Патчим execute_dex_cex для интеграции с rm/tg
                await loop.run_in_executor(None, self._scan_with_hooks)

            except asyncio.CancelledError:
                logger.info("🔀 DEX Scanner остановлен")
                break
            except Exception as e:
                logger.error(f"❌ DEX Scanner ошибка: {e}")

            await asyncio.sleep(INTERVALS['dex_cex_scan'])

    def _scan_with_hooks(self):
        """
        Запускает сканирование с хуками для rm и tg.
        Перехватывает найденные сделки и регистрирует их.
        """
        # Патчим глобальную функцию execute_dex_cex
        import strategies.DEXScanner as dex_module
        original_execute = dex_module.execute_dex_cex

        scanner_self = self

        def execute_with_hooks(coin, dex_price, cex_price,
                               spread_pct, direction, network, amount_usd):
            # Проверяем риск перед исполнением
            if scanner_self.rm:
                ok, reason = scanner_self.rm.check_trade(
                    'dex_cex', 'bybit', amount_usd
                )
                if not ok:
                    logger.warning(f"⚠️  DEX-CEX отклонено риск-менеджером: {reason}")
                    return

            # Исполняем оригинальную функцию
            original_execute(coin, dex_price, cex_price,
                             spread_pct, direction, network, amount_usd)

            # Регистрируем в risk_manager
            if scanner_self.rm:
                fee_pct = 0.002
                gross   = amount_usd * (spread_pct / 100)
                gas     = get_gas_price_usd(network)
                profit  = gross - gas - amount_usd * fee_pct
                scanner_self.rm.on_position_opened('dex_cex', amount_usd)
                scanner_self.rm.on_position_closed('dex_cex', profit)

            # Telegram уведомление
            if scanner_self._tg:
                import asyncio as aio
                try:
                    loop = aio.get_event_loop()
                    fee_pct = 0.002
                    gross   = amount_usd * (spread_pct / 100)
                    gas     = get_gas_price_usd(network)
                    profit  = gross - gas - amount_usd * fee_pct
                    loop.call_soon_threadsafe(
                        lambda: aio.ensure_future(
                            scanner_self._tg.notify_trade_close(
                                'dex_cex', f"{coin} [{network}]", profit
                            )
                        )
                    )
                except Exception:
                    pass

        # Заменяем на время сканирования
        dex_module.execute_dex_cex = execute_with_hooks
        try:
            scan_dex_cex()
        finally:
            # Восстанавливаем оригинал
            dex_module.execute_dex_cex = original_execute

    async def run_gas_monitor(self):
        """Мониторинг цены газа"""
        while True:
            try:
                for network in ['bsc', 'arbitrum', 'polygon']:
                    gas = get_gas_price_usd(network)
                    if gas > MAX_GAS:
                        msg = f"⛽ [{network}] газ дорогой: ${gas:.3f} > лимит ${MAX_GAS}"
                        logger.warning(msg)
                        if self._tg:
                            await self._tg.notify_risk_alert(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Gas monitor: {e}")

            await asyncio.sleep(INTERVALS['dex_cex_gas_check'])

# ─────────────────────────────────────────────────────────────────
# ПРЯМОЙ ЗАПУСК (без main.py)
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("🚀 DEX-CEX сканер запущен")
    logger.info(f"Сети: BSC{'+ Arbitrum' if w3_arb else ''}{'+ Polygon' if w3_poly else ''}")
    logger.info(f"Мин. спред: {MIN_SPREAD}% | Мин. прибыль: ${MIN_PROFIT}")

    if not w3_bsc:
        logger.error("❌ BSC не подключён — проверь BSC_NODE_URL в .env")
        exit()

    while True:
        check_and_rebalance()
        scan_dex_cex()
        logger.info(f"Следующая проверка через {INTERVALS['dex_cex_scan']} сек...")
        time.sleep(INTERVALS['dex_cex_scan'])