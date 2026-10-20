# CLAUDE.md — Garimpo

Instruções e boas práticas para trabalhar neste projeto (lidas pelo Claude Code no início de cada sessão; valem igualmente para humanos).

## Antes de qualquer coisa

1. **`git pull` primeiro, sempre.** Na v2 o banco de produção vive no branch `data` (o Actions commita lá). Para trabalhar com dados reais: `python -m app baixar-banco`.
2. Leia `docs/v2-arquitetura.md` (módulos, schema, contratos), `docs/contexto.md` (o que é o projeto e por quê) e a fase ativa no `docs/roadmap.md`.
3. **Não implemente nada de fases futuras**, mesmo que pareça rápido. Feature nova entra primeiro no `docs/roadmap.md`, nunca direto no código.
4. Se a tarefa conflitar com a filosofia do projeto (seção 10 do `docs/contexto.md`), aponte o conflito antes de codar.

## Arquitetura e código

- **Etapas isoladas, orquestradas só pelo `app/cli.py`:** coleta (`sources/`) → resolução (`resolve.py`) → site (`signals/site.py`) → extração (`extract.py`) → score (`score.py`) → sinais (`signals/`) → feedback → fila (`queue.py`) → escavação (`dig/`) → revisitas → Issues e e-mail (`deliver/`). Cada etapa roda num `try/except` do `Pipeline` e grava estatística/erro na tabela `runs`. Nada de dependência lateral (ex.: fonte chamando o e-mail).
- **Contrato das fontes:** `collect(cfg) -> list[dict]` com `name, tagline, url, source, kind, external_id, source_url, points, category` e `observations` opcional. Fonte nova = módulo novo em `app/sources/` + registro no `REGISTRY` + chave em `config/fontes.toml`.
- **Contrato dos sinais:** `lookup(company, cfg) -> list[observação]` com métricas da tabela do `docs/v2-arquitetura.md`. Métrica nova entra primeiro nessa tabela.
- **O LLM extrai fatos; a nota é calculada em código** (`score.py`, funções puras). Não peça nota ao LLM. Ajuste de pesos e limiares é em `config/pesos.toml` / `config/perfil.toml`, medido com `python -m app avaliar`.
- **Falha graciosa é inegociável:** toda chamada externa (API, scraping, LLM, SMTP, GitHub) em try/except com log `[modulo] mensagem`. Uma fonte quebrada nunca derruba o pipeline e o e-mail sempre sai. A escavação aborta só o dossiê que estourou o teto; `llm.BudgetExceeded` para as etapas de LLM da execução.
- **Toda credencial é opcional:** ausência de chave = etapa pulada com aviso claro, nunca crash (`OPENAI_API_KEY`, `SERPER_API_KEY`, `PH_TOKEN`, `GITHUB_TOKEN`, `SEC_CONTACT`, SMTP).
- **Dependências mínimas:** `requests` e `google-play-scraper` em runtime (config em TOML via `tomllib` da stdlib). Antes de adicionar uma lib, pergunte se 20 linhas de código puro resolvem.
- **Simplicidade > abstração:** funções simples, nada de classes abstratas, factories ou camadas "para o futuro". A refatoração da v2 foi decisão explícita do dono (ver roadmap); refatoração grande nova continua precisando de motivo concreto.
- Python 3.12 (versão do runner), type hints nos parâmetros e retornos públicos, docstrings curtas em português explicando o *porquê*, nomes de variáveis em inglês.

## Banco de dados

- SQLite em `data/garimpo.db` localmente; em produção, no branch `data`.
- **TODO o SQL mora em `app/db/`** (pacote reexportado como `from app import db`). Nada de SQL em outros módulos.
- **Mudança de schema = migração nova** em `app/migrations/NNN_nome.sql` ou `.py` (função `run(conn)`), aplicada em ordem e registrada em `PRAGMA user_version`. Nunca edite uma migração já aplicada em produção.
- A empresa é a unidade (`companies.site_key`). Tudo que entra passa por `resolve.ingest()`. Nunca inserir empresa direto.
- `observations` só recebe INSERT, e só quando o valor muda — é o histórico que permite acompanhar tração sem inchar o banco (ele vive no git).
- As tabelas `legacy_*` são a v1 congelada: só leitura.
- Reviews cruas não são persistidas — só o destilado (clusters com trechos curtos e anônimos).

