const crypto = require('node:crypto');

const COOKIE_NAME = 'btc_binance_ro_session';
const SESSION_PURPOSE = 'btc-binance-readonly-v1';
const DEFAULT_TTL_SECONDS = 30 * 60;
const MAX_TTL_SECONDS = 12 * 60 * 60;

function safeEqual(a, b) {
  const aa = Buffer.from(String(a || ''), 'utf8');
  const bb = Buffer.from(String(b || ''), 'utf8');
  return aa.length === bb.length && crypto.timingSafeEqual(aa, bb);
}

function dashboardPassword() {
  return String(
    process.env.BINANCE_DASHBOARD_PASSWORD ||
    process.env.BINANCE_DASHBOARD_TOKEN ||
    ''
  ).trim();
}

function ttlSeconds() {
  const raw = Number(process.env.BINANCE_SESSION_TTL_SECONDS || DEFAULT_TTL_SECONDS);
  if (!Number.isFinite(raw)) return DEFAULT_TTL_SECONDS;
  return Math.max(300, Math.min(MAX_TTL_SECONDS, Math.floor(raw)));
}

function sign(secret, expiresAtMs) {
  return crypto
    .createHmac('sha256', secret)
    .update(`${SESSION_PURPOSE}|${expiresAtMs}`)
    .digest('hex');
}

function createSessionToken(secret, nowMs = Date.now()) {
  const expiresAtMs = nowMs + ttlSeconds() * 1000;
  const signature = sign(secret, expiresAtMs);
  return {
    token: `${expiresAtMs}.${signature}`,
    expiresAtMs
  };
}

function verifySessionToken(token, secret, nowMs = Date.now()) {
  if (!token || !secret) return false;
  const [expiresRaw, signature, extra] = String(token).split('.');
  if (extra !== undefined || !expiresRaw || !signature) return false;
  const expiresAtMs = Number(expiresRaw);
  if (!Number.isFinite(expiresAtMs) || expiresAtMs <= nowMs) return false;
  if (expiresAtMs - nowMs > MAX_TTL_SECONDS * 1000 + 60_000) return false;
  return safeEqual(signature, sign(secret, expiresAtMs));
}

function parseCookies(req) {
  const raw = String((req && req.headers && req.headers.cookie) || '');
  const out = {};
  for (const part of raw.split(';')) {
    const idx = part.indexOf('=');
    if (idx < 0) continue;
    const key = part.slice(0, idx).trim();
    const value = part.slice(idx + 1).trim();
    if (!key) continue;
    try { out[key] = decodeURIComponent(value); }
    catch (_) { out[key] = value; }
  }
  return out;
}

function isSessionAuthorized(req, secret = dashboardPassword()) {
  if (!secret) return false;
  const cookies = parseCookies(req);
  return verifySessionToken(cookies[COOKIE_NAME], secret);
}

function cookieBase() {
  return `${COOKIE_NAME}=`;
}

function setSessionCookie(res, token, maxAgeSeconds) {
  res.setHeader(
    'Set-Cookie',
    `${cookieBase()}${encodeURIComponent(token)}; Path=/api/binance; Max-Age=${maxAgeSeconds}; HttpOnly; Secure; SameSite=Strict`
  );
}

function clearSessionCookie(res) {
  res.setHeader(
    'Set-Cookie',
    `${cookieBase()}; Path=/api/binance; Max-Age=0; HttpOnly; Secure; SameSite=Strict`
  );
}

function json(res, status, payload) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'private, no-store, max-age=0');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Vary', 'Cookie');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.end(JSON.stringify(payload));
}

module.exports = {
  COOKIE_NAME,
  safeEqual,
  dashboardPassword,
  ttlSeconds,
  createSessionToken,
  verifySessionToken,
  isSessionAuthorized,
  setSessionCookie,
  clearSessionCookie,
  json
};
