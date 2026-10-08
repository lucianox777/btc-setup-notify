const crypto = require('node:crypto');
const {
  dashboardPassword,
  isSessionAuthorized,
  json
} = require('./_auth.js');

const SYMBOL = 'BTCUSDT';
const BASE_ASSET = 'BTC';
const QUOTE_ASSET = 'USDT';
const DEFAULT_BASE_URL = 'https://api.binance.com';
const DEFAULT_RECV_WINDOW = 5000;
const MAX_TRADE_PAGES = 20;
const TRADE_PAGE_SIZE = 1000;

function numberOr(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function addOtherFee(map, asset, amount) {
  if (!asset || !Number.isFinite(amount) || amount <= 0) return;
  map[asset] = (map[asset] || 0) + amount;
}

function computeCostBasis(trades, baseAsset = BASE_ASSET, quoteAsset = QUOTE_ASSET) {
  const ordered = [...(trades || [])].sort((a, b) => numberOr(a.time) - numberOr(b.time));
  let qty = 0;
  let cost = 0;
  let realizedPnl = 0;
  let historyGap = false;
  const otherFees = {};

  for (const trade of ordered) {
    const price = numberOr(trade.price, NaN);
    const rawQty = numberOr(trade.qty, NaN);
    const quoteQty = numberOr(
      trade.quoteQty,
      Number.isFinite(price) && Number.isFinite(rawQty) ? price * rawQty : NaN
    );
    const commission = Math.max(0, numberOr(trade.commission, 0));
    const commissionAsset = String(trade.commissionAsset || '');
    if (!(price > 0) || !(rawQty > 0) || !(quoteQty >= 0)) continue;

    if (trade.isBuyer) {
      let acquiredQty = rawQty;
      let addedCost = quoteQty;
      if (commissionAsset === baseAsset) acquiredQty = Math.max(0, rawQty - commission);
      else if (commissionAsset === quoteAsset) addedCost += commission;
      else addOtherFee(otherFees, commissionAsset, commission);
      qty += acquiredQty;
      cost += addedCost;
      continue;
    }

    const avgBefore = qty > 0 ? cost / qty : 0;
    let qtyReduction = rawQty;
    let netProceeds = quoteQty;

    if (commissionAsset === baseAsset) qtyReduction += commission;
    else if (commissionAsset === quoteAsset) netProceeds = Math.max(0, quoteQty - commission);
    else addOtherFee(otherFees, commissionAsset, commission);

    if (!(qty > 0)) {
      historyGap = true;
      continue;
    }

    const relievedQty = Math.min(qty, qtyReduction);
    if (qtyReduction > qty + 1e-12) historyGap = true;
    const relievedCost = avgBefore * relievedQty;

    qty = Math.max(0, qty - relievedQty);
    cost = Math.max(0, cost - relievedCost);
    realizedPnl += netProceeds - relievedCost;

    if (qty < 1e-12) {
      qty = 0;
      cost = 0;
    }
  }

  return {
    quantity: qty,
    cost,
    averagePrice: qty > 0 ? cost / qty : null,
    realizedPnl,
    historyGap,
    otherFees
  };
}

async function parseResponse(response) {
  const text = await response.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; }
  catch (_) { body = { message: text || response.statusText }; }
  if (!response.ok) {
    const err = new Error(
      body && body.msg ? body.msg :
      body && body.message ? body.message :
      `Binance HTTP ${response.status}`
    );
    err.status = response.status;
    err.code = body && body.code;
    err.binance = body;
    throw err;
  }
  return body;
}

function createBinanceClient({ apiKey, apiSecret, baseUrl, recvWindow }) {
  let offsetMs = 0;
  let timeReady = false;

  async function syncTime() {
    if (timeReady) return;
    try {
      const response = await fetch(`${baseUrl}/api/v3/time`, {
        headers: { Accept: 'application/json' }
      });
      const body = await parseResponse(response);
      if (Number.isFinite(Number(body.serverTime))) {
        offsetMs = Number(body.serverTime) - Date.now();
      }
    } catch (_) {
      offsetMs = 0;
    }
    timeReady = true;
  }

  async function request(path, params = {}, signed = false) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        query.set(key, String(value));
      }
    }

    if (signed) {
      await syncTime();
      query.set('recvWindow', String(recvWindow));
      query.set('timestamp', String(Date.now() + offsetMs));
      const unsigned = query.toString();
      const signature = crypto
        .createHmac('sha256', apiSecret)
        .update(unsigned)
        .digest('hex');
      query.set('signature', signature);
    }

    const url = `${baseUrl}${path}${query.size ? `?${query.toString()}` : ''}`;
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        ...(apiKey ? { 'X-MBX-APIKEY': apiKey } : {})
      }
    });
    return parseResponse(response);
  }

  return { request };
}

