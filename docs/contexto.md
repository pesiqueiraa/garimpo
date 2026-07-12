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
coleta (APIs) → SQLite (dedup) → enriquecimento (LLM) → relatório HTML → e-mail diário
```

1. **Coleta** — três fontes na v1.0:
   - Show HN via API pública da Algolia (sem chave)
   - Product Hunt via API GraphQL oficial (requer `PH_TOKEN`)
   - Y Combinator via espelho público yc-oss (sem chave)
2. **Dedup** — chave por domínio da URL (ou nome normalizado). Produto visto em **múltiplas fontes** é marcado como sinal forte (`cross_source`).
3. **Enriquecimento** (opcional, requer `ANTHROPIC_API_KEY`) — LLM classifica: nicho, dor que resolve, público (B2B/B2C), veredito de concorrência BR e score de oportunidade 0-10.
4. **Relatório** — HTML com cards (nome, tagline, fonte, pontos, score, veredito BR) salvo em `data/` e enviado por SMTP.
5. **Automação** — GitHub Actions roda tudo diariamente às 7h (Brasília) de graça e commita o banco de volta no repo.

## 4. Stack e decisões técnicas

| Decisão | Escolha | Racional |
|---|---|---|
| Linguagem | Python 3.12 | Ecossistema de scraping/dados; familiaridade |
| Banco | SQLite (arquivo no repo) | Zero infra; volume pequeno; versionado junto do código |
| LLM | API Anthropic, modelo Haiku | Classificação simples não precisa de modelo caro |
| Envio | SMTP puro (Gmail + senha de app) | Sem dependência de serviço de e-mail pago |
| Automação | GitHub Actions (cron) | Gratuito, sem servidor |
| Dependências | Só `requests` na v1.0 | Minimalismo proposital; cada fase adiciona o próprio mínimo |

**Princípios de código adotados:**
- API oficial ou RSS sempre antes de scraping; scraping só leve, com pausas, 1x/dia.
- Falha graciosa: fonte que quebra loga e é pulada, o pipeline nunca morre por causa de uma fonte.
- Toda chave/credencial é opcional: sem `PH_TOKEN` pula Product Hunt, sem `ANTHROPIC_API_KEY` pula enriquecimento, sem SMTP salva o HTML só em disco.
- Custo-alvo: centavos por dia.

## 5. Estrutura do repositório

```
garimpo/
├── main.py                    # orquestra o pipeline (--no-mail para testar)
├── requirements.txt
├── README.md                  # setup e instruções operacionais
├── ROADMAP.md                 # fases do produto e critérios de pronto
├── CONTEXT.md                 # este arquivo
├── garimpo/
│   ├── collectors.py          # Show HN, Product Hunt, YC
│   ├── db.py                  # SQLite, schema, dedup, consultas
│   ├── enrich.py              # classificação LLM (opcional)
│   └── report.py              # HTML do e-mail + envio SMTP
├── data/                      # garimpo.db + relatórios gerados
└── .github/workflows/daily.yml
```

**Schema atual (tabela `products`):** `dedup_key`, `name`, `tagline`, `url`, `source`, `source_url`, `points`, `category`, `first_seen`, `last_seen`, `seen_count`, `sources_seen`, e campos de enriquecimento: `niche`, `pain`, `audience`, `br_verdict`, `opportunity_score`.

## 6. Variáveis de ambiente

| Variável | Obrigatória? | Função |
|---|---|---|
| `PH_TOKEN` | Não | Habilita coleta do Product Hunt |
| `ANTHROPIC_API_KEY` | Não | Habilita enriquecimento LLM |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `MAIL_TO` | Não | Habilitam envio do e-mail |

No GitHub Actions, todas entram como *secrets*.

## 7. Estado atual

- **v1.0 entregue e testada** (pipeline completo com dados simulados; dedup e cross-source validados).
- **Pendência operacional:** subir para um repo privado no GitHub, configurar secrets e receber o primeiro e-mail real. É o pré-requisito para qualquer desenvolvimento novo.
- Próxima entrega planejada: **v1.1 — minerador de reviews + dossiê** (ver ROADMAP.md, Fase 1).

## 8. Direção do produto (resumo do ROADMAP.md)

- **Fase 1 (MVP completo):** escavação de concorrentes via reviews 1-3★ da Play Store (`google-play-scraper`), clusterização de dores por LLM, dossiê por oportunidade com "ângulo de ataque", veredito de concorrência BR com busca real (Serper/Tavily) e kit de validação automático (copy de landing, perguntas Mom Test, mensagem de abordagem).
- **Congelamento de 30 dias** após o MVP: só uso real e 1 validação na rua. As anotações do período reordenam o resto.
- **Fases 2-5:** mais fontes de sinal (Reddit, GitHub Trending, RSS, detector de aquisição/morte) → inteligência de mercado (Google Trends, TAM de guerrilha, análise de pricing) → aprendizado (feedback 👍/👎, cofre de ideias, lista de caça) → dashboard criativo por último, de propósito.
- **Fase 6 (opcional):** produtização como SaaS de assinatura.

## 9. Limitações conhecidas (honestidade > marketing)

- O veredito de concorrência BR na v1.0 é **palpite do LLM**, sem busca na web — serve de triagem, não de decisão. Upgrade previsto na v1.2.
- Reviews da Play Store (v1.1) cobrem bem B2C e B2B de pequenos negócios; SaaS enterprise/web-only pode não ter app — nesses casos o dossiê sai sem mapa de dores.
- Score é heurístico e será recalibrado com feedback de uso (Fase 4).
- O yc-oss é um espelho comunitário; se sair do ar, a fonte YC precisa de alternativa.

## 10. Filosofia do projeto (para não se perder)

1. **A ferramenta serve à validação, não o contrário.** Sucesso se mede em conversas de validação geradas, não em features entregues.
2. **Fase nova só começa com a anterior em uso real.**
3. **Dados de uso mandam mais que planejamento.** O roadmap pós-MVP é hipótese.
4. **Custo perto de zero é requisito**, não otimização.
5. A tentação permanente deste projeto é virar "produto procrastinador" — construir a ferramenta perfeita de achar oportunidades para adiar o desconforto de validar uma. O congelamento de 30 dias existe por isso.