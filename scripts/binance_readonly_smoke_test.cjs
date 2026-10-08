const assert = require('node:assert/strict');
const fs = require('node:fs');
const api = require('../api/binance/account.js');

const { computeCostBasis, safeEqual, permissionSummary } = api._test;

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

const source = fs.readFileSync('api/binance/account.js', 'utf8');
assert.ok(source.includes('BINANCE_API_KEY'));
assert.ok(source.includes('BINANCE_API_SECRET'));
assert.ok(source.includes('BINANCE_DASHBOARD_TOKEN'));
assert.ok(source.includes("req.method !== 'GET'"));
assert.equal(source.includes('/api/v3/order'), false);
assert.equal(source.includes('/sapi/v1/capital/withdraw/apply'), false);

const html = fs.readFileSync('index.html', 'utf8');
assert.ok(html.includes('/api/binance/account'));
assert.ok(html.includes('binanceRefreshBtn'));
assert.ok(html.includes('Binance · somente leitura'));

console.log('Binance read-only smoke test: PASS');
