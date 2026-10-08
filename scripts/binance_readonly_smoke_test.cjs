const assert = require('node:assert/strict');
const fs = require('node:fs');
const api = require('../api/binance/account.js');
const auth = require('../api/binance/_auth.js');

const { computeCostBasis, permissionSummary, mapOpenOrder } = api._test;
const { safeEqual, createSessionToken, verifySessionToken } = auth;

const trades = [
  { time: 1, isBuyer: true, price: '10000', qty: '0.1', quoteQty: '1000', commission: '0', commissionAsset: 'USDT' },
  { time: 2, isBuyer: true, price: '20000', qty: '0.1', quoteQty: '2000', commission: '0', commissionAsset: 'USDT' },
  { time: 3, isBuyer: false, price: '30000', qty: '0.05', quoteQty: '1500', commission: '0', commissionAsset: 'USDT' }
];

const basis = computeCostBasis(trades);
assert.ok(Math.abs(basis.quantity - 0.15) < 1e-12);
assert.ok(Math.abs(basis.averagePrice - 15000) < 1e-8);
assert.ok(Math.abs(basis.realizedPnl - 750) < 1e-8);
assert.equal(basis.historyGap, false);

const buyFee = computeCostBasis([
  { time: 1, isBuyer: true, price: '10000', qty: '0.1', quoteQty: '1000', commission: '1', commissionAsset: 'USDT' }
]);
assert.ok(Math.abs(buyFee.averagePrice - 10010) < 1e-8);

assert.equal(safeEqual('abc', 'abc'), true);
assert.equal(safeEqual('abc', 'abd'), false);
assert.equal(safeEqual('abc', 'ab'), false);

const now = Date.now();
const session = createSessionToken('senha-forte-teste', now);
assert.equal(verifySessionToken(session.token, 'senha-forte-teste', now + 1000), true);
assert.equal(verifySessionToken(session.token, 'senha-errada', now + 1000), false);
assert.equal(verifySessionToken(session.token, 'senha-forte-teste', session.expiresAtMs + 1), false);

const permsOk = permissionSummary({
  enableReading: true,
  enableSpotAndMarginTrading: false,
  enableMargin: false,
  enableFutures: false,
  enableVanillaOptions: false,
  enableWithdrawals: false,
  ipRestrict: true
});
assert.equal(permsOk.readOnlySafe, true);

const permsBad = permissionSummary({
  enableReading: true,
  enableSpotAndMarginTrading: true
});
assert.equal(permsBad.readOnlySafe, false);

const order = mapOpenOrder({
  orderId: 123,
  symbol: 'BTCUSDT',
  side: 'BUY',
  type: 'LIMIT',
  status: 'NEW',
  timeInForce: 'GTC',
  price: '80000',
  origQty: '0.01',
  executedQty: '0.002',
  cummulativeQuoteQty: '160',
  time: 1000,
  updateTime: 2000
});
assert.equal(order.orderId, '123');
assert.ok(Math.abs(order.remainingQty - 0.008) < 1e-12);

const accountSource = fs.readFileSync('api/binance/account.js', 'utf8');
const authSource = fs.readFileSync('api/binance/_auth.js', 'utf8');
const sessionSource = fs.readFileSync('api/binance/session.js', 'utf8');

assert.ok(accountSource.includes('BINANCE_API_KEY'));
assert.ok(accountSource.includes('BINANCE_API_SECRET'));
assert.ok(accountSource.includes("/api/v3/openOrders"));
assert.ok(accountSource.includes('isSessionAuthorized'));
assert.equal(accountSource.includes("headers.authorization"), false);
assert.equal(accountSource.includes('/api/v3/order'), false);
assert.equal(accountSource.includes('/sapi/v1/capital/withdraw/apply'), false);

assert.ok(authSource.includes('HttpOnly'));
assert.ok(authSource.includes('Secure'));
assert.ok(authSource.includes('SameSite=Strict'));
assert.ok(authSource.includes('BINANCE_DASHBOARD_PASSWORD'));
assert.ok(sessionSource.includes("req.method !== 'POST'"));
assert.ok(sessionSource.includes("req.method === 'DELETE'"));

const html = fs.readFileSync('index.html', 'utf8');
assert.ok(html.includes('PATCH_V204_28_BINANCE_GRU1'));
assert.ok(html.includes('PATCH_V204_27_BINANCE_PRIVATE_SESSION_OPEN_ORDERS'));
assert.ok(html.includes('/api/binance/session'));
assert.ok(html.includes('/api/binance/account'));
assert.ok(html.includes('binanceLoginBtn'));
assert.ok(html.includes('binanceLogoutBtn'));
assert.ok(html.includes('binanceOpenOrdersBox'));
assert.ok(html.includes('Área privada · Binance'));
assert.ok(html.includes('Ordens abertas · BTCUSDT'));
assert.equal(html.includes('BINANCE_DASHBOARD_TOKEN_SESSION_KEY_V20426'), false);
assert.equal(html.includes('binanceDashboardToken'), false);
assert.equal(html.includes('value="4163.41"'), false);

const vercel = JSON.parse(fs.readFileSync('vercel.json', 'utf8'));
assert.deepEqual(vercel.functions?.['api/binance/account.js']?.regions, ['gru1']);
assert.deepEqual(vercel.functions?.['api/binance/session.js']?.regions, ['gru1']);
assert.ok(accountSource.includes('process.env.VERCEL_REGION'));

console.log('Binance private read-only smoke test: PASS');
