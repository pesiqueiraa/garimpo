# CLAUDE.md — Garimpo

Instruções e boas práticas para trabalhar neste projeto (lidas pelo Claude Code no início de cada sessão; valem igualmente para humanos).

## Antes de qualquer coisa

1. **`git pull` primeiro, sempre.** O workflow diário do Actions commita `data/garimpo.db` de volta ao repo; trabalhar sem pull gera divergência com binário.
2. Leia `docs/contexto.md` (o que é o projeto, decisões, estado atual) e a fase ativa no `docs/roadmap.md`.
3. **Não implemente nada de fases futuras**, mesmo que pareça rápido ou "já que estou aqui". O escopo da fase ativa é lei. Feature nova entra primeiro no `docs/roadmap.md`, nunca direto no código.
4. Se a tarefa pedida conflitar com a filosofia do projeto (seção 10 do `docs/contexto.md`), aponte o conflito antes de codar.

## Arquitetura e código

- **Pipeline linear e explícito:** `coleta → db → enrich → escava → report → mail`. Cada etapa em seu módulo (`collectors.py`, `db.py`, `enrich.py`, `excavate.py`, `report.py`), orquestrada apenas pelo `main.py`. Não criar dependências laterais entre módulos (ex: collector chamando report).
- **Contrato de dados dos coletores:** todo coletor retorna `list[dict]` com as chaves `name, tagline, url, source, source_url, points, category`. Fonte nova = função nova em `collectors.py` seguindo esse contrato + registro em `collect_all()`. Nada além disso.
- **Falha graciosa é inegociável:** toda chamada externa (API, scraping, LLM, SMTP) em try/except com log `[fonte] mensagem`. Uma fonte quebrada nunca derruba o pipeline. Nunca deixar exceção de rede subir até o `main.py`. A escavação tem 3 níveis: concorrente → dossiê (`record_dig_failure`, 3 tentativas) → etapa (`excavate_all` nunca levanta).
- **Toda credencial é opcional:** ausência de chave = etapa pulada com aviso claro, nunca crash. Padrão existente: `PH_TOKEN`, `OPENAI_API_KEY`, SMTP (`MAIL_FROM` opcional para Resend). Manter esse comportamento em tudo que for novo.
- **Dependências mínimas:** hoje só `requests` e `google-play-scraper` (runtime; pytest fica em `requirements-dev.txt`). Antes de adicionar uma lib, pergunte se 20 linhas de código puro resolvem. Cada adição entra no `requirements.txt` com versão mínima.
- **Simplicidade > abstração:** este é um projeto pessoal de um estudante. Nada de classes abstratas, factories ou camadas "para o futuro". Função simples que funciona hoje ganha de arquitetura elegante para um cenário hipotético.
- Python 3.12 (versão do runner em produção), type hints nos parâmetros e retornos públicos, docstrings curtas em português explicando o *porquê*, nomes de variáveis em inglês.

## Banco de dados

- SQLite único em `data/garimpo.db`, schema definido em `db.py` com `CREATE TABLE IF NOT EXISTS`.
- **Mudança de schema = migração idempotente** (checar se coluna existe antes de `ALTER TABLE`, padrão do `_migrate()`), porque o banco em produção vive no repo e não pode ser recriado do zero.
- Deduplicação é sagrada: tudo que entra passa por `upsert()` com `dedup_key`. Nunca inserir direto.
- Novas entidades seguem o padrão das tabelas da v1.1 (`competitors`, `pain_clusters`, `dossiers`): schema no `db.py`, funções de acesso no `db.py`, nada de SQL espalhado pelos outros módulos.
- Reviews cruas da Play Store NÃO são persistidas — só o destilado (clusters com trechos curtos).

## LLM (enriquecimento e dossiês)

