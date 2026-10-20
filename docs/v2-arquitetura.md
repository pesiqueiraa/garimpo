# Garimpo v2 — arquitetura

Documento de referência da v2. Descreve as decisões, o modelo de dados e os
contratos entre módulos. Quem for mexer no código lê isto antes do `CLAUDE.md`
detalhar as regras do dia a dia.

## Por que a v2 existe

Seis semanas de uso da v1 (12/07 a 22/08/2026) mostraram problemas concretos:

| Sintoma na v1 | Causa | Resposta da v2 |
|---|---|---|
| 405 de 519 produtos com score exatamente 6 ou 7 | Uma chamada de LLM dando nota 0-10 sem rubrica | O LLM só extrai fatos; a nota é calculada em código a partir de subnotas |
| 71 dossiês e nenhum kit | Kit exigia score final ≥ 8, que nunca saía | Kit também para o melhor dossiê da semana |
| Dossiês de DeepSeek, LLM em FPGA, visualizador do HuggingFace | Nada filtrava o que não é SaaS tropicalizável | Flags de descarte na extração; descartado não entra na fila |
| Product Hunt com 1 item em 6 semanas | Fonte quebrada em silêncio | Registro por execução + alerta de saúde por fonte no e-mail |
| "Ainda parece cru" | Só lançamentos do dia; nada de tração nem acompanhamento | Empresa como unidade, sinais de tração e revisitas em D+30/90/180 |

## Os quatro princípios

1. **A unidade é a empresa, não o lançamento.** Lançamento é um *evento* de uma empresa.
2. **O LLM extrai fatos e escreve texto; a nota é calculada em código.**
3. **Não há pressa no dia do lançamento.** Revisitas mostram quem ganhou tração.
4. **O sistema aprende com o dono.** Feedback pelo e-mail e pelas Issues muda status e o ajuste pessoal do score.

## Fluxo

Diário (`python -m app rodar`):

```
coleta (sources/) → resolução (resolve.py) → sondagem do site (signals/site, wayback)
→ extração de fatos (extract.py, LLM) → score prévio (score.py)
→ sinais de tração dos candidatos (signals/) → score prévio de novo
→ leitura de feedback (feedback.py) → fila (queue.py) → escavação (dig/)
→ revisitas do dia (revisit.py) → Issues (deliver/issues.py) → e-mail diário
```

Aos domingos (fuso de Brasília) o mesmo comando também roda: tendências
(`trends.py`), kit da semana (`dig.weekly_kit`) e e-mail semanal.

Cada etapa roda dentro de `try/except` no `cli.py`. Etapa que falha é
registrada na tabela `runs` e o pipeline segue — o e-mail sempre sai.

## Mapa de módulos

```
app/
  __main__.py        python -m app <comando>
  cli.py             subcomandos e orquestração das etapas
  main.py            atalho de compatibilidade da v1 (--no-mail, --investigar)
  config.py          lê config/*.toml (tomllib, sem dependência nova)
  db/                TODO o SQL do projeto mora aqui
    core.py          conexão, migrações (PRAGMA user_version), utilitários
    companies.py     empresas, eventos, fusão de empresas
    signals.py       observações e páginas de site
    analysis.py      extrações, scores, embeddings, tendências, cache do LLM
    digs.py          fila, dossiês, concorrentes de loja, dores, concorrentes BR
    feedback.py      sinais do dono
    runs.py          registro de execuções
  migrations/        001_v1_base.py, 002_v2_schema.sql, 003_v2_backfill.py, 004_v2_tracking.sql
  resolve.py         URL → site_key/domínio; ingestão dos itens coletados
  llm.py             única porta para a OpenAI (JSON, schema, cache, orçamento, embeddings)
  prompts/           prompts versionados (o nome do arquivo é a versão)
  extract.py         extração de fatos com LLM
  score.py           subnotas, pesos, descarte, catalisadores (funções puras)
  queue.py           fila de escavação com orçamento, prioridade e expiração
  sources/           fontes de descoberta (retornam itens)
  signals/           sinais de tração por empresa (retornam observações)
  dig/               escavação → dossiê
  revisit.py         revisitas D+30/90/180 e promoção
  trends.py          embeddings + agrupamento semanal
  feedback.py        lê Issues do GitHub e registra sinais do dono
  health.py          alertas de saúde das fontes (lógica pura)
  evaluate.py        golden set: amostra e avaliação do score
  deliver/           e-mails diário e semanal, Issues, SMTP
config/
  fontes.toml        fontes ligadas, limites, orçamento
  pesos.toml         pesos das subnotas, limiares, descarte
  catalisadores.toml mudanças regulatórias brasileiras
  perfil.toml        preferências pessoais (B2B, complexidade...)
scripts/
  build_cnae_counts.py   dados abertos do CNPJ → data/cnae_counts.csv (manual)
data/
  garimpo.db         em produção vive no branch `data` (o Actions baixa e sobe)
  golden.csv         golden set rotulado pelo dono
  cnae_counts.csv    contagem de empresas ativas por CNAE e UF (opcional)
```

