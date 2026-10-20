# 📖 CONTEXT.md — Garimpo

> Documento de contexto do projeto. Serve para (re)situar qualquer pessoa — ou IA assistente — que for trabalhar no código. Mantenha atualizado a cada mudança relevante.

---

## 1. O que é o Garimpo

Ferramenta pessoal que monitora diariamente lançamentos de SaaS no exterior (Show HN, Product Hunt, Y Combinator), deduplica, classifica com IA e envia por e-mail as melhores oportunidades de **tropicalização** — adaptar para o Brasil produtos validados lá fora que ainda não têm player local.

Em uma frase: **um radar diário de oportunidades de SaaS para o mercado brasileiro.**

## 2. Por que existe (motivação)

- O dono do projeto é um estudante de tecnologia que quer empreender com SaaS no Brasil.
- A estratégia escolhida é a tropicalização (precedentes: iFood, 99, Contabilizei): pegar modelos validados no exterior e adaptar às peculiaridades locais — Pix, WhatsApp como canal principal, nota fiscal, Simples Nacional.
- O gargalo dessa estratégia é o garimpo manual: acompanhar Product Hunt, HN, YC e newsletters todo dia não escala. O Garimpo automatiza esse funil.
- Objetivo secundário: o projeto em si é aprendizado prático (APIs, LLM, automação) e, se provar valor, é ele próprio tropicalizável como produto (referências gringas: GummySearch, Exploding Topics).

## 3. Como funciona (pipeline)

```
coleta → resolução por empresa → leitura do site → fatos (LLM) → score em código
→ sinais de tração → feedback → fila → escavação → revisitas → Issues → e-mail
```

1. **Coleta** — fontes de descoberta (Show HN, Launch HN, Product Hunt, lotes novos do YC) e fontes com prova de tração (empresas antigas do YC que cresceram, TrustMRR com receita verificada, gap do top faturamento da App Store EUA × Brasil).
2. **Resolução** — tudo vira evento de uma **empresa** (`site_key`: domínio registrável, ou host + caminho em plataformas como GitHub, ou nome normalizado). Redirects do Product Hunt são resolvidos ao ler o site e fundem duplicatas.
3. **Leitura do site** — home e página de preços: texto para o LLM, preço mínimo, plano grátis, venda self-serve ou por demo, hash da página de preços (detecta mudança de preço). Wayback dá a idade do domínio.
4. **Fatos (LLM)** — extração com JSON Schema: o que é, quem paga, público, tipo de negócio, trilhos locais exigidos (pagamento, fiscal, WhatsApp, Open Finance, governo, idioma, regulação), flags de descarte, CNAE do cliente brasileiro, palavras-chave em português e espanhol. **O LLM não dá nota.**
5. **Score em código** — subnotas de 1 a 5 (tração, tropicalizabilidade, timing, fit, gap) ponderadas em 0-10; descarte zera. Pesos em `config/pesos.toml`, preferências em `config/perfil.toml`, catalisadores em `config/catalisadores.toml`.
6. **Sinais de tração** — para as candidatas: tamanho de equipe e status no YC, vagas abertas (Greenhouse/Lever/Ashby), ranking Tranco, captações na SEC (Form D).
7. **Feedback** — 👍 👎 👀 do e-mail e labels `status:*` das Issues mudam o status e ajustam o fit por similaridade (embeddings) com o que o dono curtiu ou descartou.
8. **Fila e escavação** — fila com prioridade, orçamento e expiração. A escavação gera o dossiê: concorrentes na Play Store e App Store com avaliações 1-3★, busca no Google BR, Reclame Aqui, G2 e América Latina, mapa permanente de concorrentes brasileiros, dores clusterizadas, TAM por CNAE (opcional), veredito com evidências, score posterior e ângulo de ataque. Kit de validação para nota ≥ 8 e para o melhor dossiê da semana.
9. **Revisitas** — D+30, D+90 e D+180: site, sinais e score de novo; quem ganhou tração é promovido para a fila.
10. **Entrega** — dossiês viram Issues no GitHub; e-mail diário curto e, aos domingos, e-mail semanal com temas em alta (agrupamento por embeddings), quem ganhou tração, kit da semana, fila, feedback e saúde das fontes.
11. **Automação** — GitHub Actions às 05:37 de Brasília; o banco vive no branch `data`.

## 4. Stack e decisões técnicas

