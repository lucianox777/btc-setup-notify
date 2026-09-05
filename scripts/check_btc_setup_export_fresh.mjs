import fs from 'node:fs';
import path from 'node:path';

const repoRoot = process.cwd();
const exportCsvPath = path.resolve(repoRoot, process.env.EXPORT_CSV_PATH || 'data/btc_setup_export.csv');
const forceExport = String(process.env.FORCE_EXPORT || 'false').toLowerCase() === 'true';

// Campos que o notify precisa para validar a regra completa de capitulação 25%.
const REQUIRED_EXPORT_COLUMNS = [
  'vol_mult_ma90',
  'vol14',
  'dd60',
  'ret7',
  'ret30',
  'volume_status',
];

function utcDateOnly(d = new Date()) {
  return d.toISOString().slice(0, 10);
}

function addDays(dateString, days) {
  const d = new Date(`${dateString}T00:00:00.000Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function yesterdayUtc() {
  return addDays(utcDateOnly(), -1);
}

function parseCsvLine(line) {
  const out = [];
  let cur = '';
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (quoted && line[i + 1] === '"') { cur += '"'; i++; }
      else quoted = !quoted;
    } else if (ch === ',' && !quoted) {
      out.push(cur);
      cur = '';
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

function parseCsvSummary(csvText) {
  const lines = csvText.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) throw new Error('CSV exportado vazio ou sem dados.');

  const header = parseCsvLine(lines[0]).map(s => s.trim().toLowerCase());
  const dateIdx = header.indexOf('date') >= 0 ? header.indexOf('date') : 0;
  const lastCols = parseCsvLine(lines.at(-1));
  const lastDate = lastCols[dateIdx]?.trim()?.slice(0, 10);

  if (!/^\d{4}-\d{2}-\d{2}$/.test(lastDate)) {
    throw new Error(`Última data inválida no CSV: ${lastDate}`);
  }

  const missingColumns = REQUIRED_EXPORT_COLUMNS.filter(col => !header.includes(col));

  return {
    lastDate,
    rowCount: lines.length - 1,
    header,
    missingColumns,
  };
}

function setOutput(key, value) {
  const out = process.env.GITHUB_OUTPUT;
  if (out) fs.appendFileSync(out, `${key}=${value}\n`);
}

const requiredDate = yesterdayUtc();
let needsExport = true;
let reason = '';
let lastDate = '';
let rowCount = 0;
let missingColumns = [];

try {
  if (forceExport) {
    needsExport = true;
    reason = 'execução manual com force=true';
  } else if (!fs.existsSync(exportCsvPath)) {
    needsExport = true;
    reason = `${path.relative(repoRoot, exportCsvPath)} ainda não existe`;
  } else {
    const parsed = parseCsvSummary(fs.readFileSync(exportCsvPath, 'utf8'));
    lastDate = parsed.lastDate;
    rowCount = parsed.rowCount;
    missingColumns = parsed.missingColumns;

    if (missingColumns.length) {
      needsExport = true;
      reason = `CSV está atualizado em data, mas schema antigo: faltam colunas ${missingColumns.join(', ')}`;
    } else if (String(lastDate).localeCompare(requiredDate) < 0) {
      needsExport = true;
      reason = `CSV defasado: ${lastDate} < ${requiredDate}`;
    } else {
      needsExport = false;
      reason = `CSV já está atualizado e com schema válido: ${lastDate} >= ${requiredDate}`;
    }
  }
} catch (err) {
  needsExport = true;
  reason = `não foi possível validar CSV existente: ${err?.message || String(err)}`;
}

console.log(`Última data exportada: ${lastDate || '—'}`);
console.log(`Linhas exportadas: ${rowCount || '—'}`);
console.log(`Data mínima exigida: ${requiredDate}`);
console.log(`Colunas obrigatórias: ${REQUIRED_EXPORT_COLUMNS.join(', ')}`);
console.log(`Colunas faltantes: ${missingColumns.length ? missingColumns.join(', ') : 'nenhuma'}`);
console.log(`Precisa exportar: ${needsExport ? 'sim' : 'não'} — ${reason}`);

setOutput('needs_export', needsExport ? 'true' : 'false');
setOutput('last_date', lastDate || '');
setOutput('required_date', requiredDate);
setOutput('missing_columns', missingColumns.join(','));
setOutput('reason', reason.replace(/\r?\n/g, ' '));
