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
- `BINANCE_DASHBOARD_PASSWORD` — senha forte da área privada; protege saldos, operações e ordens porque o site é público. `BINANCE_DASHBOARD_TOKEN` continua aceito apenas como fallback de compatibilidade.

Não habilite Trading, Margin, Futures ou Withdrawals na chave usada pelo painel.

A integração lê BTC/USDT, últimas operações BTCUSDT e calcula custo médio por custo médio ponderado. Se o saldo BTC não reconciliar com o histórico BTCUSDT (por transferências, outras corretoras ou outros pares), o painel marca o custo médio como estimado.

A senha é digitada no modal **Operações e histórico**. O servidor valida a senha e cria um cookie de sessão `HttpOnly`, `Secure` e `SameSite=Strict`; a senha não é guardada em `localStorage`/`sessionStorage`. A sessão dura 30 minutos por padrão (`BINANCE_SESSION_TTL_SECONDS` pode ajustar entre 5 minutos e 12 horas).


### Privacidade e valores reais

- A área **Operações e histórico** fica bloqueada por senha por padrão.
- Sem sessão válida, `/api/binance/account` responde 401 e não retorna saldo, trades nem ordens.
- Com sessão válida, `BTC em posição` recebe o saldo BTC total real (livre + bloqueado) e `USDT disponível` recebe o saldo USDT livre real.
- Ordens abertas BTCUSDT aparecem em modo somente leitura via endpoint USER_DATA da Binance; o painel não contém rota de criar/cancelar ordem.
- O custo médio só é usado no PnL quando o histórico BTCUSDT reconcilia com o saldo BTC real; caso contrário aparece como estimativa e não sobrescreve o preço médio operacional.