async function fetchAllTrades(client) {
  const all = [];
  let fromId = 0n;
  let truncated = false;
  let fallbackRecentOnly = false;

  for (let page = 0; page < MAX_TRADE_PAGES; page += 1) {
    let rows;
    try {
      rows = await client.request('/api/v3/myTrades', {
        symbol: SYMBOL,
        limit: TRADE_PAGE_SIZE,
        fromId: fromId.toString()
      }, true);
    } catch (err) {
      if (page !== 0) throw err;
      rows = await client.request('/api/v3/myTrades', {
        symbol: SYMBOL,
        limit: TRADE_PAGE_SIZE
      }, true);
      fallbackRecentOnly = true;
    }

    if (!Array.isArray(rows)) rows = [];
    all.push(...rows);
    if (fallbackRecentOnly || rows.length < TRADE_PAGE_SIZE) break;

    const last = rows.at(-1);
    if (last == null || last.id == null) break;
    fromId = BigInt(String(last.id)) + 1n;

    if (page === MAX_TRADE_PAGES - 1) truncated = true;
  }

  return { trades: all, truncated, fallbackRecentOnly };
}

function findBalance(account, asset) {
  const row = Array.isArray(account && account.balances)
    ? account.balances.find((b) => String(b.asset) === asset)
    : null;
  const free = Math.max(0, numberOr(row && row.free, 0));
  const locked = Math.max(0, numberOr(row && row.locked, 0));
  return { asset, free, locked, total: free + locked };
}

function mapOpenOrder(order) {
  const origQty = Math.max(0, numberOr(order && order.origQty, 0));
  const executedQty = Math.max(0, numberOr(order && order.executedQty, 0));
  const remainingQty = Math.max(0, origQty - executedQty);
  return {
    orderId: order && order.orderId != null ? String(order.orderId) : null,
    symbol: String((order && order.symbol) || SYMBOL),
    side: String((order && order.side) || ''),
    type: String((order && order.type) || ''),
    status: String((order && order.status) || ''),
    timeInForce: String((order && order.timeInForce) || ''),
    price: numberOr(order && order.price, null),
    stopPrice: numberOr(order && order.stopPrice, null),
    origQty,
    executedQty,
    remainingQty,
    cummulativeQuoteQty: numberOr(order && order.cummulativeQuoteQty, 0),
    time: numberOr(order && order.time, null),
    updateTime: numberOr(order && order.updateTime, null)
  };
}

function permissionSummary(restrictions) {
  if (!restrictions || typeof restrictions !== 'object') {
    return {
      verified: false,
      readOnlySafe: null,
      warning: 'Não foi possível verificar as permissões da chave pela API.'
    };
  }

  const risky = [
    'enableSpotAndMarginTrading',
    'enableMargin',
    'enableFutures',
    'enableVanillaOptions',
    'enableWithdrawals'
  ].filter((key) => restrictions[key] === true);

  return {
    verified: true,
    readOnlySafe: restrictions.enableReading === true && risky.length === 0,
    enableReading: restrictions.enableReading === true,
    riskyEnabled: risky,
    ipRestrict: restrictions.ipRestrict === true
  };
}

