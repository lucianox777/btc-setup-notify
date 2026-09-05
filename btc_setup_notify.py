"""
BTC Setup — Notificação via Telegram
GitHub Actions · roda de hora em hora (~30s/execução)

Arquitetura limpa (v204.29):
  - Fontes: CoinMetrics GitHub CSV (histórico) + CSV exportado pelo setup (tail)
  - SEM chamada direta à CoinMetrics API (evita 403 no GitHub Actions)
  - Heartbeat: envia quando o CSV exportado atualiza para nova data fechada
  - Linha parcial (hoje UTC): bloqueada — só usa fechamentos confirmados
  - Rodapé: v204.29
  - v204.1: Halving dist não entra em bloqueios; virada verde aquecida entra só em ressalvas se detectada
  - v204.2: ressalvas de ciclo/aquecimento também aparecem durante o regime verde, não só na virada
  - v204.3: separa macro COMPRAR/100% de execução cap 50%; se posição atual já está em 50%, mensagem final vira MANTER 50%, não AUMENTAR ATÉ 50%
  - v204.4: aplica v32.9: filtros/cap 50 são limite de aumento, não alvo de venda; se posição atual está acima de 50% e a macro continua 100%, manter a posição atual e não vender por filtro
  - v204.23: mensagem diária inclui preço fechado, FR fechado, linhas diárias 1,05/0,96/0,93 e linha VFL diária/regime
  - v204.24: fallback aproximado para a linha VFL diária/regime quando o cálculo exato não retorna valor
  - v204.25: mensagem do Telegram enxuta; evita repetir "diário/diária" nas linhas e na recomposição
  - v204.26: rótulo da linha azul ajustado para "0,93 / reversão"; bloco 25% preservado
  - v204.27: garante linhas FR na mensagem diária e remove repetição de valores no bloco Recomposição
  - v204.28: restaura mensagem diária completa estilo v204.24, com bloco de recomposição detalhado, preservando rótulo azul “0,93 / reversão” e corrigindo inconsistência posição 0% vs MANTER 50%
  - v204.29: mantém as linhas FR no bloco de mercado e transforma Recomposição em leitura operacional, sem repetir os mesmos valores de preço/linhas

Secrets GitHub:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

Arquivos no repositório:
  btc_setup_notify.py
  data/btc_setup_export.csv    ← atualizado pelo workflow de exportação
  data/estado_anterior.json    ← criado automaticamente
  .github/workflows/btc_notify.yml
"""

import os, json, datetime, urllib.request, io, time
import pandas as pd
import numpy as np
from pathlib import Path

# ─── CONFIGURAÇÃO ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
EXPORT_CSV       = os.environ.get("EXPORT_CSV_PATH",  "data/btc_setup_export.csv")
ESTADO_PATH      = os.environ.get("ESTADO_PATH",      "data/estado_anterior.json")

# Opcional: informe a exposição atual quando o CSV exportado não trouxer esse campo.
# Aceita 50, 50%, 0.50 ou 0,50.
CURRENT_EXPOSURE_PCT_ENV = os.environ.get("BTC_EXPOSURE_PCT", os.environ.get("CURRENT_EXPOSURE_PCT", ""))

CM_CSV_URL = "https://raw.githubusercontent.com/coinmetrics/data/refs/heads/master/csv/btc.csv"


def _parse_pct_value(v, default=np.nan) -> float:
    """Normaliza percentuais: 50/"50%" -> 0.50; 0.50 -> 0.50."""
    try:
        if v is None or pd.isna(v):
            return default
        if isinstance(v, str):
            s = v.strip().replace("%", "").replace(" ", "")
            if not s:
                return default
            if "," in s and "." not in s:
                s = s.replace(",", ".")
            x = float(s)
        else:
            x = float(v)
        if not np.isfinite(x):
            return default
        if abs(x) > 1.5:
            x = x / 100.0
        return max(0.0, min(1.0, x))
    except Exception:
        return default

def _row_first(row: pd.Series, names, default=np.nan):
    for name in names:
        try:
            if name in row.index:
                v = row.get(name)
                if v is not None and not pd.isna(v):
                    return v
        except Exception:
            pass
    return default

def _text_first(row: pd.Series, names, default="") -> str:
    for name in names:
        try:
            if name in row.index:
                v = row.get(name)
                if v is not None and not pd.isna(v) and str(v).strip():
                    return str(v).strip()
        except Exception:
            pass
    return default

# ─── DOWNLOAD COM RETRY ─────────────────────────────────────────────────────
def baixar(url: str, timeout=30, retries=3, backoff=5) -> bytes:
    ultimo_erro = None
    for tentativa in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "btc-setup-notify/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            ultimo_erro = e
            if tentativa < retries:
                espera = backoff * (2 ** (tentativa - 1))
                print(f"  Tentativa {tentativa}/{retries} falhou: {e} — aguardando {espera}s...")
                time.sleep(espera)
            else:
                print(f"  Tentativa {tentativa}/{retries} falhou: {e}")
    raise RuntimeError(f"Falha após {retries} tentativas: {ultimo_erro}")

def enviar_erro_telegram(token: str, chat_id: str, erro: str):
    try:
        texto = "🔴 *BTC Setup Notify — ERRO*\n\n" + f"`{erro[:300]}`" + "\n\n_Verifique o GitHub Actions_"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"  Erro ao enviar erro: {e}")

