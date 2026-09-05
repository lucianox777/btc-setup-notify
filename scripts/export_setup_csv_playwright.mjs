import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import http from 'node:http';

const htmlPath = process.env.SETUP_HTML_PATH || 'setup_v204_tail_coinmetrics_price_mvrv.html';
const csvPath = process.env.EXPORT_CSV_PATH || 'data/btc_setup_export.csv';
const metaPath = process.env.EXPORT_META_PATH || 'data/btc_setup_export_meta.json';
const requireFreshUtc = String(process.env.REQUIRE_YESTERDAY_UTC || process.env.REQUIRE_FRESH_DAILY_UTC || '').toLowerCase() === 'true';
const requireNoBinanceTail = String(process.env.REQUIRE_NO_BINANCE_TAIL || '').toLowerCase() === 'true';
const blockBinanceVisual = String(process.env.BLOCK_BINANCE_VISUAL_IN_EXPORT || 'true').toLowerCase() === 'true';
const freshnessGraceUtcHourRaw = Number(process.env.FRESHNESS_GRACE_UTC_HOUR || 15);
const freshnessGraceUtcHour = Number.isFinite(freshnessGraceUtcHourRaw)
  ? Math.max(0, Math.min(23, Math.trunc(freshnessGraceUtcHourRaw)))
  : 15;

function die(message) {
  console.error(`::error::${message}`);
  process.exit(1);
}

