#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC intraday lines watcher — PJ/FR 1h.

Uso no GitHub Actions, sem navegador/Playwright:
- busca candles BTCUSDT 1h fechados na Binance;
- replica a lógica do HTML Setup v204 para PJ 1h:
  auto-calibração por âncoras + candidatos de EMA, fallback EMA96h;
- busca preço atual BTCUSDT;
- calcula PJ agora, FR agora, linhas 1,05 / 0,96 / 0,93;
- compara com o estado horário anterior;
- envia Telegram apenas quando entra em ACIMA_VERDE ou ABAIXO_DOURADA.

Requer secrets no workflow apenas quando houver alerta:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCRIPT_VERSION = "btc_intraday_lines_watch_v1.0"
MS_HOUR = 3_600_000

# Mesmas âncoras/candidatos usados pelo HTML para calibrar visualmente o PJ 1h.
# JS Date.UTC usa mês zero-based. Aqui usamos timestamp UTC explícito.
GLASSNODE_PJ1H_ANCHORS = [
    {"t": int(datetime(2026, 3, 30, 0, 0, tzinfo=timezone.utc).timestamp() * 1000), "ratio": 0.95698638, "label": "30/03 00h"},
    {"t": int(datetime(2026, 4, 9, 11, 0, tzinfo=timezone.utc).timestamp() * 1000), "ratio": 1.03536729, "label": "09/04 11h"},
    {"t": int(datetime(2026, 5, 7, 23, 0, tzinfo=timezone.utc).timestamp() * 1000), "ratio": 1.00003206, "label": "07/05 23h"},
    {"t": int(datetime(2026, 5, 11, 0, 0, tzinfo=timezone.utc).timestamp() * 1000), "ratio": 1.0107859, "label": "11/05 00h"},
    {"t": int(datetime(2026, 5, 14, 18, 0, tzinfo=timezone.utc).timestamp() * 1000), "ratio": 1.01410826, "label": "14/05 18h"},
    {"t": int(datetime(2026, 5, 15, 19, 0, tzinfo=timezone.utc).timestamp() * 1000), "ratio": 0.98292268, "label": "15/05 19h"},
]
PJ1H_EMA_CANDIDATES = [48, 72, 96, 120, 144, 168, 192, 216, 240, 288, 336]


@dataclass
class Candle1h:
    t: int
    close: float
    high: float
    low: float
    volume: float
    quote_volume: float


@dataclass
class Calibration:
    mode: str
    win: int
    mae: Optional[float]
    n: int
    text: str


@dataclass
class Snapshot:
    version: str
    updated_at: str
    symbol: str
    zone: str
    previous_zone: Optional[str]
    subzone: str
    alert_sent: bool
    alert_reason: str
    price_now: float
    fair_price_now: float
    fr_now: float
    line_green_105: float
    line_golden_096: float
    line_blue_093: float
    ema_win: int
    calibration_mode: str
    calibration_text: str
    last_closed_1h_open_time: str
    last_closed_1h_close: float
    last_closed_1h_fair_price: float
    last_closed_1h_fr: float
    binance_base_url: str


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = env_str(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = env_str(name, "")
    try:
        return float(raw.replace(",", ".")) if raw else default
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name, "")
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "sim", "s"}


def iso_utc(ms_or_s: float) -> str:
    if ms_or_s > 10_000_000_000:
        sec = ms_or_s / 1000.0
    else:
        sec = ms_or_s
    return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def br_hour_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d/%m %Hh UTC")


def usd(x: float) -> str:
    if not math.isfinite(x):
        return "US$ —"
    return "US$ " + f"{x:,.0f}"


def n4(x: float) -> str:
    return "—" if not math.isfinite(x) else f"{x:.4f}"


def http_json(url: str, timeout: int = 20, attempts: int = 3) -> Any:
    last_error: Optional[BaseException] = None
    headers = {
        "User-Agent": f"{SCRIPT_VERSION} GitHubActions",
        "Accept": "application/json,text/plain,*/*",
        "Cache-Control": "no-cache",
    }
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"Falha HTTP/JSON em {url}: {last_error}")