Regras herdadas da v1 que continuam valendo: funções simples (nada de classes
abstratas ou factories), falha graciosa em toda chamada externa com log
`[fonte] mensagem`, toda credencial opcional, dependências só `requests` e
`google-play-scraper`, nomes de variáveis em inglês e docstrings em português.

## Modelo de dados

Tabelas da v2 (as da v1 viram `legacy_*` na migração 002 e ficam só para consulta):

| Tabela | Conteúdo |
|---|---|
| `companies` | uma linha por empresa; `site_key` único, `domain` registrável, `status` |
| `events` | lançamentos e listagens (`source`, `kind`, `external_id`, `points`, `payload`) |
| `observations` | medições ao longo do tempo (`metric`, `value_num`, `value_text`, `source`, `observed_at`) — só recebe INSERT, e só quando o valor muda (registro de mudanças) |
| `site_pages` | última leitura do site e da página de preços de cada empresa |
| `extractions` | fatos extraídos pelo LLM, com `prompt_version` e `model` |
| `scores` | `kind` = prior, posterior ou legacy; `subscores` e `evidence` em JSON |
| `dossiers` | um por escavação; veredito, evidências, LatAm, reclamações, TAM, catalisadores, kit, `issue_number` |
| `app_competitors` | concorrentes confirmados na Play Store e na App Store |
| `pain_clusters` | temas de dor das avaliações negativas |
| `br_competitors` + `br_competitor_links` | mapa permanente do mercado brasileiro, reaproveitado entre escavações |
| `feedback` | sinais do dono (like, dislike, watch, interesting, validating, discarded, building) |
| `embeddings` | vetor (256 dimensões, float32) por empresa |
| `trends` | agrupamentos semanais com rótulo e crescimento |
| `llm_cache` | respostas do LLM por hash de prompt + modelo |
| `runs` | uma linha por execução: contagem por fonte, custo, erros |

### `site_key` e domínio

- `site_key` identifica a empresa: domínio registrável (`acme.com`), ou host +
  caminho em plataformas compartilhadas (`github.com/fulano/projeto`), ou
  `name:<nome normalizado>` quando não há URL.
- `domain` é o domínio registrável usado pelos sinais (vagas, Tranco). Fica
  vazio em plataformas compartilhadas.
- Se o site redireciona para outro domínio **com cara de ser da mesma empresa**
  (nome ou domínio parecidos, ou link de redirect do Product Hunt), a chave muda
  e, se o destino já existe, as empresas são fundidas (`db.merge_companies`,
  que mantém o status mais forte e registra a fusão no log). Redirect para
  terceiro sem relação (SSO, hospedagem) é ignorado.
- A ingestão procura primeiro o evento (`source` + `external_id`): depois de um
  redirect, o mesmo item da fonte volta para a mesma empresa, sem duplicar.

### Status da empresa

`new` → `watching` (score prévio alto ou marcado pelo dono) → `queued` → `dug`.
Saídas laterais: `discarded` (descarte automático ou do dono), `expired` (90
dias na fila sem tração), `promoted` (revisita mostrou tração maior; volta para
a fila com prioridade), `validating` e `building` (vindos do dono).

- Sair da fila (qualquer status diferente de `queued`) limpa `queued_at` e a
  prioridade: quem volta ganha prazo novo.
- `promoted_at` guarda a promoção; o bônus na fila vale até a empresa ser escavada.
- A revisita compara a tração de agora com a da revisita anterior
  (`revisit_traction`) ou, na primeira, com a do primeiro score — os sinais
  diários já atualizam a tração antes de a revisita chegar.

### Métricas de `observations`

| Métrica | Fonte |
|---|---|
| `mrr_usd`, `revenue_30d_usd`, `growth_mom_pct`, `for_sale` | trustmrr |
| `team_size`, `yc_status`, `yc_batch`, `is_hiring` | yc |
| `open_jobs` | greenhouse / lever / ashby |
| `tranco_rank` | tranco |
| `formd_filings`, `formd_last` | sec_formd |
| `domain_first_seen` | wayback |
| `price_min_usd`, `has_free_plan`, `sales_motion`, `pricing_hash` | site |
| `appstore_rank_us`, `appstore_rank_br` | appstore |

## Contratos entre módulos

### Fontes (`app/sources/*.py`)

`collect(cfg: dict) -> list[dict]`. Nunca levanta. Cada item:

```python
{"name": str, "tagline": str | None, "url": str | None,
 "source": str, "kind": "launch" | "listing" | "ranking",
 "external_id": str, "source_url": str | None,
 "points": int, "category": str | None,
 "observations": [{"metric": str, "value_num": float | None, "value_text": str | None}]}
```

`observations` é opcional e entra como sinal de tração da empresa (ex.: MRR do
TrustMRR, tamanho de equipe do YC).

### Sinais (`app/signals/*.py`)

`lookup(company, cfg: dict) -> list[dict]` com observações
`{"metric", "value_num", "value_text", "source"}`. Nunca levanta.
`signals/site.py` tem também `probe(url) -> dict | None` com `final_url`,
`title`, `text`, `pricing_url`, `pricing_text`, `pricing_hash` e `features`.

### Extração (`app/extract.py`)

Prompt `prompts/extract_v2.txt`, saída validada contra `EXTRACT_SCHEMA`. Campos:
`summary_pt`, `niche`, `pain`, `buyer`, `audience`, `business_type`,
`pricing_model`, `sales_motion`, `technical_complexity`, `local_rails`
(payments, invoicing_tax, messaging, banking_open_finance,
government_integration, language_content, local_regulation), `kill_flags`,
`br_cnae_guess`, `br_customer_segment`, `search_keywords_pt`,
`search_keywords_es`, `br_competitor_guess`.

### Score (`app/score.py`)

`compute(extraction, observations, sources, max_points, affinity, cfg, catalysts, verdict=None) -> dict`
devolve `{"total", "subscores", "kill_reason", "evidence", "catalysts"}`.

Subnotas de 1 a 5:

- **traction** — o sinal mais forte entre MRR, equipe, vagas, Tranco, Form D,
  pontos e múltiplas fontes, com bônus quando há vários sinais fortes.
- **tropicalizability** — quantos trilhos locais o modelo exige (pagamento,
  fiscal, mensageria, Open Finance, governo, idioma, regulação).
- **timing** — cruzamento com `config/catalisadores.toml` (3 = neutro).
- **fit** — `config/perfil.toml` (público, motion de venda, complexidade) mais a
  afinidade com o que o dono curtiu ou descartou.
- **gap** — no prévio, o palpite do LLM (peso menor); no posterior, o veredito
  da busca real.

`total = Σ peso × (subnota − 1) / 4 × 10 / Σ pesos`, em 0-10. Qualquer flag de
descarte ativa em `pesos.toml` zera o total e preenche `kill_reason`.

### LLM (`app/llm.py`)

`configure(conn=None, budget_usd=None)`, `ask_json(prompt, max_tokens, reasoning_effort, schema=None, cache=True) -> (data, tokens_in, tokens_out)`,
`embed(texts, dims=256) -> list[list[float]]`, `load_prompt(name) -> str`,
`usage() -> dict`, `cost_usd(tokens_in, tokens_out)`.
Levanta `LLMError` (sem chave ou resposta inválida) e `BudgetExceeded` (teto da
execução atingido).

### Escavação (`app/dig/`)

`run(conn, cfg) -> int` escava a fila do dia; `dig_company(conn, company_id, cfg) -> int | None`
devolve o id do dossiê; `weekly_kit(conn, cfg) -> int | None` gera o kit do
melhor dossiê da semana. Nada levanta para fora.

### Entrega (`app/deliver/`)

`daily.build(conn, cfg) -> (subject, html)`, `weekly.build(conn, cfg) -> (subject, html)`,
`issues.sync(conn, cfg) -> int`, `mail.send(html, subject) -> bool`.

## Operação

- Cron às 08:37 UTC (05:37 em Brasília): fora da hora cheia e com folga para
  atrasos do GitHub Actions antes das 7h.
- O banco vive no branch `data`. O workflow baixa `garimpo.db` de lá, roda e
  commita de volta. Na primeira execução, sem o branch, usa o `data/garimpo.db`
  do `main` e cria o branch.
- Teto de custo por execução em `fontes.toml` (`[orcamento]`); estourou, as
  etapas de LLM param e o resto segue.
- `payload` dos eventos é apagado após 90 dias e o banco passa por `VACUUM` ao
  final de cada execução.
- Issues precisam de `GITHUB_TOKEN` com `issues: write` (o workflow já pede).
- Cada etapa do `rodar` commita só quando termina; exceção desfaz o que ficou
  pela metade, e Ctrl+C/timeout desfaz e encerra. Antes de subir o banco, o
  workflow roda `PRAGMA integrity_check` (abrir o banco desfaz journal pendente);
  banco corrompido não sobe.
- O e-mail diário mostra só o que surgiu depois da execução anterior terminada.
- Form D só é consultado com contato real no User-Agent (`SEC_CONTACT` ou `MAIL_TO`).