# ─── CARREGAR DADOS ─────────────────────────────────────────────────────────
def carregar_dados() -> pd.DataFrame:
    hoje_utc = datetime.datetime.now(datetime.timezone.utc).date()
    ontem    = hoje_utc - datetime.timedelta(days=1)

    # 1. CSV base CoinMetrics (histórico completo)
    print("  Baixando CSV CoinMetrics...")
    raw  = baixar(CM_CSV_URL, retries=3, backoff=5).decode("utf-8")
    df   = pd.read_csv(io.StringIO(raw))
    df["time"] = pd.to_datetime(df["time"])
    df   = df[df["PriceUSD"].notna() & df["CapMVRVCur"].notna()].copy()
    df   = df.sort_values("time").reset_index(drop=True)
    ultimo = df["time"].iloc[-1].date()
    print(f"  GitHub CSV: {len(df)} linhas, até {ultimo}")

    # 2. CSV exportado pelo setup (tail recente)
    if Path(EXPORT_CSV).exists():
        df_exp = pd.read_csv(EXPORT_CSV)
        col_dt = "date" if "date" in df_exp.columns else df_exp.columns[0]
        df_exp["time"] = pd.to_datetime(df_exp[col_dt])
        df_exp = df_exp.sort_values("time").reset_index(drop=True)

        # Bloquear linha de hoje (parcial/intraday) — só fechamentos confirmados
        df_exp = df_exp[df_exp["time"].dt.date <= ontem].copy()

        ultimo_exp = df_exp["time"].iloc[-1].date() if len(df_exp) else None
        print(f"  CSV exportado: {len(df_exp)} linhas, até {ultimo_exp} (hoje={hoje_utc} bloqueado)")

        tail_exp = df_exp[df_exp["time"].dt.date > ultimo]
        if len(tail_exp):
            last_sply     = float(df["SplyCur"].dropna().iloc[-1])
            last_vol_ma90 = float(df["volume_reported_spot_usd_1d"].dropna().tail(90).mean())
            rows = []
            for _, row in tail_exp.iterrows():
                days = (row["time"].date() - ultimo).days
                new  = {c: np.nan for c in df.columns}
                new["time"]           = row["time"]
                new["PriceUSD"]       = row.get("price_usd", np.nan)
                new["CapMVRVCur"]     = row.get("mvrv", np.nan)
                if pd.notna(new["CapMVRVCur"]) and pd.notna(row.get("cap_real_usd")):
                    new["CapMrktCurUSD"] = row["cap_real_usd"] * row["mvrv"]
                new["SplyCur"]        = last_sply + days * 450
                new["volume_reported_spot_usd_1d"] = last_vol_ma90
                new["_vfl_signal_csv"]   = row.get("vfl_signal", np.nan)
                new["_sth_confirm"]      = row.get("sth_confirmation", "")
                new["_combined_signal"]  = _text_first(row, ["combined_signal", "operational_signal", "decision_label", "action_label"], "")
                new["_target_exposure"]  = _row_first(row, ["target_exposure", "macro_exposure", "base_exposure", "target"], np.nan)
                # v204.3: campos opcionais de execução/posição exportados pelo setup
                new["_macro_exposure"]   = _row_first(row, ["macro_exposure", "macro_target", "base_exposure", "target_exposure"], np.nan)
                new["_execution_cap"]    = _row_first(row, ["execution_cap", "exec_cap", "effective_target_exposure", "cap_control", "capControl", "execution_target", "effective_target"], np.nan)
                new["_current_exposure"] = _row_first(row, ["current_exposure", "current_exposure_pct", "btc_exposure", "btc_exposure_pct", "exposure_pct", "posicao_pct"], np.nan)
                new["_execution_note"]   = _text_first(row, ["execution_note", "execution_summary", "operational_note", "action_text", "action_sub"], "")
                new["_fng_csv"]          = _row_first(row, ["fng", "fear_greed", "fearGreed"], np.nan)
                # v204: campos de volume e risco direto do CSV exportado
                new["_vol_mult_csv"]     = row.get("vol_mult_ma90", np.nan)
                new["_dd60_csv"]         = row.get("dd60", np.nan)
                new["_vol14_csv"]        = row.get("vol14", np.nan)
                new["_ret7_csv"]         = row.get("ret7", np.nan)
                rows.append(new)
            df_tail = pd.DataFrame(rows)
            df = pd.concat([df, df_tail], ignore_index=True)
            df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
            print(f"  +{len(df_tail)} dias do exportado → até {df['time'].iloc[-1].date()}")
    else:
        print(f"  CSV exportado não encontrado: {EXPORT_CSV}")

    # SEM chamada à CoinMetrics API — confia no CSV exportado pelo workflow
    print(f"  Último dia disponível: {df['time'].iloc[-1].date()}")
    return df


# ─── LINHA VFL DIÁRIA / REGIME ──────────────────────────────────────────────
def _finite_mean_full(vals):
    vals = list(vals)
    if not vals:
        return np.nan
    out = []
    for v in vals:
        try:
            x = float(v)
            if not np.isfinite(x):
                return np.nan
            out.append(x)
        except Exception:
            return np.nan
    return float(sum(out) / len(out)) if out else np.nan


def _project_vfl_ratio_at_price(df: pd.DataFrame, price: float, long: int = 180, short: int = 14) -> float:
    """Projeta o VFL suavizado caso o próximo fechamento diário fosse `price`."""
    try:
        if df is None or len(df) < max(long, short) or not np.isfinite(price) or price <= 0:
            return np.nan
        last = df.iloc[-1]
        base_price = float(last["PriceUSD"])
        base_mvrv = float(last["mvrv"])
        if not np.isfinite(base_price) or base_price <= 0 or not np.isfinite(base_mvrv):
            return np.nan
        projected_mvrv = base_mvrv * (price / base_price)
        mvrv_window = list(df["mvrv"].iloc[-(long - 1):])
        projected_mvrv_ma = _finite_mean_full(mvrv_window + [projected_mvrv])
        projected_raw = projected_mvrv / projected_mvrv_ma if np.isfinite(projected_mvrv_ma) and projected_mvrv_ma != 0 else np.nan
        raw_col = "vflRaw" if "vflRaw" in df.columns else "VflRatio"
        raw_window = list(df[raw_col].iloc[-(short - 1):])
        projected_ratio = _finite_mean_full(raw_window + [projected_raw])
        return projected_ratio
    except Exception:
        return np.nan