def with_base_paths(path: str) -> Iterable[Tuple[str, str]]:
    raw = env_str("BINANCE_BASE_URLS", "https://api.binance.com,https://data-api.binance.vision")
    for base in [x.strip().rstrip("/") for x in raw.split(",") if x.strip()]:
        yield base, base + path


def fetch_binance(path: str) -> Tuple[Any, str]:
    errors = []
    for base, url in with_base_paths(path):
        try:
            return http_json(url), base
        except Exception as exc:  # tenta próximo domínio público
            errors.append(f"{base}: {exc}")
    raise RuntimeError("Todas as bases Binance falharam: " + " | ".join(errors))


def ema(vals: List[float], win: int) -> List[float]:
    out = [math.nan] * len(vals)
    k = 2.0 / (win + 1.0)
    prev = math.nan
    for i, v in enumerate(vals):
        if not math.isfinite(v):
            continue
        if not math.isfinite(prev):
            prev = v
        else:
            prev = v * k + prev * (1.0 - k)
        out[i] = prev
    return out


def nearest_hour_index_by_time(arr: List[Candle1h], t: int, max_diff_ms: int = 2 * MS_HOUR) -> int:
    if not arr:
        return -1
    lo, hi = 0, len(arr) - 1
    while lo < hi:
        mid = (lo + hi) >> 1
        if arr[mid].t < t:
            lo = mid + 1
        else:
            hi = mid
    best = lo
    if lo > 0 and abs(arr[lo - 1].t - t) < abs(arr[lo].t - t):
        best = lo - 1
    return best if abs(arr[best].t - t) <= max_diff_ms else -1


def calibrate_pj1h_ema(all_rows: List[Candle1h], mode: str, manual_win: int) -> Calibration:
    manual_win = max(2, int(manual_win or 96))
    if mode != "auto":
        return Calibration("manual", manual_win, None, 0, f"EMA manual {manual_win}h")

    anchors = [a for a in GLASSNODE_PJ1H_ANCHORS if all_rows[0].t <= a["t"] <= all_rows[-1].t]
    if len(anchors) < 3:
        return Calibration("auto_fallback", manual_win, None, len(anchors), f"Auto sem pontos suficientes; usando EMA manual {manual_win}h")

    closes = [r.close for r in all_rows]
    best: Optional[Calibration] = None
    for win in PJ1H_EMA_CANDIDATES:
        if len(all_rows) < win + 10:
            continue
        fair = ema(closes, win)
        errors: List[float] = []
        for a in anchors:
            idx = nearest_hour_index_by_time(all_rows, int(a["t"]))
            if idx < 0 or not math.isfinite(fair[idx]) or fair[idx] <= 0:
                continue
            calc = all_rows[idx].close / fair[idx]
            errors.append(abs(calc - float(a["ratio"])))
        if len(errors) < 3:
            continue
        mae = sum(errors) / len(errors)
        if best is None or (best.mae is not None and mae < best.mae):
            best = Calibration("auto", win, mae, len(errors), f"EMA {win}h calibrada · erro médio {mae:.4f} · {len(errors)} pontos")
    return best or Calibration("auto_fallback", manual_win, None, 0, f"Auto falhou; usando EMA manual {manual_win}h")


