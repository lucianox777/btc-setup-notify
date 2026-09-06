"""Entrada do BTC Setup Notify com formatação Telegram v204.30.

Mantém toda a lógica de cálculo/estado do btc_setup_notify.py e altera apenas a
apresentação da mensagem:
- Cards também aparecem no heartbeat;
- VFL em linha própria logo abaixo dos Cards;
- remove o segundo VFL do bloco de mercado;
- junta Fechamento e FR na mesma linha;
- remove o rótulo repetido de data do fechamento;
- compacta os rótulos das linhas 1,05 / 0,96 / 0,93 / VFL regime.
"""

import pandas as pd

import btc_setup_notify as base


RODAPE = "_BTC Setup v204.30 · GitHub Actions_"
_ORIGINAL_CORPO = base._corpo


def _linha_cards(dec: dict) -> str:
    cards = dec.get("cards") or []
    if not cards:
        return ""
    txt = f"✅ *Cards ({len(cards)})* " + ", ".join(cards[:4])
    if len(cards) > 4:
        txt += f" +{len(cards) - 4}"
    return txt + "\n"


def _linha_vfl(dec: dict) -> str:
    return ("🟢" if dec.get("vfl") == 1 else "🔴") + " *VFL*\n"


def _corpo(last, dec, cap, nat):
    """Reaproveita o corpo canônico e altera somente o bloco visual de mercado."""
    original = _ORIGINAL_CORPO(last, dec, cap, nat)
    linhas = original.splitlines()

    # Tudo a partir da linha verde continua canônico; preço/FR são remontados acima.
    inicio = next(
        (i for i, linha in enumerate(linhas)
         if linha.startswith("🟢 *Linha verde diária 1,05:")),
        None,
    )
    if inicio is None:
        raise RuntimeError("Formato inesperado em _corpo(): linha verde diária não encontrada")

    preco = float(last["PriceUSD"])
    fr = float(dec["fr"])
    saida = [f"💰 *Fechamento:* ${preco:,.0f}  |  📊 *FR:* {fr:.4f}"]

    # Preserva apenas avisos reais (ex.: dados desatualizados), não o rótulo repetido
    # "fechamento DD/MM", cuja data já está no cabeçalho da mensagem.
    if nat.get("aviso"):
        saida.append(str(nat["aviso"]))

    resto = linhas[inicio:]
    trocas = {
        "🟢 *Linha verde diária 1,05:*": "🟢 *1,05 / redução:*",
        "🟡 *Linha dourada diária 0,96:*": "🟡 *0,96 / recompra:*",
        "🔵 *Linha azul diária 0,93 / reversão:*": "🔵 *0,93 / reversão:*",
        "🔁 *Linha VFL diária/regime:*": "🔁 *VFL regime:*",
    }
    for linha in resto:
        for antigo, novo in trocas.items():
            if linha.startswith(antigo):
                linha = novo + linha[len(antigo):]
                break
        saida.append(linha)

    return "\n".join(saida) + "\n"


def montar_mudanca(last, dec, cap, nat, mudancas, ressalvas=None):
    data_fmt = pd.to_datetime(last["time"]).strftime("%d/%m/%Y")
    mud_txt = "\n".join(f"• {m}" for m in mudancas)

    msg = f"🔔 *Mudança detectada:*\n{mud_txt}\n\n"
    msg += f"{dec['emoji']} *BTC Setup — {data_fmt}*\n"
    msg += f"\n*{dec['op']}*\n{dec['mot']}\n\n"
    msg += _linha_cards(dec)
    msg += _linha_vfl(dec)
    # Sem linha em branco entre Cards, VFL e Bloqueios/Ressalvas.
    msg = base._append_decision_context(msg, dec, ressalvas)
    msg += "\n" + _corpo(last, dec, cap, nat)
    msg += "\n" + RODAPE
    return msg


def montar_heartbeat(last, dec, cap, nat, estado_ant, ressalvas=None):
    data_fmt = pd.to_datetime(last["time"]).strftime("%d/%m/%Y")
    desde = estado_ant.get("date", "—")

    msg = f"✅ *BTC Setup — OK · {data_fmt}*\n"
    msg += "\n_Sistema rodando · sem mudanças_\n"
    msg += f"\n*{dec['op']}*  _(desde {desde})_\n{dec['mot']}\n\n"
    msg += _linha_cards(dec)
    msg += _linha_vfl(dec)
    msg = base._append_decision_context(msg, dec, ressalvas)
    msg += "\n" + _corpo(last, dec, cap, nat)
    msg += "\n" + RODAPE
    return msg


def main():
    # A lógica permanece no módulo canônico; substituímos somente renderizadores.
    base._corpo = _corpo
    base.montar_mudanca = montar_mudanca
    base.montar_heartbeat = montar_heartbeat
    base.RODAPE = RODAPE
    base.main()


if __name__ == "__main__":
    main()