## LLM

- **Toda chamada passa por `app/llm.py`** (`ask_json`, `embed`): JSON garantido, JSON Schema quando houver, cache por hash, contagem de tokens e teto por execução. Nada de `requests.post` direto para a API em outros módulos.
- Prompts ficam em `app/prompts/<nome>_vN.txt`. Mudou o prompt de forma relevante? Arquivo novo com versão nova (a versão vai para `extractions.prompt_version`).
- Modelo padrão `gpt-5-mini` com `reasoning_effort` mínimo; ângulo de ataque e kit usam `medium`. Trocar de modelo só com ganho medido no golden set.
- **Custo é requisito:** teto por execução (`[orcamento]`), teto por dossiê (`[escavacao].cost_ceiling_usd`), custo real gravado em `runs.cost_usd` e `dossiers.cost_usd`. Meta: centavos por dia.
- O LLM classifica e resume; **não inventa fatos**. Links de evidência só valem se vieram dos resultados de busca (o código descarta os outros). TAM e score são estimativas e aparecem rotulados assim.
- Tudo que vem de LLM, scraping ou banco e vai para HTML passa por `html.escape`.

## Scraping e APIs externas (ética e robustez)

- Ordem de preferência: API oficial > RSS/endpoint público > scraping. Scraping só leve: 1x/dia, pausas entre requisições, limites de volume, User-Agent identificado (`[sinais].user_agent`).
- Respeitar termos de uso e rate limits. Em dúvida sobre uma fonte, coletar menos ou desligar em `config/fontes.toml`.
- Nunca coletar dados pessoais. "Onde achar entrevistados" aponta lugares (associações, grupos), nunca pessoas.

## Testes e validação

- `python3 -m pip install -r requirements-dev.txt` e `python3 -m pytest` **a partir da raiz**. Os testes usam banco SQLite real em arquivo temporário e **bloqueiam a rede** (`tests/conftest.py`): toda chamada externa precisa de mock.
- Mocke só as bordas (`requests.*`, `llm.ask_json`, `llm.embed`, `google_play_scraper`), nunca a lógica.
- Antes de commitar mudança de pipeline: `python -m app rodar --sem-email` com o banco real (`baixar-banco`) e abrir o HTML gerado em `data/`.
- Fonte ou sinal novo: testar com resposta real 1x manualmente e garantir falha graciosa (timeout, 500, formato inesperado).
- Mudou prompt, peso ou perfil: `python -m app avaliar` antes e depois.

## Git e operação

- Commits pequenos e descritivos em português, com prefixo de versão quando fizer parte de uma entrega (ex.: `v2: ...`).
- Nunca commitar credenciais; tudo via env/secrets. `data/*.html` não é versionado.
- Mudou variável de ambiente, comando ou setup? Atualizar `README.md` no mesmo commit. Mudou arquitetura, schema ou contrato? Atualizar `docs/v2-arquitetura.md`. Mudou o estado do projeto? `docs/contexto.md`.
- O workflow do Actions é a produção: tudo precisa funcionar local e no runner (sem caminhos absolutos, sem estado local).

## E-mail e relatório

- O e-mail é o produto: legível no celular, HTML inline, seções só aparecem quando têm conteúdo. O diário é curto (destaques, dossiês, promovidas, saúde); o semanal traz tendências, tração, kit da semana, fila e feedback.
- Texto em português; tom direto; melhor 5 itens bons que 30 medianos.

## O que NÃO fazer (anti-padrões deste projeto)

- ❌ Adicionar feature fora da fase ativa "aproveitando o embalo".
- ❌ Pedir nota ao LLM ou espalhar SQL fora de `app/db/`.
- ❌ Adicionar framework/ORM/fila/infra para problema que SQLite + cron resolvem.
- ❌ Quebrar o contrato das fontes/sinais ou fazer o pipeline depender de credencial obrigatória.
- ❌ Prometer precisão que não existe (score, TAM e veredito são heurísticas — rotular como tal).
- ❌ Deixar o Garimpo virar "produto procrastinador": se o dono pedir feature nova sem ter usado os dossiês para validar nada, lembre-o da regra de ouro do roadmap.
