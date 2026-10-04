# btc-setup-notify

O frontend canônico do BTC Setup é sempre `index.html` na raiz do repositório.

## Publicação no Vercel

O workflow `.github/workflows/vercel_deploy.yml` publica `index.html` em produção quando esse arquivo muda na branch `main`, e também pode ser executado manualmente.

Projeto informado no v0: `https://v0.app/luciano777x/btc-setup-notify`.

Configure estes Repository Secrets em **Settings → Secrets and variables → Actions**:

- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`
- `VERCEL_PROJECT_ID`

Os IDs `orgId` e `projectId` podem ser obtidos vinculando localmente o projeto com a Vercel CLI (`vercel link`) e consultando `.vercel/project.json`.

Não renomeie o setup para nomes versionados. A versão funcional permanece no conteúdo/metadata; o arquivo publicado continua sendo `index.html`.