| Decisão | Escolha | Racional |
|---|---|---|
| Linguagem | Python 3.12 | Ecossistema de scraping/dados; familiaridade |
| Banco | SQLite no branch `data` | Zero infra; histórico do código limpo; migrações numeradas |
| LLM | OpenAI gpt-5-mini (+ text-embedding-3-small) | Extração e escavação baratas; JSON Schema; cache por hash |
| Nota | Código (`score.py`) | A v1 com nota do LLM colapsou 405 de 519 produtos em 6 ou 7 |
| Config | TOML (`tomllib`) | Editável à mão, sem dependência nova |
| Envio | SMTP via Resend (ou Gmail) | Gratuito para volume baixo |
| Dossiês | Issues do GitHub | Labels viram feedback; quadro de projeto de graça |
| Automação | GitHub Actions (cron) | Gratuito, sem servidor |
| Busca | Serper.dev | Google BR, Reclame Aqui, G2 e LatAm com o mesmo cliente |
| Dependências | `requests` + `google-play-scraper` | Minimalismo proposital |

**Princípios de código:** API oficial ou RSS antes de scraping; falha graciosa por etapa; toda credencial opcional; custo perto de zero com teto por execução e por dossiê; LLM extrai fatos e escreve texto, nunca dá nota.

## 5. Estrutura do repositório

Ver o mapa de módulos em `docs/v2-arquitetura.md`. Resumo: `app/cli.py` orquestra; `app/db/` concentra todo o SQL; `app/sources/` e `app/signals/` trazem dados; `app/extract.py` e `app/score.py` transformam em nota; `app/dig/` escava; `app/deliver/` entrega; `config/` guarda pesos, perfil, fontes e catalisadores.

**Schema (v2):** `companies`, `events`, `observations`, `site_pages`, `extractions`, `scores`, `dossiers`, `app_competitors`, `pain_clusters`, `br_competitors`, `br_competitor_links`, `feedback`, `embeddings`, `trends`, `llm_cache`, `runs`. As tabelas da v1 ficaram como `legacy_*` (só leitura).

## 6. Variáveis de ambiente

| Variável | Obrigatória? | Função |
|---|---|---|
| `OPENAI_API_KEY` | Não | Extração, escavação, tendências |
| `SERPER_API_KEY` | Não | Busca real (Google BR, Reclame Aqui, G2, LatAm) |
| `PH_TOKEN` | Não | Product Hunt |
| `GITHUB_TOKEN` | Não | Dossiês como Issues e leitura de feedback (o Actions fornece) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `MAIL_TO` / `MAIL_FROM` | Não | E-mail |

## 7. Estado atual

- **v1.0-v1.2 entregues** (julho/2026) e usadas de 12/07 a 22/08/2026, quando o workflow foi desligado manualmente. O `DIARIO.md` do congelamento ficou em branco.
- **Dados da v1:** 519 produtos (241 Show HN, 277 YC, 1 Product Hunt), score colapsado em 6-7, 71 dossiês, nenhum kit gerado (o kit exigia nota ≥ 8, que nunca saiu), custo total de US$0,46.
- **v2 (outubro/2026)** — refatoração completa decidida pelo dono para atacar esses problemas: empresa como unidade, nota em código, descarte, sinais de tração, revisitas, escavação ampliada, Issues, feedback, tendências e operação com branch `data`.

## 8. Direção do produto

Ver `docs/roadmap.md`. A v2 antecipou partes das fases 2 a 4 (fontes de tração, TAM, feedback, tendências). Continuam no roadmap: Reddit, GitHub Trending, RSS de newsletters, detector de aquisição/morte, Google Trends, análise de pricing comparada, cofre de ideias, lista de caça e dashboard.

## 9. Limitações conhecidas (honestidade > marketing)

- Score, TAM e veredito são heurísticas. O score só fica bom com o golden set rotulado pelo dono.
- TrustMRR não tem API oficial: o parser é leitura leve de páginas públicas e pode quebrar.
- Vagas por "slug" do domínio podem pegar a empresa errada; Form D por nome também (sinais fracos, pesam pouco).
- As fontes foram desenvolvidas sem acesso à rede (formatos documentados + testes simulados): a primeira execução real é a validação de verdade — o rodapé de saúde do e-mail mostra fonte zerada.
- Reviews de loja cobrem bem B2C e B2B de pequenos negócios; SaaS só web depende do Reclame Aqui e do G2.
- O yc-oss é um espelho comunitário; se sair do ar, as fontes do YC param (com alerta).

## 10. Filosofia do projeto (para não se perder)

1. **A ferramenta serve à validação, não o contrário.** Sucesso se mede em conversas de validação geradas, não em features entregues.
2. **Fase nova só começa com a anterior em uso real.**
3. **Dados de uso mandam mais que planejamento.** O roadmap pós-MVP é hipótese.
4. **Custo perto de zero é requisito**, não otimização.
5. A tentação permanente deste projeto é virar "produto procrastinador" — construir a ferramenta perfeita de achar oportunidades para adiar o desconforto de validar uma. O congelamento de 30 dias existe por isso.