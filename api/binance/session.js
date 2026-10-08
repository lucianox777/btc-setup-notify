const {
  safeEqual,
  dashboardPassword,
  ttlSeconds,
  createSessionToken,
  isSessionAuthorized,
  setSessionCookie,
  clearSessionCookie,
  json
} = require('./_auth.js');

function readBody(req) {
  if (req && req.body && typeof req.body === 'object') return req.body;
  if (typeof (req && req.body) === 'string') {
    try { return JSON.parse(req.body); } catch (_) { return {}; }
  }
  return {};
}

async function delay(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function handler(req, res) {
  const secret = dashboardPassword();

  if (!secret) {
    return json(res, 503, {
      ok: false,
      authenticated: false,
      error: 'BINANCE_PASSWORD_NOT_CONFIGURED',
      message: 'Senha privada da área Binance ainda não foi configurada no Vercel.'
    });
  }

  if (req.method === 'GET') {
    return json(res, 200, {
      ok: true,
      authenticated: isSessionAuthorized(req, secret)
    });
  }

  if (req.method === 'DELETE') {
    clearSessionCookie(res);
    return json(res, 200, { ok: true, authenticated: false });
  }

  if (req.method !== 'POST') {
    res.setHeader('Allow', 'GET, POST, DELETE');
    return json(res, 405, { ok: false, error: 'METHOD_NOT_ALLOWED' });
  }

  const body = readBody(req);
  const supplied = String(body.password || '');

  if (!safeEqual(supplied, secret)) {
    await delay(350);
    clearSessionCookie(res);
    return json(res, 401, {
      ok: false,
      authenticated: false,
      error: 'INVALID_PASSWORD',
      message: 'Senha inválida.'
    });
  }

  const created = createSessionToken(secret);
  setSessionCookie(res, created.token, ttlSeconds());

  return json(res, 200, {
    ok: true,
    authenticated: true,
    expiresAt: new Date(created.expiresAtMs).toISOString()
  });
}

module.exports = handler;