def calcular_linha_vfl_diaria(df: pd.DataFrame, vfl_buy: float = 1.005, vfl_sell: float = 0.985) -> dict:
    """Preço de fechamento que ameaçaria a próxima mudança do regime VFL."""
    if df is None or len(df) == 0:
        return {"price": np.nan, "mode": "VFL regime", "direction": "indisponível", "threshold": np.nan}
    last = df.iloc[-1]
    base_price = float(last["PriceUSD"])
    is_green = int(last.get("signal", 0)) == 1
    threshold = vfl_sell if is_green else vfl_buy
    mode = "Defesa VFL" if is_green else "Virada VFL"
    direction = "fechamento abaixo ameaça virar vermelho" if is_green else "fechamento acima pode virar verde"

    turn_price = np.nan
    if np.isfinite(base_price) and base_price > 0:
        if not is_green:
            lo = max(1.0, base_price * 0.35)
            hi = base_price * 1.03
            tries = 0
            while np.isfinite(_project_vfl_ratio_at_price(df, hi)) and _project_vfl_ratio_at_price(df, hi) < vfl_buy and tries < 40:
                hi *= 1.08
                tries += 1
            if np.isfinite(_project_vfl_ratio_at_price(df, hi)) and _project_vfl_ratio_at_price(df, hi) >= vfl_buy:
                for _ in range(70):
                    mid = (lo + hi) / 2
                    r = _project_vfl_ratio_at_price(df, mid)
                    if np.isfinite(r) and r >= vfl_buy:
                        hi = mid
                    else:
                        lo = mid
                turn_price = hi
        else:
            lo = max(1.0, base_price * 0.25)
            hi = base_price * 1.03
            tries = 0
            while np.isfinite(_project_vfl_ratio_at_price(df, lo)) and _project_vfl_ratio_at_price(df, lo) > vfl_sell and tries < 40:
                lo *= 0.82
                tries += 1
            if np.isfinite(_project_vfl_ratio_at_price(df, lo)) and _project_vfl_ratio_at_price(df, lo) <= vfl_sell:
                for _ in range(70):
                    mid = (lo + hi) / 2
                    r = _project_vfl_ratio_at_price(df, mid)
                    if np.isfinite(r) and r <= vfl_sell:
                        lo = mid
                    else:
                        hi = mid
                turn_price = lo

    # v204.24: fallback aproximado. Se a bisseção não encontrou valor finito
    # por alguma coluna auxiliar/tail, ainda exibimos a referência de regime
    # usando o VFL diário confirmado: preço * limiar / VFL atual.
    approx = False
    if not np.isfinite(turn_price):
        try:
            last_ratio = float(last.get("VflRatio", np.nan))
        except Exception:
            last_ratio = np.nan
        if np.isfinite(base_price) and base_price > 0 and np.isfinite(last_ratio) and last_ratio > 0 and np.isfinite(threshold) and threshold > 0:
            turn_price = base_price * (threshold / last_ratio)
            approx = True
            mode = mode + " (aprox.)"
            direction = direction + "; cálculo aproximado pelo VFL"

    return {"price": turn_price, "mode": mode, "direction": direction, "threshold": threshold, "approx": approx}