function isoDateUtcDaysAgo(daysAgo, base = new Date()) {
  const d = new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth(), base.getUTCDate()));
  d.setUTCDate(d.getUTCDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

function requiredMinLastDateUtc(now = new Date()) {
  // Antes do horário de tolerância, a diária D-1 ainda pode não ter sido publicada.
  // Ex.: 04/09 00:13 UTC aceita 02/09. A partir de 15:00 UTC exige 03/09.
  const daysAgo = now.getUTCHours() < freshnessGraceUtcHour ? 2 : 1;
  return isoDateUtcDaysAgo(daysAgo, now);
}

function parseCsvLine(line) {
  const out = [];
  let cur = '';
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '"') {
      if (quoted && line[i + 1] === '"') {
        cur += '"';
        i += 1;
      } else {
        quoted = !quoted;
      }
    } else if (ch === ',' && !quoted) {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

function rowObject(header, values) {
  const obj = {};
  for (let i = 0; i < header.length; i += 1) obj[header[i]] = values[i] ?? '';
  return obj;
}

// Exportador rígido: usa exatamente SETUP_HTML_PATH; sem fallback de nome/caminho.

function contentTypeFor(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === '.html') return 'text/html; charset=utf-8';
  if (ext === '.js') return 'text/javascript; charset=utf-8';
  if (ext === '.css') return 'text/css; charset=utf-8';
  if (ext === '.json') return 'application/json; charset=utf-8';
  if (ext === '.csv') return 'text/csv; charset=utf-8';
  if (ext === '.png') return 'image/png';
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  return 'application/octet-stream';
}

async function startStaticServer(rootDir) {
  const server = http.createServer(async (req, res) => {
    try {
      const rawPath = decodeURIComponent(new URL(req.url || '/', 'http://127.0.0.1').pathname);
      const relative = rawPath.replace(/^\/+/, '') || 'index.html';
      const requested = path.resolve(rootDir, relative);
      if (!requested.startsWith(rootDir + path.sep) && requested !== rootDir) {
        res.writeHead(403);
        res.end('Forbidden');
        return;
      }
      if (!existsSync(requested)) {
        res.writeHead(404);
        res.end('Not found');
        return;
      }
      const stat = await fs.stat(requested);
      if (!stat.isFile()) {
        res.writeHead(404);
        res.end('Not found');
        return;
      }
      res.writeHead(200, { 'content-type': contentTypeFor(requested), 'cache-control': 'no-store' });
      res.end(await fs.readFile(requested));
    } catch (err) {
      res.writeHead(500);
      res.end(String(err?.message || err));
    }
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Servidor local não retornou porta válida.');
  return { server, port: address.port };
}

const resolvedHtmlPath = htmlPath;
const absHtml = path.resolve(resolvedHtmlPath);
if (!existsSync(absHtml)) die(`HTML não encontrado: ${resolvedHtmlPath}`);

await fs.mkdir(path.dirname(csvPath), { recursive: true });
await fs.mkdir(path.dirname(metaPath), { recursive: true });

const rootDir = path.resolve(process.cwd());
const { server, port } = await startStaticServer(rootDir);
const browser = await chromium.launch({
  headless: true,
  args: ['--disable-dev-shm-usage']
});

const context = await browser.newContext({
  acceptDownloads: true,
  viewport: { width: 1600, height: 1400 }
});

await context.addInitScript(() => {
  try { localStorage.clear(); } catch (_) {}
});

const page = await context.newPage();
page.on('console', (msg) => console.log(`[page:${msg.type()}] ${msg.text()}`));
page.on('pageerror', (err) => console.error(`[pageerror] ${err.message}`));
page.on('response', (response) => {
  const url = response.url();
  if (url.includes('coinmetrics') || url.includes('raw.githubusercontent') || url.includes('bgeometrics')) {
    console.log(`[${new Date().toISOString()}] response: ${response.status()} ${url}`);
  }
});
page.on('requestfailed', (request) => {
  const url = request.url();
  if (url.includes('coinmetrics') || url.includes('raw.githubusercontent') || url.includes('binance')) {
    console.log(`[${new Date().toISOString()}] requestfailed: ${request.method()} ${url} :: ${request.failure()?.errorText || 'unknown'}`);
  }
});

let binanceVisualBlocked = 0;
const binanceVisualBlockedSamples = [];
if (blockBinanceVisual) {
  await page.route('**/*', async (route) => {
    const req = route.request();
    let host = '';
    try { host = new URL(req.url()).hostname.toLowerCase(); } catch (_) {}
    if (host.includes('binance.com') || host.includes('binancefuture.com') || host.includes('binance.us')) {
      binanceVisualBlocked += 1;
      if (binanceVisualBlockedSamples.length < 12) {
        binanceVisualBlockedSamples.push({ method: req.method(), url: req.url() });
      }
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
}

let pageState = null;
try {
  const relativeHtmlUrlPath = resolvedHtmlPath.split(path.sep).map(encodeURIComponent).join('/');
  const setupUrl = `http://127.0.0.1:${port}/${relativeHtmlUrlPath}`;
  console.log(`Abrindo setup via servidor local: ${setupUrl}`);
  await page.goto(setupUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // v5: o botão de exportação existe no DOM, mas fica dentro do painel recolhido.
  // Portanto, espere apenas o elemento existir, sem exigir visibilidade.
  await page.waitForSelector('#exportCsv', { state: 'attached', timeout: 60000 });

  await page.evaluate(() => {
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.value = value;
    };
    set('autoLoad', 'yes');
    set('intradayAuto', 'no');
    set('pj1hAuto', 'no');
    set('liveAuto', 'no');
    set('sthAuto', 'no');
    set('lthAuto', 'no');
  });

  const loadAction = await page.evaluate(() => {
    const force = document.getElementById('forceCsvBtn');
    if (!force) return null;
    force.click();
    return 'forceCsvBtn.click()';
  });
  if (!loadAction) die('Não encontrei o botão obrigatório de carregamento BTC CSV: #forceCsvBtn.');
  console.log(`Ação de carga acionada: ${loadAction}`);

  await page.waitForFunction(() => {
    try {
      return typeof data !== 'undefined'
        && Array.isArray(data)
        && data.length > 1000
        && data[data.length - 1]
        && data[data.length - 1].date
        && Number.isFinite(Number(data[data.length - 1].price));
    } catch (_) {
      return false;
    }
  }, { timeout: 240000 });

  pageState = await page.evaluate(() => {
    const last = data[data.length - 1] || {};
    let tail = null;
    try { tail = typeof btcCsvApiTailInfo !== 'undefined' ? btcCsvApiTailInfo : null; } catch (_) {}
    const read = (id) => {
      try { return document.getElementById(id)?.textContent || null; } catch (_) { return null; }
    };
    return {
      rows: data.length,
      lastDate: last.date || null,
      lastPrice: last.price ?? null,
      fairRatio: last.fairRatio ?? null,
      signal: last.signal ?? null,
      volumeStatus: last._cmVolumeStatus || last.volume_status || null,
      statusText: read('statusText'),
      sourceText: read('sourceText'),
      tail
    };
  });
  console.log('Status:', pageState.statusText || '(sem status)');
  console.log('Fonte:', pageState.sourceText || '(sem fonte)');
  console.log('Loaded state:', JSON.stringify(pageState));
  console.log(`Requisições Binance bloqueadas como visual/intradiário: ${binanceVisualBlocked}`);

  // v5: não clica no botão escondido. Extrai o mesmo CSV diretamente do estado calculado da página.
  const csvText = await page.evaluate(() => {
    if (typeof data === 'undefined' || !Array.isArray(data) || !data.length) {
      throw new Error('data[] indisponível para exportação direta.');
    }
    const n = (v) => {
      try {
        if (typeof num === 'function') return num(v);
      } catch (_) {}
      const x = Number(String(v ?? '').replace(',', '.'));
      return Number.isFinite(x) ? x : NaN;
    };
    const elVal = (id, fallback = '') => {
      try { return document.getElementById(id)?.value ?? fallback; } catch (_) { return fallback; }
    };
    const safe = (fn, fallback = '') => {
      try {
        const v = fn();
        return v == null ? fallback : v;
      } catch (_) {
        return fallback;
      }
    };
    const safeFn = (name) => {
      try {
        // eslint-disable-next-line no-eval
        const f = eval(name);
        return typeof f === 'function' ? f : null;
      } catch (_) {
        return null;
      }
    };
    const fnGetVflTurnInfo = safeFn('getVflTurnInfo');
    const fnExecutionCap = safeFn('v2044ExecutionCapForExport');
    const fnCurrentExposure = safeFn('v2044CurrentExposurePct');
    const fnExecutionNote = safeFn('v2044ExecutionNoteForExport');
    const fnRecomposition = safeFn('v2047RecompositionExportNote');
    const fnTactical = safeFn('getTacticalStateDecision');

    let volumeSourceColVal = null;
    try { volumeSourceColVal = volumeSourceCol; } catch (_) {}

    const header = [
      'date', 'price_usd', 'mvrv', 'vfl_ratio', 'vfl_signal', 'vfl_confidence',
      'fair_ratio', 'fair_price_usd', 'sth_realized_price_usd', 'sth_ratio', 'sth_confirmation',
      'lth_realized_price_usd', 'lth_ratio', 'lth_confirmation', 'cap_real_usd', 'cap_real_30d_usd',
      'cap_real_30d_pct', 'capital_flow_label', 'rsi', 'rsi_capitulation', 'rsi_alert_reference_exposure',
      'fair_buy_price_usd', 'fair_sell_price_usd', 'fair_reversal_price_usd',
      'vfl_regime_price_usd', 'vfl_regime_direction', 'fair_zone', 'combined_signal',
      'target_exposure', 'macro_exposure', 'execution_cap', 'current_exposure_pct',
      'execution_note', 'recomposition_note', 'visual_alert_signal', 'visual_alert_exposure',
      'macro_exposure_with_visual_alerts', 'strategy_equity', 'visual_alert_equity_reference',
      'buy_hold_equity', 'vol_mult_ma90', 'vol14', 'dd60', 'ret7', 'ret30', 'volume_status'
    ];

    const lastIndex = data.length - 1;
    const lines = [header.join(',')];
    for (let i = 0; i < data.length; i += 1) {
      const d = data[i] || {};
      const isLast = i === lastIndex;
      const turn = isLast && fnGetVflTurnInfo ? safe(() => fnGetVflTurnInfo(d, d.price), null) : null;
      const fairBottom = n(elVal('fairBottom', '0.960'));
      const fairTop = n(elVal('fairTop', '1.050'));
      const fairRev = n(elVal('fairRev', '0.930'));
      const volumeStatus = d._cmVolumeStatus || d.volume_status || ((volumeSourceColVal && Number.isFinite(d.volume) && d.volume > 0) ? 'real' : 'missing');
      const tactical = fnTactical ? safe(() => fnTactical(d, d.tacticalExposure || 0), {}) : {};
      const row = [
        d.date,
        d.price,
        d.mvrv,
        d.vflRatio,
        d.signal,
        d.vflConf?.label || '',
        d.fairRatio,
        d.fairPrice,
        d.sthPrice,
        d.sthRatio,
        d.sthConfirm?.label || '',
        d.lthPrice,
        d.lthRatio,
        d.lthConfirm?.label || '',
        d.capReal,
        d.capReal30d,
        d.capReal30dPct,
        d.capFlow?.label || '',
        d.rsi,
        d.rsiCap?.label || '',
        d.rsiCapExposure,
        Number.isFinite(d.fairPrice) && Number.isFinite(fairBottom) ? d.fairPrice * fairBottom : '',
        Number.isFinite(d.fairPrice) && Number.isFinite(fairTop) ? d.fairPrice * fairTop : '',
        Number.isFinite(d.fairPrice) && Number.isFinite(fairRev) ? d.fairPrice * fairRev : '',
        isLast ? (turn?.turnPrice || '') : '',
        isLast ? (turn?.directionShort || '') : '',
        d.fairZone?.label || '',
        d.combined?.label || '',
        d.combined?.exposure ?? '',
        d.combined?.exposure ?? '',
        fnExecutionCap ? safe(() => fnExecutionCap(d), '') : '',
        isLast && fnCurrentExposure ? safe(() => fnCurrentExposure(), '') : '',
        fnExecutionNote ? safe(() => fnExecutionNote(d), '') : '',
        fnRecomposition ? safe(() => fnRecomposition(d), '') : '',
        tactical?.label || '',
        d.tacticalExposure,
        d.totalExposureWithTactical,
        d.eq,
        d.eqTac,
        d.bh,
        Number.isFinite(d.volMult) ? d.volMult.toFixed(4) : '',
        Number.isFinite(d.vol14) ? d.vol14.toFixed(4) : '',
        Number.isFinite(d.dd60) ? d.dd60.toFixed(6) : '',
        Number.isFinite(d.ret7) ? d.ret7.toFixed(6) : '',
        Number.isFinite(d.ret30) ? d.ret30.toFixed(6) : '',
        volumeStatus
      ];
      lines.push(row.map((v) => String(v ?? '').replace(/,/g, '.')).join(','));
    }
    return lines.join('\n') + '\n';
  });

  await fs.writeFile(csvPath, csvText, 'utf8');
  console.log(`Saved CSV directly from page state: ${csvPath}`);
} catch (err) {
  await page.screenshot({ path: 'data/btc_setup_export_error.png', fullPage: true }).catch(() => {});
  throw err;
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}

const csv = await fs.readFile(csvPath, 'utf8');
const lines = csv.trim().split(/\r?\n/).filter(Boolean);
if (lines.length < 2) die(`CSV exportado sem linhas suficientes: ${lines.length}`);

const header = parseCsvLine(lines[0]);
const last = rowObject(header, parseCsvLine(lines[lines.length - 1]));
const lastDate = String(last.date || '').slice(0, 10);
if (!/^\d{4}-\d{2}-\d{2}$/.test(lastDate)) die(`Última data inválida no CSV exportado: ${last.date || '(vazio)'}`);

const now = new Date();
const minLastDate = requireFreshUtc ? requiredMinLastDateUtc(now) : null;
const freshnessMode = requireFreshUtc
  ? (now.getUTCHours() < freshnessGraceUtcHour ? 'grace_accepts_d_minus_2' : 'strict_requires_d_minus_1')
  : 'disabled';

if (requireFreshUtc && lastDate < minLastDate) {
  die(
    `CSV exportado está defasado: última data ${lastDate}, exigido >= ${minLastDate} UTC. `
    + `Modo=${freshnessMode}; hora UTC=${now.toISOString()}; grace até ${String(freshnessGraceUtcHour).padStart(2, '0')}:00 UTC. `
    + 'Se estiver antes da publicação diária da CoinMetrics, rode novamente após a atualização.'
  );
}

if (requireNoBinanceTail) {
  const status = String(last.volume_status || last._cmVolumeStatus || '').toLowerCase();
  const bad = status.includes('binance') || status.includes('synthetic') || status.includes('missing');
  if (bad) die(`Volume/tail não aprovado na última linha: volume_status=${last.volume_status || last._cmVolumeStatus || '(vazio)'}`);
}

const meta = {
  generatedAt: now.toISOString(),
  exporter: 'playwright-v7-strict-hidden-controls-direct-csv-meta-fix',
  htmlPath: resolvedHtmlPath,
  csvPath,
  rows: lines.length - 1,
  columns: header,
  last,
  pageState,
  checks: {
    requireFreshDailyUtc: requireFreshUtc,
    compatibilityEnvRequireYesterdayUtc: requireFreshUtc,
    freshnessGraceUtcHour,
    freshnessMode,
    requiredMinLastDateUtc: minLastDate,
    requireNoBinanceTail,
    blockBinanceVisualInExport: blockBinanceVisual,
    binanceVisualIntradayBlocked: binanceVisualBlocked,
    binanceVisualIntradayBlockedSamples: binanceVisualBlockedSamples,
    passed: true
  }
};

await fs.writeFile(metaPath, JSON.stringify(meta, null, 2) + '\n', 'utf8');
console.log(`Saved meta: ${metaPath}`);
console.log(JSON.stringify({
  rows: meta.rows,
  lastDate,
  requiredMinLastDateUtc: minLastDate,
  freshnessMode,
  volume_status: last.volume_status || last._cmVolumeStatus || null,
  binance_visual_intraday_blocked: binanceVisualBlocked,
  exporter: meta.exporter
}, null, 2));
