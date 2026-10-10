const crypto = require('node:crypto');

const ISSUER = 'https://token.actions.githubusercontent.com';
const JWKS_URL = ISSUER + '/.well-known/jwks';
const EXPECTED_AUDIENCE = 'btc-setup-notify-vercel';
const EXPECTED_REPOSITORY = 'lucianox777/btc-setup-notify';
const EXPECTED_REF = 'refs/heads/main';
const EXPECTED_WORKFLOW_REF =
  'lucianox777/btc-setup-notify/.github/workflows/btc_notify.yml@refs/heads/main';

let jwksCache = { at: 0, keys: [] };

function b64urlJson(part) {
  const txt = Buffer.from(String(part || ''), 'base64url').toString('utf8');
  return JSON.parse(txt);
}

function audienceMatches(aud) {
  if (Array.isArray(aud)) return aud.includes(EXPECTED_AUDIENCE);
  return String(aud || '') === EXPECTED_AUDIENCE;
}

function validateClaims(payload, nowSeconds = Math.floor(Date.now() / 1000)) {
  if (!payload || typeof payload !== 'object') return { ok: false, reason: 'payload ausente' };
  if (payload.iss !== ISSUER) return { ok: false, reason: 'issuer inválido' };
  if (!audienceMatches(payload.aud)) return { ok: false, reason: 'audience inválida' };
  if (payload.repository !== EXPECTED_REPOSITORY) return { ok: false, reason: 'repository inválido' };
  if (payload.ref !== EXPECTED_REF) return { ok: false, reason: 'ref inválida' };
  if (payload.workflow_ref !== EXPECTED_WORKFLOW_REF) return { ok: false, reason: 'workflow inválido' };

  const exp = Number(payload.exp);
  const nbf = Number(payload.nbf || 0);
  const iat = Number(payload.iat || 0);
  const skew = 90;
  if (!Number.isFinite(exp) || exp < nowSeconds - skew) return { ok: false, reason: 'token expirado' };
  if (Number.isFinite(nbf) && nbf > nowSeconds + skew) return { ok: false, reason: 'token ainda não válido' };
  if (Number.isFinite(iat) && iat > nowSeconds + skew) return { ok: false, reason: 'iat futuro' };

  return { ok: true };
}

async function getJwks() {
  const now = Date.now();
  if (jwksCache.keys.length && now - jwksCache.at < 60 * 60 * 1000) return jwksCache.keys;
  const response = await fetch(JWKS_URL, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`GitHub OIDC JWKS HTTP ${response.status}`);
  const body = await response.json();
  const keys = Array.isArray(body && body.keys) ? body.keys : [];
  if (!keys.length) throw new Error('GitHub OIDC JWKS vazio');
  jwksCache = { at: now, keys };
  return keys;
}

async function verifyGithubActionsToken(token) {
  const parts = String(token || '').split('.');
  if (parts.length !== 3) return { ok: false, reason: 'JWT inválido' };

  let header;
  let payload;
  try {
    header = b64urlJson(parts[0]);
    payload = b64urlJson(parts[1]);
  } catch (_) {
    return { ok: false, reason: 'JWT ilegível' };
  }

  if (header.alg !== 'RS256' || !header.kid) return { ok: false, reason: 'alg/kid inválido' };
  const claims = validateClaims(payload);
  if (!claims.ok) return claims;

  const keys = await getJwks();
  const jwk = keys.find((x) => x && x.kid === header.kid);
  if (!jwk) return { ok: false, reason: 'kid desconhecido' };

  const key = crypto.createPublicKey({ key: jwk, format: 'jwk' });
  const verified = crypto.verify(
    'RSA-SHA256',
    Buffer.from(parts[0] + '.' + parts[1], 'utf8'),
    key,
    Buffer.from(parts[2], 'base64url')
  );
  if (!verified) return { ok: false, reason: 'assinatura inválida' };

  return { ok: true, payload };
}

function bearerToken(req) {
  const raw = String((req && req.headers && req.headers.authorization) || '');
  return raw.startsWith('Bearer ') ? raw.slice(7).trim() : '';
}

module.exports = {
  EXPECTED_AUDIENCE,
  validateClaims,
  verifyGithubActionsToken,
  bearerToken
};
