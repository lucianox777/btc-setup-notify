const { verifyGithubActionsToken, bearerToken } = require('./_github_oidc.js');
const accountModule = require('./account.js');

const { numberOr, createBinanceClient, findBalance, mapOpenOrder } = accountModule._shared;

const SYMBOL = 'BTCUSDT';
const DEFAULT_BASE_URL = 'https://api.binance.com';
const DEFAULT_RECV_WINDOW = 5000;

function json(res, status, payload) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'private, no-store, max-age=0');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Vary', 'Authorization');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.end(JSON.stringify(payload));
}

async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return json(res, 405, { ok: false, error: 'METHOD_NOT_ALLOWED' });
  }

  try {
    const auth = await verifyGithubActionsToken(bearerToken(req));
    if (!auth.ok) {
      return json(res, 401, {
        ok: false,
        error: 'UNAUTHORIZED_GITHUB_OIDC',
        message: 'Contexto privado disponível apenas para o workflow autorizado.'
      });
    }
  } catch (err) {
    console.error('GitHub OIDC verify error:', err && err.message ? err.message : err);
    return json(res, 502, {
      ok: false,
      error: 'OIDC_VERIFY_ERROR',
      message: 'Não foi possível validar a identidade do workflow.'
    });
  }

  const apiKey = String(process.env.BINANCE_API_KEY || '').trim();
  const apiSecret = String(process.env.BINANCE_API_SECRET || '').trim();
  if (!apiKey || !apiSecret) {
    return json(res, 503, {
      ok: false,
      error: 'BINANCE_NOT_CONFIGURED',
      message: 'Binance não configurada no Vercel.'
    });
  }

  const baseUrl = String(process.env.BINANCE_BASE_URL || DEFAULT_BASE_URL).replace(/\/$/, '');
  const recvWindow = Math.min(
    60000,
    Math.max(1000, numberOr(process.env.BINANCE_RECV_WINDOW, DEFAULT_RECV_WINDOW))
  );
  const client = createBinanceClient({ apiKey, apiSecret, baseUrl, recvWindow });

  try {
    const [account, ticker, openOrdersRaw] = await Promise.all([
      client.request('/api/v3/account', { omitZeroBalances: 'true' }, true),
      client.request('/api/v3/ticker/price', { symbol: SYMBOL }, false),
      client.request('/api/v3/openOrders', { symbol: SYMBOL }, true)
    ]);

    const btc = findBalance(account, 'BTC');
    const usdt = findBalance(account, 'USDT');
    const price = numberOr(ticker && ticker.price, NaN);
    const btcValue = Number.isFinite(price) ? btc.total * price : NaN;
    const accountValue = Number.isFinite(btcValue) ? btcValue + usdt.total : NaN;
    const exposure = Number.isFinite(accountValue) && accountValue > 0
      ? btcValue / accountValue
      : null;

    const openOrders = (Array.isArray(openOrdersRaw) ? openOrdersRaw : []).map(mapOpenOrder);
    const openBuyOrders = openOrders.filter((o) => String(o.side).toUpperCase() === 'BUY').length;
    const openSellOrders = openOrders.filter((o) => String(o.side).toUpperCase() === 'SELL').length;

    return json(res, 200, {
      ok: true,
      source: 'binance-private-notify-context',
      asOf: new Date().toISOString(),
      currentExposurePct: exposure,
      openBuyOrders,
      openSellOrders,
      hasOpenBuyOrder: openBuyOrders > 0,
      hasOpenSellOrder: openSellOrders > 0,
      exposureBasis: 'BTCUSDT-account-balances',
      executionRegion: String(process.env.VERCEL_REGION || process.env.VERCEL_REGION_ID || '').trim() || null
    });
  } catch (err) {
    console.error('Binance notify context error:', err && err.message ? err.message : err);
    return json(res, err && err.status === 429 ? 429 : 502, {
      ok: false,
      error: 'BINANCE_UPSTREAM_ERROR',
      code: err && err.code != null ? err.code : null,
      message: 'Contexto privado da Binance indisponível.'
    });
  }
}

module.exports = handler;
