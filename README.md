# btc-setup-notify

O frontend canônico do BTC Setup é sempre `index.html` na raiz do repositório.

## Publicação no Vercel

O workflow `.github/workflows/vercel_deploy.yml` publica `index.html` e as funções em `api/` em produção quando esses arquivos mudam na branch `main`, e também pode ser executado manualmente.

Projeto informado no v0: `https://v0.app/luciano777x/btc-setup-notify`.

O projeto Vercel alvo já está fixado no workflow. Configure somente o Repository Secret:

- `VERCEL_TOKEN`

Não renomeie o setup para nomes versionados. A versão funcional permanece no conteúdo/metadata; o arquivo publicado continua sendo `index.html`.

## Binance — somente leitura

A tela **Operações e histórico** consulta a Binance por `/api/binance/account`. A API Secret fica exclusivamente no Vercel e nunca é enviada ao `index.html`.

Configure no projeto Vercel, preferencialmente em Production, como variáveis sensíveis:

- `BINANCE_API_KEY` — chave Binance com **Enable Reading**.
- `BINANCE_API_SECRET` — secret correspondente.
- `BINANCE_DASHBOARD_TOKEN` — senha aleatória própria do painel; protege os saldos/histórico porque o site pode ser público.

Não habilite Trading, Margin, Futures ou Withdrawals na chave usada pelo painel.

A integração lê BTC/USDT, últimas operações BTCUSDT e calcula custo médio por custo médio ponderado. Se o saldo BTC não reconciliar com o histórico BTCUSDT (por transferências, outras corretoras ou outros pares), o painel marca o custo médio como estimado.

O token do painel é digitado no modal **Operações e histórico** e fica apenas em `sessionStorage` até fechar a sessão do navegador.