# ─── CALCULAR INDICADORES ───────────────────────────────────────────────────
def calcular(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("time").reset_index(drop=True)
    p  = df["PriceUSD"].values

    df["logret"] = np.log(pd.Series(p) / pd.Series(p).shift(1))
    for n in [3, 7, 14, 30, 60]:
        df[f"ret{n}"] = pd.Series(p).pct_change(n).values
    for n in [14, 30, 60, 90]:
        df[f"vol{n}"] = df["logret"].rolling(n).std() * np.sqrt(365)

    df["CapRealUSD"] = df["CapMrktCurUSD"] / df["CapMVRVCur"]
    df["EMA4"]       = pd.Series(p).ewm(span=4).mean().values
    df["EMA50"]      = pd.Series(p).ewm(span=50).mean().values
    df["EMA200"]     = pd.Series(p).ewm(span=200).mean().values
    df["FR"]         = p / df["EMA4"].values
    df["mvrv"]       = df["CapMVRVCur"]
    df["ema200Dist"] = p / df["EMA200"].values - 1
    df["capRealPct7"] = pd.Series(df["CapRealUSD"].values).pct_change(7).values
    df["mvrvROC7"]   = pd.Series(df["mvrv"].values).pct_change(7).values
    df["mvrvROC30"]  = pd.Series(df["mvrv"].values).pct_change(30).values

    # VflRatio = MVRV/MA180(MVRV), suavizado MA14 — fórmula real do setup
    mvrv_vals  = df["mvrv"].values
    mvrvMA180  = pd.Series(mvrv_vals).rolling(180).mean().values
    vflRaw     = np.where((np.isfinite(mvrvMA180)) & (mvrvMA180 != 0),
                          mvrv_vals / mvrvMA180, np.nan)
    df["vflRaw"] = vflRaw
    df["VflRatio"] = pd.Series(vflRaw).rolling(14).mean().values

    # buildSignal com histerese: verde em >1.005, vermelho em <0.985
    vfl_vals = df["VflRatio"].values
    sig = np.zeros(len(vfl_vals), dtype=int)
    state = 0
    for i in range(len(vfl_vals)):
        v = vfl_vals[i]
        if np.isfinite(v):
            if state == 0 and v > 1.005: state = 1
            elif state == 1 and v < 0.985: state = 0
        sig[i] = state
    df["signal"] = sig

    # Usar vfl_signal do CSV exportado onde disponível
    if "_vfl_signal_csv" in df.columns:
        mask = df["_vfl_signal_csv"].notna()
        df.loc[mask, "signal"] = df.loc[mask, "_vfl_signal_csv"].astype(int)

    # v204.23/v204.24: preço diário de defesa/virada do regime VFL.
    # É uma projeção para o próximo fechamento diário; não é a linha azul 0,93.
    try:
        turn = calcular_linha_vfl_diaria(df)
        for k, v in turn.items():
            df.loc[df.index[-1], f"_vfl_turn_{k}"] = v
    except Exception as e:
        print(f"  Aviso: falha ao calcular linha VFL diária/regime: {e}")

    # RSI14
    d = np.diff(p); g = np.where(d > 0, d, 0); l = np.where(d < 0, -d, 0)
    r14 = np.full(len(p), np.nan)
    for i in range(14, len(p)):
        ag = g[max(0, i-14):i].mean(); al = l[max(0, i-14):i].mean()
        r14[i] = 100 - 100 / (1 + ag/al) if al > 0 else 100
    df["rsi14"] = r14

    halvings = [pd.Timestamp("2012-11-28"), pd.Timestamp("2016-07-09"),
                pd.Timestamp("2020-05-11"), pd.Timestamp("2024-04-20")]
    def hp(t):
        prev = [h for h in halvings if h <= t]
        if not prev: return "pre"
        d = (t - prev[-1]).days
        return "acum" if d<365 else "bull" if d<730 else "dist" if d<900 else "bear" if d<1460 else "pre"
    df["halvPhase"] = df["time"].apply(hp)

    vcol = "volume_reported_spot_usd_1d"
    df["volMa90"] = pd.Series(df[vcol].values).rolling(90).mean()
    df["volMult"] = df[vcol] / df["volMa90"]
    df["peak60"]  = pd.Series(p).rolling(60).max().values
    df["dd60"]    = p / df["peak60"].values - 1
    df["ret1d"]   = pd.Series(p).pct_change(1).values

    # Sobrescrever com valores do CSV exportado (mais precisos)
    if "_vol_mult_csv" in df.columns:
        mask = df["_vol_mult_csv"].notna()
        df.loc[mask, "volMult"] = df.loc[mask, "_vol_mult_csv"]
    if "_dd60_csv" in df.columns:
        mask = df["_dd60_csv"].notna()
        df.loc[mask, "dd60"] = df.loc[mask, "_dd60_csv"]
    if "_vol14_csv" in df.columns:
        mask = df["_vol14_csv"].notna()
        df.loc[mask, "vol14"] = df.loc[mask, "_vol14_csv"]
    if "_ret7_csv" in df.columns:
        mask = df["_ret7_csv"].notna()
        df.loc[mask, "ret7"] = df.loc[mask, "_ret7_csv"]
    if "_fng_csv" in df.columns:
        mask = df["_fng_csv"].notna()
        if mask.any():
            df.loc[mask, "fng"] = df.loc[mask, "_fng_csv"]

    return df

# ─── DECISÃO OPERACIONAL ────────────────────────────────────────────────────
def calcular_decisao(last: pd.Series) -> dict:
    vfl  = int(last["signal"])
    fr   = float(last["FR"])
    zona = 0.96 <= fr <= 1.05
    r3, r7, r14 = float(last["ret3"]), float(last["ret7"]), float(last["ret14"])

    cards, bloqueios = [], []
    def add(nome, cond):
        if cond: cards.append(nome)

    add("Vol14+ret7",        last["vol14"] < .40 and r7 > .03)
    add("EMA50>EMA200+vol14",last["EMA50"] > last["EMA200"] and last["vol14"] < .40 and r7 > .03)
    add("CapReal7d+vol14",   float(last["capRealPct7"]) > 0 and last["vol14"] < .40 and r7 > .03)
    add("RSI14>65+vol14",    last["rsi14"] > 65 and last["vol14"] < .40)
    add("RSI14>70+vol14",    last["rsi14"] > 70 and last["vol14"] < .40)
    add("MVRV ROC7+vol14",   float(last["mvrvROC7"]) > 0 and last["vol14"] < .40 and r7 > .03)
    add("MVRV ROC30+vol14",  float(last["mvrvROC30"]) > 0 and last["vol14"] < .40 and r7 > .03)
    add("Acima EMA200+vol14",float(last["ema200Dist"]) > 0 and last["vol14"] < .40 and r7 > .03)

    # halvPhase dist removido dos bloqueios — backtests v32.8.2/v32.8.3.
    if r7 < 0:                         bloqueios.append("ret7 neg")
    if r3 < 0 and r7 < 0 and r14 < 0: bloqueios.append("Tripla neg")

    if not vfl:
        op, emoji, mot = "ALVO 0%", "⏸", "VFL vermelho — fora da zona"
        macro_op, macro_exposure = "ALVO 0%", 0.0
    elif not zona:
        op, emoji, mot = "AGUARDAR", "⏸", f"FR={fr:.4f} fora da zona 0.96-1.05"
        macro_op, macro_exposure = "AGUARDAR", 0.0
    elif fr >= 1.05:
        op, emoji, mot = "MANTER 50%", "🔒", "VFL verde, mas FR≥1,05 — exposição macro 50%"
        macro_op, macro_exposure = "MANTER 50%", 0.5
    elif bloqueios:
        op, emoji, mot = "NÃO COMPRAR", "🚫", ", ".join(bloqueios)
        macro_op, macro_exposure = "NÃO COMPRAR", 0.0
    elif len(cards) >= 2:
        op, emoji, mot = "COMPRAR", "✅", f"{len(cards)} cards favoráveis"
        macro_op, macro_exposure = "COMPRAR", 1.0
    elif len(cards) == 1:
        op, emoji, mot = "AGUARDAR", "⏳", "1 card — aguardar confirmação"
        macro_op, macro_exposure = "AGUARDAR", 0.0
    else:
        op, emoji, mot = "AGUARDAR", "⏸", "Nenhum card ativo"
        macro_op, macro_exposure = "AGUARDAR", 0.0

    _combined = str(last.get("_combined_signal", "") or "").strip()
    _expo     = last.get("_target_exposure")
    _macro_exp = _parse_pct_value(last.get("_macro_exposure", np.nan), np.nan)
    _exec_cap  = _parse_pct_value(last.get("_execution_cap", np.nan), np.nan)
    _curr_exp  = _parse_pct_value(last.get("_current_exposure", np.nan), np.nan)
    _env_exp   = _parse_pct_value(CURRENT_EXPOSURE_PCT_ENV, np.nan)
    if np.isfinite(_env_exp):
        _curr_exp = _env_exp
    _execution_note = str(last.get("_execution_note", "") or "").strip()

    if _combined:
        op = _combined
        op_up_tmp = op.upper()
        if "COMPRAR" in op_up_tmp or "AUMENTAR" in op_up_tmp:   emoji = "✅"
        elif "VENDER" in op_up_tmp:                              emoji = "📉"
        elif "MANTER" in op_up_tmp:                              emoji = "🔒"
        else:                                                       emoji = "⏸"

    if _expo is not None and not pd.isna(_expo):
        _expo_norm = _parse_pct_value(_expo, np.nan)
        if np.isfinite(_expo_norm):
            macro_exposure = _expo_norm
            if macro_exposure >= 0.99:
                macro_op = "COMPRAR"
            elif macro_exposure >= 0.49:
                macro_op = "MANTER 50%"
            elif macro_exposure <= 0.01:
                macro_op = "ALVO 0%"

    op_upper = op.upper()
    note_upper = _execution_note.upper()
    if not np.isfinite(_exec_cap) and (
        "ATÉ 50" in op_upper or "ATE 50" in op_upper or "CAP 50" in op_upper or
        "LIMITE 50" in op_upper or "MANTER 50" in op_upper or
        "ATÉ 50" in note_upper or "ATE 50" in note_upper or
        "CAP 50" in note_upper or "LIMITE 50" in note_upper or "MANTER 50" in note_upper
    ):
        _exec_cap = 0.50

    execution_cap = _exec_cap if np.isfinite(_exec_cap) else macro_exposure
    if np.isfinite(execution_cap):
        execution_cap = max(0.0, min(1.0, execution_cap))

    execution_note = ""
    if vfl == 1 and macro_exposure >= 0.99 and np.isfinite(execution_cap) and execution_cap <= 0.51:
        # v204.4 / v32.9:
        # O cap 50 validado como filtro operacional NÃO virou alvo de venda.
        # Ele limita apenas novos aumentos/recomposições. Venda/redução só pela macro:
        #   - VFL vermelho -> 0%
        #   - VFL verde + FR >= 1,05 -> 50%
        # Se a posição atual já estiver acima de 50% enquanto a macro segue 100%, manter.
        if np.isfinite(_curr_exp):
            if _curr_exp < execution_cap - 0.005:
                op = "AUMENTAR ATÉ 50%"
                emoji = "✅"
                mot = f"macro: COMPRAR/100%; execução: comprar só até 50%; posição atual {_curr_exp*100:.0f}%"
                execution_note = (
                    "Macro VFL/FR permite 100%, mas filtros de execução limitam NOVA compra/recomposição "
                    "a 50% enquanto não houver desbloqueio v33.13."
                )
            elif _curr_exp <= execution_cap + 0.005:
                op = "MANTER 50%"
                emoji = "🔒"
                mot = f"macro: COMPRAR/100%; execução: limite de aumento 50%; posição atual {_curr_exp*100:.0f}%"
                execution_note = (
                    "Macro VFL/FR continua comprada, mas o cap de execução é 50%; como a posição "
                    "já está no limite, não aumentar para 75/100 agora."
                )
            else:
                op = f"MANTER {_curr_exp*100:.0f}%"
                emoji = "🔒"
                mot = f"macro: COMPRAR/100%; filtro: limite de aumento 50%; posição atual {_curr_exp*100:.0f}%"
                execution_note = (
                    "v32.9 rejeitou tratar o cap 50 como alvo de venda. O filtro bloqueia NOVO aumento/recomposição, "
                    "mas não manda vender posição existente acima de 50%; vender só se a macro pedir 50% ou 0%."
                )
        elif "AUMENTAR" in op_upper or "COMPRAR" in op_upper:
            op = "AUMENTAR ATÉ 50%"
            emoji = "✅"
            mot = "macro: COMPRAR/100%; execução: limite de aumento 50%"
            execution_note = (
                "Sem exposição atual informada no CSV/env; se a carteira já estiver em 50%, a ação prática é MANTER 50%; "
                "se estiver acima de 50%, NÃO vender por este filtro."
            )

    if not execution_note and _execution_note:
        execution_note = _execution_note

    return {"op": op, "emoji": emoji, "mot": mot,
            "cards": cards, "bloqueios": bloqueios, "vfl": vfl, "fr": fr,
            "macro_op": macro_op, "macro_exposure": macro_exposure,
            "execution_cap": execution_cap, "current_exposure": _curr_exp,
            "execution_note": execution_note}

# ─── CAPITULAÇÃO 25% ────────────────────────────────────────────────────────
def calcular_cap25(last: pd.Series) -> dict:
    vfl_verm = int(last["signal"]) == 0
    vol_mult = float(last["volMult"]) if pd.notna(last["volMult"]) else None
    dd60     = float(last["dd60"])    if pd.notna(last["dd60"])    else None
    ret1d    = float(last["ret1d"])   if pd.notna(last["ret1d"])   else None
    vol_ok   = vol_mult is not None and vol_mult >= 2.0
    dd_ok    = dd60    is not None and dd60    <= -0.20
    dia_ok   = ret1d   is not None and ret1d   < 0
    return {
        "ativa":    vfl_verm and vol_ok and dd_ok and dia_ok,
        "quase":    vfl_verm and dd_ok  and dia_ok and not vol_ok,
        "vfl_verm": vfl_verm, "vol_ok": vol_ok, "dd_ok": dd_ok, "dia_ok": dia_ok,
        "volMult":  vol_mult, "dd60": dd60, "ret1d": ret1d,
    }

# ─── ESTADO ─────────────────────────────────────────────────────────────────
def carregar_estado() -> dict:
    path = Path(ESTADO_PATH)
    if path.exists():
        try: return json.loads(path.read_text())
        except: pass
    return {}

def salvar_estado(estado: dict):
    path = Path(ESTADO_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(estado, indent=2, ensure_ascii=False))

def extrair_estado(last: pd.Series, dec: dict, cap: dict) -> dict:
    return {
        "date":      str(last["time"])[:10],
        "vfl":       dec["vfl"],
        "dec_label": dec["op"],
        "cap_ativa": cap["ativa"],
        "cap_quase": cap["quase"],
        # last_daily_ping_date não está aqui — é preservado no main
    }

def detectar_mudancas(ant: dict, atual: dict) -> list:
    if not ant:
        return ["🆕 Primeira execução"]
    mudancas = []
    if ant.get("vfl") != atual.get("vfl"):
        de   = "🟢 verde"   if ant.get("vfl")  == 1 else "🔴 vermelho"
        para = "🟢 verde"   if atual.get("vfl") == 1 else "🔴 vermelho"
        mudancas.append(f"VFL: {de} → {para}")
    if ant.get("dec_label") != atual.get("dec_label"):
        mudancas.append(f"Decisão: {ant.get('dec_label','—')} → {atual.get('dec_label','—')}")
    if not ant.get("cap_ativa") and atual.get("cap_ativa"):
        mudancas.append("🚨 Comprar 25% ATIVADA")
    if ant.get("cap_ativa") and not atual.get("cap_ativa"):
        mudancas.append("Comprar 25% desativada")
    if not ant.get("cap_quase") and not ant.get("cap_ativa") and atual.get("cap_quase"):
        mudancas.append("⚠️ Quase-capitulação detectada")
    return mudancas

def deve_enviar_heartbeat(estado_ant: dict, ultimo_dia: str) -> bool:
    """
    Envia heartbeat quando o CSV exportado atualiza para nova data fechada.
    Compara último dia disponível com last_daily_ping_date no estado.
    """
    ultimo_ping = estado_ant.get("last_daily_ping_date")
    return ultimo_ping != ultimo_dia

# ─── RESSALVAS / DIAGNÓSTICOS NÃO BLOQUEANTES ──────────────────────────────
def _safe_float(v, default=np.nan) -> float:
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default

def _fmt_pct(v, signed=True) -> str:
    x = _safe_float(v)
    if not np.isfinite(x):
        return "—"
    sign = "+" if signed else ""
    return f"{x*100:{sign}.1f}%"

def _fmt_num(v, decimals=0) -> str:
    x = _safe_float(v)
    if not np.isfinite(x):
        return "—"
    return f"{x:.{decimals}f}"

def calcular_ressalvas(last: pd.Series, dec: dict, estado_ant: dict) -> list:
    """
    Ressalvas são diagnósticos de contexto: aparecem no Telegram, mas NÃO bloqueiam
    a decisão operacional.

    v204.2:
      - Halving Dist continua fora de 🚫 Bloqueios.
      - Ciclo avançado/fase dist aparece como ressalva durante o regime verde,
        não apenas no dia da virada.
      - Aquecimento (ret7/RSI/F&G quando disponível) também aparece durante o
        regime verde; na virada vermelho→verde o texto fica mais forte.
    """
    ressalvas = []
    op = str(dec.get("op", ""))
    op_upper = op.upper()
    vfl = int(dec.get("vfl", 0))
    fr = _safe_float(dec.get("fr"))
    r7 = _safe_float(last.get("ret7"))
    rsi = _safe_float(last.get("rsi14"))
    fng = _safe_float(last.get("fng", last.get("fear_greed", np.nan)))
    mvrv = _safe_float(last.get("mvrv"))
    fase = str(last.get("halvPhase", "") or "")
    green_flip = bool(estado_ant and estado_ant.get("vfl") == 0 and vfl == 1)

    # Ressalvas só entram quando o regime está verde e a decisão é operacionalmente
    # positiva/neutra de posição. Não usar para VFL vermelho nem para bloqueios reais.
    green_operational = (
        vfl == 1 and
        ("COMPRAR" in op_upper or "MANTER" in op_upper or "ALVO" in str(dec.get("mot", "")).upper())
    )

    # Halving Dist: diagnóstico de ciclo, não bloqueio do Telegram.
    # A v32.8.1 achou pista geral; v32.8.2/v32.8.3 não validaram bloqueio na virada.
    if green_operational and fase == "dist":
        if green_flip:
            ressalvas.append(
                "Ciclo avançado pós-halving: fase dist; v32.8.1 achou pista de cautela para aumento, "
                "mas v32.8.2/v32.8.3 não confirmaram bloqueio na virada verde."
            )
        else:
            ressalvas.append(
                "Ciclo avançado pós-halving: fase dist; manter como diagnóstico/cautela de execução, "
                "não como bloqueio operacional."
            )

    # Aquecimento durante todo o regime verde, não só na virada.
    # v32.8.3 não promoveu atraso/cap automático, então o texto é ressalva, não regra.
    heat_reasons = []
    if np.isfinite(r7) and r7 >= 0.20:
        heat_reasons.append(f"ret7 {_fmt_pct(r7)}")
    elif np.isfinite(r7) and r7 >= 0.15:
        heat_reasons.append(f"ret7 {_fmt_pct(r7)}")

    if np.isfinite(rsi) and rsi >= 75:
        heat_reasons.append(f"RSI {_fmt_num(rsi, 0)}")
    elif np.isfinite(rsi) and rsi >= 70:
        heat_reasons.append(f"RSI {_fmt_num(rsi, 0)}")

    if np.isfinite(fng) and fng >= 65:
        heat_reasons.append(f"F&G {_fmt_num(fng, 0)}")

    heated_regime = (
        green_operational and
        0.96 <= fr <= 1.05 and
        (
            (np.isfinite(r7) and np.isfinite(rsi) and r7 >= 0.20 and rsi >= 75) or
            (np.isfinite(r7) and np.isfinite(rsi) and r7 >= 0.15 and rsi >= 70) or
            (np.isfinite(r7) and np.isfinite(fng) and r7 >= 0.15 and fng >= 65) or
            (fase == "dist" and np.isfinite(r7) and r7 >= 0.20)
        )
    )

    if heated_regime:
        detalhes = []
        detalhes.extend(heat_reasons)
        detalhes.append(f"FR {_fmt_num(fr, 4)}")
        if np.isfinite(mvrv):
            detalhes.append(f"MVRV {_fmt_num(mvrv, 3)}")
        if fase:
            detalhes.append(f"fase {fase}")

        prefixo = "Virada verde aquecida" if green_flip else "Regime verde aquecido"
        ressalvas.append(
            prefixo + ": " + ", ".join(detalhes) +
            "; v32.8.3 não validou atraso/cap automático — usar só cautela de execução."
        )

    return ressalvas[:4]

# ─── NATUREZA DO PREÇO ───────────────────────────────────────────────────────
def natureza_preco(date_str: str) -> dict:
    try: dia = datetime.date.fromisoformat(date_str[:10])
    except: return {"emoji": "❓", "label": date_str, "aviso": None}
    hoje  = datetime.datetime.now(datetime.timezone.utc).date()
    ontem = hoje - datetime.timedelta(days=1)
    delta = (hoje - dia).days
    if dia >= hoje:
        return {"emoji": "⚡", "label": "parcial/intraday hoje", "aviso": "⚠️ fechamento pendente"}
    elif dia == ontem:
        return {"emoji": "✔️", "label": f"fechamento {dia.strftime('%d/%m')}", "aviso": None}
    else:
        return {"emoji": "⚠️",
                "label": f"fechamento {dia.strftime('%d/%m')} ({delta}d atrás)",
                "aviso": f"⚠️ dados desatualizados — {delta} dias"}

# ─── FORMATAÇÃO DAS LINHAS DIÁRIAS ──────────────────────────────────────────
def _fmt_usd0(v) -> str:
    x = _safe_float(v)
    if not np.isfinite(x):
        return "—"
    return f"US$ {x:,.0f}"


def _linhas_diarias(last: pd.Series, fr: float) -> dict:
    preco = _safe_float(last.get("PriceUSD"))
    fair_price = _safe_float(last.get("EMA4"))
    if not np.isfinite(fair_price) and np.isfinite(preco) and np.isfinite(fr) and fr != 0:
        fair_price = preco / fr
    return {
        "fair": fair_price,
        "green": fair_price * 1.05 if np.isfinite(fair_price) else np.nan,
        "gold": fair_price * 0.96 if np.isfinite(fair_price) else np.nan,
        "blue": fair_price * 0.93 if np.isfinite(fair_price) else np.nan,
        "vfl_price": _safe_float(last.get("_vfl_turn_price", np.nan)),
        "vfl_mode": str(last.get("_vfl_turn_mode", "Linha VFL/regime") or "Linha VFL/regime"),
        "vfl_direction": str(last.get("_vfl_turn_direction", "confirma só no fechamento") or "confirma só no fechamento"),
    }


# ─── MENSAGENS ───────────────────────────────────────────────────────────────
RODAPE = "_BTC Setup v204.29 · GitHub Actions_"

def _corpo(last, dec, cap, nat):
    preco = float(last["PriceUSD"])
    fr    = dec["fr"]
    vfl_s = "🟢 verde" if dec["vfl"] == 1 else "🔴 vermelho"
    halv  = last["halvPhase"]
    mvrv  = float(last["mvrv"])
    r7    = float(last["ret7"])
    v14   = float(last["vol14"])
    rsi   = float(last["rsi14"]) if pd.notna(last["rsi14"]) else None
    linhas = _linhas_diarias(last, fr)

    preco_txt = f"${preco:,.0f}  {nat['emoji']} _{nat['label']}_"
    if nat["aviso"]:
        preco_txt += f"\n{nat['aviso']}"

    # v204.29: mantém os valores das linhas apenas no bloco de mercado.
    # A recomposição abaixo é leitura operacional, sem repetir os mesmos números.
    msg  = f"💰 *Preço diário fechado:* {preco_txt}\n"
    msg += f"📊 *Diário fechado:* FR {fr:.4f}  |  VFL {vfl_s}\n"
    msg += f"🟢 *Linha verde diária 1,05:* {_fmt_usd0(linhas['green'])}\n"
    msg += f"🟡 *Linha dourada diária 0,96:* {_fmt_usd0(linhas['gold'])}\n"
    msg += f"🔵 *Linha azul diária 0,93 / reversão:* {_fmt_usd0(linhas['blue'])}\n"
    msg += f"🔁 *Linha VFL diária/regime:* {_fmt_usd0(linhas['vfl_price'])} — {linhas['vfl_mode']}; {linhas['vfl_direction']}\n"
    msg += f"📈 *ret7:* {r7*100:+.1f}%  |  *vol14:* {v14*100:.0f}%"
    msg += (f"  |  *RSI:* {rsi:.0f}" if rsi else "") + "\n"
    msg += f"🔄 *Fase:* {halv}  |  *MVRV:* {mvrv:.3f}\n"

    msg += "\n💵 *Leitura diária / recomposição:*\n"
    if dec.get("vfl") == 1:
        if fr < 1.05:
            msg += "VFL verde + FR diário <1,05 mantém alvo macro 100%.\n"
        else:
            msg += "VFL verde + FR diário ≥1,05 muda o alvo macro para 50%.\n"
        msg += "Linha verde 1,05 = redução/não-recompra; linha dourada 0,96 = zona preferencial de recomposição; 0,93/reversão = oportunidade profunda/contexto.\n"
        msg += "Filtro/cap de execução limita novo aumento; não é ordem de venda da posição existente.\n"
        curr = dec.get("current_exposure")
        cap_exec = dec.get("execution_cap")
        if np.isfinite(curr) and np.isfinite(cap_exec) and curr < cap_exec - 0.005:
            msg += f"Como a posição atual é {curr*100:.0f}% e o cap liberado é {cap_exec*100:.0f}%, a ação prática é comprar até {cap_exec*100:.0f}%.\n"
    else:
        msg += "VFL vermelho mantém alvo macro 0%; desconto/FR baixo/linha dourada não liberam compra pela regra principal.\n"
        msg += "A única compra em VFL vermelho preservada é a exceção 25% completa, com volume, DD60 e dia negativo confirmados.\n"

    msg += "\n*─ Comprar 25% ─*\n"
    if cap["ativa"]:
        msg += (f"🚨 *ATIVA*\n"
                f"VFL verm ✅  Vol ✅ {cap['volMult']:.2f}x  "
                f"DD60 ✅ {cap['dd60']*100:.1f}%  Dia neg ✅\n")
    else:
        v  = "✅" if cap["vfl_verm"] else "❌"
        vt = f"{cap['volMult']:.2f}x" if cap["volMult"] else "—"
        vv = f"✅ {vt}" if cap["vol_ok"] else f"❌ {vt}/2,00x"
        dt = f"{cap['dd60']*100:.1f}%" if cap["dd60"] is not None else "—"
        dv = f"✅ {dt}" if cap["dd_ok"] else f"❌ {dt}/-20%"
        dg = "✅" if cap["dia_ok"] else "❌"
        if cap["quase"]: msg += "⚠️ _Quase-cap_\n"
        msg += f"VFL verm {v}  Vol {vv}  DD60 {dv}  Dia neg {dg}\n"
    return msg

def _append_decision_context(msg: str, dec: dict, ressalvas=None) -> str:
    if dec["bloqueios"]:
        msg += f"🚫 *Bloqueios:* {', '.join(dec['bloqueios'])}\n"
    elif ("COMPRAR" in str(dec.get("op", "")).upper()
          or "AUMENTAR" in str(dec.get("op", "")).upper()
          or dec.get("macro_exposure", 0) >= 0.99):
        msg += "🚫 *Bloqueios:* nenhum bloqueio operacional confirmado\n"
    if dec.get("execution_note"):
        msg += f"🧭 *Execução:* {dec['execution_note']}\n"
    if ressalvas:
        msg += "⚠️ *Ressalvas:*\n" + "\n".join(f"• {r}" for r in ressalvas) + "\n"
    return msg

def montar_mudanca(last, dec, cap, nat, mudancas, ressalvas=None):
    data_fmt  = pd.to_datetime(last["time"]).strftime("%d/%m/%Y")
    mud_txt   = "\n".join(f"• {m}" for m in mudancas)
    msg  = f"🔔 *Mudança detectada:*\n{mud_txt}\n\n"
    msg += f"{dec['emoji']} *BTC Setup — {data_fmt}*\n"
    msg += f"\n*{dec['op']}*\n{dec['mot']}\n\n"
    if dec["cards"]:
        msg += f"✅ *Cards ({len(dec['cards'])})* " + ", ".join(dec["cards"][:4])
        if len(dec["cards"]) > 4: msg += f" +{len(dec['cards'])-4}"
        msg += "\n"
    msg = _append_decision_context(msg, dec, ressalvas)
    msg += "\n" + _corpo(last, dec, cap, nat)
    msg += "\n" + RODAPE
    return msg

def montar_heartbeat(last, dec, cap, nat, estado_ant, ressalvas=None):
    data_fmt = pd.to_datetime(last["time"]).strftime("%d/%m/%Y")
    desde    = estado_ant.get("date", "—")
    msg  = f"✅ *BTC Setup — OK · {data_fmt}*\n"
    msg += f"\n_Sistema rodando · sem mudanças_\n"
    msg += f"\n*{dec['op']}*  _(desde {desde})_\n{dec['mot']}\n\n"
    msg = _append_decision_context(msg, dec, ressalvas)
    msg += "\n" + _corpo(last, dec, cap, nat)
    msg += "\n" + RODAPE
    return msg

# ─── TELEGRAM ───────────────────────────────────────────────────────────────
def enviar(token: str, chat_id: str, texto: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": texto,
                          "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("ok", False)

# ─── MAIN ───────────────────────────────────────────────────────────────────
def main():
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(f"[{ts}] BTC Setup Notify v204.29\n")

    # 1. Dados
    print("1. Carregando dados...")
    df   = carregar_dados()
    df   = calcular(df)
    last = df.iloc[-1]
    dia  = str(last["time"])[:10]
    print(f"  Último dia: {dia} | Preço: ${last['PriceUSD']:,.0f}")

    # 2. Análises
    dec = calcular_decisao(last)
    cap = calcular_cap25(last)
    print(f"  Decisão: {dec['emoji']} {dec['op']}")
    vt  = f"{cap['volMult']:.2f}x" if cap["volMult"] else "—"
    dt  = f"{cap['dd60']*100:.1f}%" if cap["dd60"] is not None else "—"
    print(f"  Cap 25%: ativa={cap['ativa']} vol={vt} dd60={dt}")

    # 3. Estado
    print("\n2. Verificando estado...")
    estado_ant   = carregar_estado()
    estado_atual = extrair_estado(last, dec, cap)
    mudancas     = detectar_mudancas(estado_ant, estado_atual)
    heartbeat    = deve_enviar_heartbeat(estado_ant, dia)

    print(f"  Mudanças: {mudancas}")
    print(f"  Heartbeat: {'sim' if heartbeat else 'não'} (último ping={estado_ant.get('last_daily_ping_date')})")

    nat = natureza_preco(dia)
    ressalvas = calcular_ressalvas(last, dec, estado_ant)
    if ressalvas:
        print(f"  Ressalvas: {ressalvas}")

    # 4. Enviar
    if mudancas:
        print(f"\n3. Enviando mudança ({len(mudancas)})...")
        msg = montar_mudanca(last, dec, cap, nat, mudancas, ressalvas)
        print(f"\n{'─'*50}\n{msg}\n{'─'*50}")
        ok  = enviar(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, msg)
        print(f"Telegram: {'✓ enviado' if ok else '✗ FALHOU'}")
        if not ok:
            raise RuntimeError("Falha ao enviar mensagem")
        # Heartbeat não necessário se houve mudança
        estado_atual["last_daily_ping_date"] = dia

    elif heartbeat:
        print(f"\n3. Enviando heartbeat (nova data: {dia})...")
        msg = montar_heartbeat(last, dec, cap, nat, estado_ant, ressalvas)
        print(f"\n{'─'*50}\n{msg}\n{'─'*50}")
        ok  = enviar(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, msg)
        print(f"Heartbeat: {'✓ enviado' if ok else '✗ FALHOU'}")
        estado_atual["last_daily_ping_date"] = dia

    else:
        print("\n3. Nenhuma mudança, heartbeat já enviado hoje — silêncio.")
        # Preservar last_daily_ping_date anterior para não repetir heartbeat
        estado_atual["last_daily_ping_date"] = estado_ant.get("last_daily_ping_date")

    salvar_estado(estado_atual)
    print("  Estado salvo.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        erro = traceback.format_exc()
        print(f"\n💥 ERRO FATAL:\n{erro}")
        try:
            enviar_erro_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, f"Erro fatal:\n{str(e)}")
        except Exception:
            pass
        raise