async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return json(res, 405, { ok: false, error: 'METHOD_NOT_ALLOWED' });
  }

  const sessionSecret = dashboardPassword();
  if (!sessionSecret || !isSessionAuthorized(req, sessionSecret)) {
    return json(res, 401, {
      ok: false,
      error: 'UNAUTHORIZED',
      message: 'Área Binance bloqueada. Informe a senha privada.'
    });
  }

  const apiKey = String(process.env.BINANCE_API_KEY || '').trim();
  const apiSecret = String(process.env.BINANCE_API_SECRET || '').trim();

  const missing = [];
  if (!apiKey) missing.push('BINANCE_API_KEY');
  if (!apiSecret) missing.push('BINANCE_API_SECRET');
  if (missing.length) {
    return json(res, 503, {
      ok: false,
      error: 'BINANCE_NOT_CONFIGURED',
      missing,
      message: 'API Binance ainda não configurada no Vercel.'
    });
  }

  const baseUrl = String(process.env.BINANCE_BASE_URL || DEFAULT_BASE_URL).replace(/\/$/, '');
  const recvWindow = Math.min(
    60000,
    Math.max(1000, numberOr(process.env.BINANCE_RECV_WINDOW, DEFAULT_RECV_WINDOW))
  );
  const client = createBinanceClient({ apiKey, apiSecret, baseUrl, recvWindow });

  try {
    const [account, ticker, tradePack, openOrdersRaw, restrictionsResult] = await Promise.all([
      client.request('/api/v3/account', { omitZeroBalances: 'true' }, true),
      client.request('/api/v3/ticker/price', { symbol: SYMBOL }, false),
      fetchAllTrades(client),
      client.request('/api/v3/openOrders', { symbol: SYMBOL }, true),
      client.request('/sapi/v1/account/apiRestrictions', {}, true).catch(() => null)
    ]);

    const btc = findBalance(account, BASE_ASSET);
    const usdt = findBalance(account, QUOTE_ASSET);
    const price = numberOr(ticker && ticker.price, NaN);
    const basis = computeCostBasis(tradePack.trades);
    const quantityMismatch = btc.total - basis.quantity;
    const mismatchAbs = Math.abs(quantityMismatch);
    const mismatchTolerance = Math.max(1e-8, btc.total * 0.005);
    // Se o BTC reconstruído pelas execuções coincide com o saldo real da conta,
    // o custo médio é utilizável mesmo quando a Binance devolveu a janela recente
    // sem aceitar paginação por fromId. O saldo real é a reconciliação principal.
    const basisReliable =
      !basis.historyGap &&
      !tradePack.truncated &&
      basis.quantity > 0 &&
      mismatchAbs <= mismatchTolerance;
    const basisAvailable =
      Number.isFinite(basis.averagePrice) &&
      basis.averagePrice > 0 &&
      basis.quantity > 0;

    const averagePrice = basis.averagePrice;
    const unrealizedPnlUsd =
      Number.isFinite(price) && averagePrice != null
        ? btc.total * (price - averagePrice)
        : null;
    const unrealizedPnlPct =
      Number.isFinite(price) && averagePrice > 0
        ? price / averagePrice - 1
        : null;

    const recentTrades = tradePack.trades
      .slice()
      .sort((a, b) => numberOr(b.time) - numberOr(a.time))
      .slice(0, 50)
      .map((t) => ({
        id: t.id == null ? null : String(t.id),
        time: numberOr(t.time, null),
        side: t.isBuyer ? 'buy' : 'sell',
        price: numberOr(t.price, null),
        qty: numberOr(t.qty, null),
        quoteQty: numberOr(t.quoteQty, null),
        commission: numberOr(t.commission, 0),
        commissionAsset: String(t.commissionAsset || '')
      }));

    const openOrders = (Array.isArray(openOrdersRaw) ? openOrdersRaw : [])
      .map(mapOpenOrder)
      .sort((a, b) => numberOr(b.time) - numberOr(a.time));

    return json(res, 200, {
      ok: true,
      source: 'binance-read-only',
      symbol: SYMBOL,
      asOf: new Date().toISOString(),
      execution: {
        region: String(process.env.VERCEL_REGION || process.env.VERCEL_REGION_ID || '').trim() || null
      },
      accountUpdateTime: numberOr(account && account.updateTime, null),
      permissions: permissionSummary(restrictionsResult),
      balances: { BTC: btc, USDT: usdt },
      market: {
        symbol: SYMBOL,
        price: Number.isFinite(price) ? price : null
      },
      costBasis: {
        method: 'average-cost-BTCUSDT',
        averagePrice,
        reconstructedBtc: basis.quantity,
        accountBtc: btc.total,
        quantityMismatch,
        reliable: basisReliable,
        available: basisAvailable,
        quality: basisReliable ? 'reconciled' : (basisAvailable ? 'estimated' : 'unavailable'),
        realizedPnlUsd: basis.realizedPnl,
        unrealizedPnlUsd,
        unrealizedPnlPct,
        historyGap: basis.historyGap,
        tradesTruncated: tradePack.truncated,
        recentOnlyFallback: tradePack.fallbackRecentOnly,
        otherFeeAssets: basis.otherFees
      },
      openOrders,
      trades: recentTrades,
      stats: {
        tradesLoaded: tradePack.trades.length,
        tradesReturned: recentTrades.length,
        openOrders: openOrders.length,
        maxTradesScanned: MAX_TRADE_PAGES * TRADE_PAGE_SIZE
      }
    });
  } catch (err) {
    console.error('Binance read-only error:', err && err.message ? err.message : err);
    return json(res, err && err.status === 429 ? 429 : 502, {
      ok: false,
      error: 'BINANCE_UPSTREAM_ERROR',
      code: err && err.code != null ? err.code : null,
      message: err && err.message ? err.message : 'Falha ao consultar a Binance.'
    });
  }
}

module.exports = handler;
module.exports._test = {
  computeCostBasis,
  permissionSummary,
  mapOpenOrder
};
