# 🗺️ Roadmap — Garimpo

**Visão do produto:** transformar sinais dispersos (lançamentos lá fora, reviews, buscas) em dossiês acionáveis de oportunidades de SaaS para o mercado brasileiro — e empurrar o dono da ferramenta para a rua, não para mais uma feature.

**Princípio norteador:** cada fase só começa quando a anterior estiver *em uso real*, não apenas pronta. Ferramenta parada não vale roadmap.

---

## ✅ Fase 0 — Fundação (v1.0) — ENTREGUE

O motor que já existe e roda hoje.

| Item | Status |
|---|---|
| Coleta: Show HN, Product Hunt, Y Combinator | ✅ |
| Banco SQLite com deduplicação e detecção cross-source | ✅ |
| Enriquecimento LLM: nicho, dor, público, veredito BR (palpite), score 0-10 | ✅ |
| Relatório HTML + envio por e-mail (SMTP) | ✅ |
| Automação diária gratuita via GitHub Actions | ✅ |

**Pendência operacional (não é código):** configurar os secrets no GitHub e receber o primeiro e-mail real. *Nada da Fase 1 deve começar antes disso.*

---

## 🎯 Fase 1 — MVP completo (v1.1 + v1.2)

Objetivo: fechar o ciclo **sinal → dossiê → ação**. Ao final desta fase, o Garimpo não diz apenas "isso parece bom" — ele entrega o mapa de dores, a prova de concorrência e o kit para validar na rua.

### v1.1 — Minerador de reviews + Dossiê (~1-2 fins de semana)

**Feature A: Escavação de concorrentes**
- Gatilho: produto com score ≥ 7 OU marcado manualmente para investigar
- LLM sugere 3-5 apps concorrentes/similares (globais e brasileiros)
- Busca na Play Store confirma quais existem (`google-play-scraper`, sem chave, sem custo)
- Coleta das avaliações de 1-3 estrelas de cada concorrente (limite ~200 reviews/app, com pausas entre requisições)
- Novas tabelas: `competitors` (app, nota média, volume de reviews) e `pain_clusters` (tema, frequência, exemplos)

**Feature B: Clusterização de dores**
- LLM agrupa reclamações em temas ("cobrança confusa", "sem integração WhatsApp"...)
- Cada tema sai com: frequência, 2-3 trechos de exemplo, gravidade estimada

**Feature C: Dossiê no e-mail**
- Nova seção "🔬 Dossiês prontos" no e-mail diário
- Estrutura do dossiê: o que é → tração lá fora → players encontrados → mapa de dores ranqueado → "ângulo de ataque" (dores negligenciadas + adaptações locais: Pix, WhatsApp, NF)
- Fila de escavação: máx. 1-2 dossiês/dia, priorizados por score (controla custo e tempo)

**Critérios de pronto:**
- [ ] Um dossiê completo gerado de ponta a ponta sem intervenção manual
- [ ] Dossiê de um nicho com player BR mostra as dores do player BR (cenário "mercado validado e mal atendido")
- [ ] Custo por dossiê medido e < R$1 em tokens

### v1.2 — Confiabilidade + Ação (~1 fim de semana)

**Feature D: Concorrência BR real (upgrade nº 1 de confiabilidade)**
- Integração com API de busca (Serper.dev ou Tavily — centavos por consulta)
- O veredito `br_verdict` passa de palpite do modelo para análise de resultados reais de busca, com evidências (links) no dossiê
- Score recalibrado com essa informação

**Feature E: Kit de validação**
- Para cada dossiê com score final ≥ 8, o LLM gera automaticamente:
  - Copy de landing page (headline, subheadline, 3 benefícios, CTA de lista de espera)
  - 5 perguntas de entrevista estilo *Mom Test*, em português, específicas do nicho
  - Mensagem de abordagem para grupos de WhatsApp/comunidades do nicho
- Anexado ao dossiê no e-mail

**Feature F: Comando manual "investigar X"**
- `python main.py --investigar "nome ou url"` — escava qualquer produto, mesmo fora do radar

**Critérios de pronto:**
- [ ] Veredito BR acompanhado de links de evidência em 100% dos dossiês
- [ ] Kit de validação gerado junto do dossiê sem passo extra

### 🧊 Congelamento de 30 dias (obrigatório)

Ao fim da v1.2: **zero desenvolvimento por 30 dias.** Só uso.

**Metas do período (é isso que mede sucesso, não features):**
- [ ] Ler o e-mail todo dia (se parar de ler, o problema é do produto — anotar por quê)
- [ ] Escolher 1 dossiê e executar o kit: landing no ar + 5 entrevistas reais
- [ ] Registrar num `DIARIO.md`: o que faltou, o que sobrou, o que enganou