def fetch_closed_1h_candles(symbol: str, days: int, manual_win: int) -> Tuple[List[Candle1h], str]:
    now_ms = int(time.time() * 1000)
    visual_start = now_ms - max(7, days) * 24 * MS_HOUR
    anchor_min = min(a["t"] for a in GLASSNODE_PJ1H_ANCHORS)
    max_win = max(manual_win, *PJ1H_EMA_CANDIDATES)
    start = min(visual_start, anchor_min) - max_win * 4 * MS_HOUR
    end = now_ms
    max_calls = env_int("BINANCE_MAX_KLINE_CALLS", 24)

    all_rows: Dict[int, Candle1h] = {}
    cur = start
    used_base = ""
    calls = 0
    while cur < end and calls < max_calls:
        qs = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": "1h",
                "startTime": int(cur),
                "endTime": int(end),
                "limit": 1000,
            }
        )
        js, base = fetch_binance(f"/api/v3/klines?{qs}")
        used_base = base
        if not isinstance(js, list) or not js:
            break
        for k in js:
            try:
                row = Candle1h(
                    t=int(k[0]),
                    close=float(k[4]),
                    high=float(k[2]),
                    low=float(k[3]),
                    volume=float(k[5]),
                    quote_volume=float(k[7]),
                )
                if row.t > 0 and row.close > 0 and math.isfinite(row.close):
                    all_rows[row.t] = row
            except (ValueError, TypeError, IndexError):
                continue
        last_open = int(js[-1][0])
        cur = last_open + MS_HOUR
        calls += 1
        if len(js) < 1000:
            break

    rows = sorted(all_rows.values(), key=lambda r: r.t)
    closed_cutoff = now_ms - 30_000
    rows = [r for r in rows if r.t + MS_HOUR <= closed_cutoff]
    if len(rows) < max(60, manual_win):
        raise RuntimeError(f"Poucos candles 1h fechados carregados: {len(rows)}")
    return rows, used_base


def fetch_current_price(symbol: str) -> Tuple[float, str]:
    qs = urllib.parse.urlencode({"symbol": symbol})
    js, base = fetch_binance(f"/api/v3/ticker/price?{qs}")
    price = float(js.get("price"))
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError(f"Preço inválido recebido da Binance: {js!r}")
    return price, base


def classify_zone(fr_now: float) -> Tuple[str, str]:
    green = env_float("FR_GREEN", 1.05)
    golden = env_float("FR_GOLDEN", 0.96)
    blue = env_float("FR_BLUE", 0.93)
    if not math.isfinite(fr_now):
        return "INDEFINIDO", "INDEFINIDO"
    if fr_now >= green:
        return "ACIMA_VERDE", "ACIMA_VERDE_1_05"
    if fr_now <= golden:
        if fr_now <= blue:
            return "ABAIXO_DOURADA", "ABAIXO_AZUL_0_93"
        return "ABAIXO_DOURADA", "ABAIXO_DOURADA_0_96"
    return "ENTRE", "ENTRE_0_96_1_05"