- **Toda chamada LLM passa por `app/llm.py`** (`ask_json`): JSON garantido com retry, remoção de cerca markdown e contagem de tokens. Nada de `requests.post` direto para a API em outros módulos.
- Modelo padrão: OpenAI `gpt-5-mini` com `reasoning_effort` mínimo (classificação simples não justifica modelo caro; o ângulo de ataque usa `medium`). Trocar de modelo só com justificativa de qualidade documentada.
- **Custo é requisito:** teto de US$0,25 por dossiê (aborta e marca `failed`), máx. 2 dossiês/dia, custo real gravado em `dossiers.cost_usd`. Meta: centavos por dia. Se uma feature estourar isso, ela ganha fila/limite diário (padrão da fila de escavação).
- O LLM classifica e resume; **não inventa fatos**. Veredito de concorrência sem busca real deve ser marcado como palpite (`desconhecido` na dúvida). Afirmação factual no dossiê precisa de evidência (link) ou de rótulo de estimativa.
- Conteúdo vindo de LLM ou scraping que vai para o HTML do e-mail passa por `html.escape` (ou coerção de tipo, ex: `_to_int` para frequency) — reviews raspadas são entrada potencialmente hostil.

## Scraping e APIs externas (ética e robustez)

- Ordem de preferência: API oficial > RSS/endpoint público > scraping. Scraping só leve: 1x/dia, pausas entre requisições (1,5s na Play Store), limites de volume (~70 reviews por estrela, 1-3★), User-Agent identificado.
- Respeitar termos de uso e rate limits. Em dúvida sobre uma fonte, coletar menos.
- Nunca coletar dados pessoais de usuários; reviews são usados de forma agregada (clusters de temas), e trechos citados em dossiê ficam anônimos.

## Testes e validação

- Suíte pytest em `tests/` (banco SQLite real em arquivo temporário, sem rede): `python3 -m pip install -r requirements-dev.txt` e `python3 -m pytest` **a partir da raiz** (garante `app` no sys.path).
- Antes de commitar mudança de pipeline, rodar de ponta a ponta: `set -a && source .env && set +a && python3 -m app.main --no-mail` e abrir o HTML gerado em `data/`.
- Lógica nova de dados (dedup, clusterização, score) deve ser testável com dados simulados sem rede — mockar as funções de borda (`llm.ask_json`, `gp_search`), nunca a lógica.
- Coletores novos: testar com resposta real 1x manualmente, depois garantir que a falha da fonte é graciosa (simular timeout).

## Git e operação

- Commits pequenos e descritivos em português, com prefixo de versão quando fizer parte de uma entrega (padrão real do histórico: `v1.1: garante pausa entre lojas na busca Play Store`).
- Nunca commitar credenciais; tudo via env/secrets (`.env` local, Secrets no Actions). `data/*.html` não é versionado; `data/garimpo.db` é versionado de propósito (o Actions commita).
- Mudou variável de ambiente, comando ou setup? Atualizar `README.md` no mesmo commit. Mudou decisão de arquitetura ou estado do projeto? Atualizar `docs/contexto.md`.
- O workflow do Actions é a produção: mudanças no pipeline devem funcionar tanto local quanto no runner (sem caminhos absolutos, sem dependência de estado local).

## E-mail e relatório

- O e-mail diário é o produto. Regras: legível no celular, HTML inline (sem CSS externo), degradar bem sem enriquecimento (cards saem sem score/veredito, nunca vazios ou quebrados; seção de dossiês só aparece se houver dossiê).
- Texto em português; tom direto; nada de encher o relatório — melhor 5 itens bons que 30 medianos. O score decide o corte.

## O que NÃO fazer (anti-padrões deste projeto)

- ❌ Adicionar feature fora da fase ativa "aproveitando o embalo".
- ❌ Refatorar grande sem necessidade concreta ("melhorar a arquitetura" não é tarefa).
- ❌ Adicionar framework/ORM/fila/infra para problema que SQLite + cron resolvem.
- ❌ Quebrar o contrato dos coletores ou espalhar SQL fora do `db.py`.
- ❌ Fazer o pipeline depender de credencial obrigatória.
- ❌ Prometer precisão que não existe (score e TAM são heurísticas — rotular como tal).
- ❌ Durante o congelamento de 30 dias pós-MVP: qualquer código novo. Se o dono pedir, lembrá-lo do combinado (ele mesmo escreveu no `docs/roadmap.md`).