*As anotações do diário reordenam as fases seguintes. O roadmap abaixo é hipótese, não contrato.*

---

## 📡 Fase 2 — Mais sinal (v2.0)

Objetivo: capturar oportunidades mais cedo, antes de virarem produto.

| Feature | Descrição | Esforço | Dependência |
|---|---|---|---|
| Reddit | r/SaaS, r/smallbusiness, r/Entrepreneur via PRAW; foco em posts "I wish there was..." — dor crua, pré-produto | Médio | Conta de API Reddit (grátis) |
| GitHub Trending | Repositórios explodindo hoje viram SaaS em 6 meses | Baixo | Nenhuma |
| RSS de newsletters | TLDR, The Hustle e afins via feed | Baixo | Nenhuma |
| Detector aquisição/morte | Monitorar no HN notícias de produto adquirido/descontinuado → usuários órfãos procurando alternativa | Médio | Nenhuma |

**Critério de pronto da fase:** cada fonte nova precisa gerar ≥ 1 item aproveitado no e-mail por semana; fonte que só gera ruído por 3 semanas é desligada.

---

## 🧠 Fase 3 — Inteligência de mercado (v3.0)

Objetivo: dossiês com dimensão de mercado, não só de produto.

| Feature | Descrição | Esforço |
|---|---|---|
| Google Trends (pytrends) | Curva de interesse EUA vs BR, 24 meses; "cronômetro de oportunidade" entra no score | Baixo |
| TAM de guerrilha | Estimativa de mercado: nº de negócios-alvo no Brasil (dados públicos de CNPJ por CNAE) × ticket plausível | Médio |
| Análise de pricing | Scraping leve da página de preços dos produtos originais: quanto cobram e por qual métrica | Médio |

**Critério de pronto:** dossiê responde "quanto vale esse mercado no Brasil e quanto cobrar" com premissas explícitas (transparência > precisão).

---

## 🔁 Fase 4 — Aprendizado e memória (v4.0)

Objetivo: o Garimpo deixa de ser genérico e vira *seu* copiloto. Se um dia virar produto vendável, esta fase é o diferencial.

| Feature | Descrição | Esforço |
|---|---|---|
| Feedback loop | Links 👍/👎 no e-mail; escolhas viram exemplos no prompt de score, calibrando o gosto do dono | Médio |
| Cofre de ideias | Descartes arquivados com motivo; se a categoria reaparecer com sinal mais forte, alerta "isso voltou, e mais quente" | Médio |
| Lista de caça | Para o nicho em validação, lista de clientes reais para entrevistar (dados públicos, ex: Google Maps) | Médio |

---

## 🎨 Fase 5 — Dashboard (v5.0)

Deixado por último de propósito: com meses de dados reais e hábitos formados, você saberá exatamente o que quer ver — e aqui entra a camada criativa que você quer explorar (o mapa de dores clusterizado é um prato cheio para visualização fora do padrão).

- Lê o mesmo `garimpo.db` (nenhuma mudança no motor)
- Candidatos: linha do tempo de sinais, mapa de nichos × concorrência, explorador de dossiês
- Decisão de conceito visual: adiada até aqui, de propósito

---

## 💰 Fase 6 (opcional) — Produtização

Somente se, após meses de uso, o Garimpo se provar valioso para você:

1. Multiusuário (nichos e preferências por pessoa)
2. Modelo: assinatura mensal (referências gringas: GummySearch, Exploding Topics — sem versão BR focada em tropicalização até onde se sabe)
3. Ironia estratégica: validar com o próprio kit de validação que ele gera

---

## 📋 Resumo executivo

| Fase | Entrega central | Quando |
|---|---|---|
| 0 ✅ | Radar diário por e-mail | Feito — falta ativar |
| 1 🎯 | Dossiê com dores reais + kit de validação | Próximos 2-4 fins de semana |
| 🧊 | 30 dias de uso, 1 validação na rua | Obrigatório antes de seguir |
| 2 | Mais fontes de sinal | Conforme diário de uso |
| 3 | Dimensão de mercado nos dossiês | Conforme diário de uso |
| 4 | Feedback loop e memória | Conforme diário de uso |
| 5 | Dashboard criativo | Com dados acumulados |
| 6 | Virar produto (talvez) | Só com prova de valor |

**Regra de ouro do projeto:** o sucesso do Garimpo não se mede em features entregues, e sim em quantas conversas de validação ele te fez ter. Se em 60 dias ele não te levou para a rua nenhuma vez, o roadmap certo é parar de codar e ir entrevistar gente.