def read_state(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_state(path: Path, state: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_message(state: Snapshot) -> str:
    if state.zone == "ACIMA_VERDE":
        title = "🟢 BTC acima da linha verde 1h"
        leitura = "Leitura: zona de redução/não-recompra. Se já está 50%, vender 0."
    elif state.zone == "ABAIXO_DOURADA":
        icon = "🔵" if state.subzone == "ABAIXO_AZUL_0_93" else "🟡"
        title = f"{icon} BTC tocou a linha dourada 1h"
        leitura = "Leitura: zona preferencial de recomposição, se o diário continua permitindo alvo 100%."
    else:
        title = "BTC 1h mudou de zona"
        leitura = "Leitura: voltou para a faixa entre dourada e verde."

    previous = state.previous_zone or "sem estado anterior"
    return "\n".join(
        [
            title,
            "",
            f"Preço: {usd(state.price_now)}",
            f"FR 1h agora: {n4(state.fr_now)}",
            f"🟢 1,05 / redução: {usd(state.line_green_105)}",
            f"🟡 0,96 / recompra: {usd(state.line_golden_096)}",
            f"🔵 0,93 / reversão: {usd(state.line_blue_093)}",
            f"PJ 1h agora: {usd(state.fair_price_now)}",
            "",
            f"Estado: {previous} → {state.zone}",
            f"Última hora fechada: {state.last_closed_1h_open_time}",
            f"EMA: {state.ema_win}h · {state.calibration_mode}",
            "",
            leitura,
            "",
            f"{SCRIPT_VERSION} · GitHub Actions",
        ]
    )


def send_telegram(text: str) -> None:
    token = env_str("TELEGRAM_BOT_TOKEN")
    chat_id = env_str("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Alerta necessário, mas TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID não estão configurados.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
    req.add_header("User-Agent", f"{SCRIPT_VERSION} GitHubActions")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    js = json.loads(raw)
    if not js.get("ok"):
        raise RuntimeError(f"Telegram retornou erro: {raw}")


def main() -> int:
    symbol = env_str("SYMBOL", "BTCUSDT").upper()
    days = env_int("PJ1H_DAYS", 105)
    manual_win = max(2, env_int("PJ1H_MANUAL_EMA", 96))
    mode = env_str("PJ1H_MODE", "auto").lower()
    state_path = Path(env_str("INTRADAY_STATE_PATH", "data/btc_intraday_lines_state.json"))

    rows, kline_base = fetch_closed_1h_candles(symbol, days, manual_win)
    cal = calibrate_pj1h_ema(rows, mode, manual_win)
    win = cal.win or manual_win
    closes = [r.close for r in rows]
    fair_series = ema(closes, win)

    visual_start = int(time.time() * 1000) - max(7, days) * 24 * MS_HOUR
    hour_rows = []
    for row, fair_price in zip(rows, fair_series):
        if row.t >= visual_start and math.isfinite(fair_price) and fair_price > 0:
            hour_rows.append((row, fair_price, row.close / fair_price))
    if not hour_rows:
        raise RuntimeError("Nenhuma linha 1h válida na janela visual.")

    last_row, last_fair, last_fr = hour_rows[-1]
    price_now, price_base = fetch_current_price(symbol)

    k = 2.0 / (win + 1.0)
    fair_now = price_now * k + last_fair * (1.0 - k)
    fr_now = price_now / fair_now if fair_now > 0 else math.nan

    zone, subzone = classify_zone(fr_now)
    previous_state = read_state(state_path)
    previous_zone = previous_state.get("zone") if previous_state else None

    first_run = previous_zone is None
    changed_zone = first_run or zone != previous_zone
    alert_on_first_run = env_bool("ALERT_ON_FIRST_RUN", False)
    alert_needed = zone in {"ACIMA_VERDE", "ABAIXO_DOURADA"} and (zone != previous_zone) and (alert_on_first_run or not first_run)

    state = Snapshot(
        version=SCRIPT_VERSION,
        updated_at=iso_utc(time.time()),
        symbol=symbol,
        zone=zone,
        previous_zone=previous_zone,
        subzone=subzone,
        alert_sent=False,
        alert_reason="",
        price_now=round(price_now, 8),
        fair_price_now=round(fair_now, 8),
        fr_now=round(fr_now, 10),
        line_green_105=round(fair_now * env_float("FR_GREEN", 1.05), 8),
        line_golden_096=round(fair_now * env_float("FR_GOLDEN", 0.96), 8),
        line_blue_093=round(fair_now * env_float("FR_BLUE", 0.93), 8),
        ema_win=win,
        calibration_mode=cal.mode,
        calibration_text=cal.text,
        last_closed_1h_open_time=br_hour_utc(last_row.t),
        last_closed_1h_close=round(last_row.close, 8),
        last_closed_1h_fair_price=round(last_fair, 8),
        last_closed_1h_fr=round(last_fr, 10),
        binance_base_url=price_base or kline_base,
    )

    if alert_needed:
        state.alert_reason = f"{previous_zone} -> {zone}"
        send_telegram(build_message(state))
        state.alert_sent = True
        print(f"Alerta enviado: {state.alert_reason}")
    else:
        if first_run:
            print(f"Primeira execução: estado inicial {zone}; alerta inicial desativado.")
        elif changed_zone:
            print(f"Zona mudou {previous_zone} -> {zone}; sem alerta porque voltou/ficou em zona não alertável.")
        else:
            print(f"Zona inalterada: {zone}; sem alerta e sem alterar estado.")

    # Para reduzir commits no GitHub grátis, só grava JSON quando precisa mudar o estado.
    if changed_zone:
        write_state(state_path, state)
        print(f"Estado gravado em {state_path}")
    else:
        print("Estado não gravado para evitar commit horário desnecessário.")

    print(
        "Resumo:",
        json.dumps(
            {
                "symbol": symbol,
                "zone": zone,
                "previous_zone": previous_zone,
                "price_now": state.price_now,
                "fr_now": state.fr_now,
                "ema_win": win,
                "calibration": cal.text,
                "state_changed": changed_zone,
                "alert_sent": state.alert_sent,
            },
            ensure_ascii=False,
        ),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise
