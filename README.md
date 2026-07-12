# ⛏️ Garimpo

Radar pessoal de oportunidades de SaaS para tropicalizar. Todo dia ele coleta lançamentos do **Show HN**, **Product Hunt** e **Y Combinator**, deduplica, classifica com IA (opcional) e manda um e-mail com as melhores descobertas.

## Como funciona

```
coleta (APIs) → SQLite (dedup) → enriquecimento (LLM, opcional) → relatório HTML → e-mail
```

- Produtos que aparecem em **mais de uma fonte** ganham destaque (sinal forte de tração).
- O enriquecimento classifica nicho, dor, público e dá um palpite sobre concorrência no Brasil, com score 0-10.

## Rodando localmente

```bash
pip install -r requirements.txt
python -m app.main --no-mail    # gera data/relatorio-AAAA-MM-DD.html sem enviar e-mail
```

Abra o HTML gerado no navegador. Só o Show HN e o YC funcionam sem nenhuma chave.

## Chaves e variáveis de ambiente (todas opcionais)

| Variável | Para quê | Onde conseguir |
|---|---|---|
| `PH_TOKEN` | Coletar do Product Hunt | producthunt.com/v2/oauth/applications → crie um app e use o *Developer Token* |
| `ANTHROPIC_API_KEY` | Classificação + score com IA | console.anthropic.com |
| `SMTP_HOST` / `SMTP_PORT` | Servidor de e-mail | Gmail: `smtp.gmail.com` / `587` |
| `SMTP_USER` / `SMTP_PASS` | Login do e-mail | Gmail: seu e-mail + **senha de app** (myaccount.google.com/apppasswords, exige 2FA ativado) |
| `MAIL_TO` | Destinatário | seu e-mail (se omitido, usa o SMTP_USER) |

Localmente, exporte no terminal (`export PH_TOKEN=...`) ou use um `.env` com a lib de sua preferência.

## Automatizando de graça (GitHub Actions)

1. Crie um repositório **privado** no GitHub e suba este projeto.
2. Em *Settings → Secrets and variables → Actions*, cadastre os secrets da tabela acima.
3. Pronto: o workflow em `.github/workflows/daily.yml` roda todo dia às 7h (Brasília), envia o e-mail e commita o banco atualizado de volta no repositório.
4. Para testar na hora: aba *Actions → Garimpo diario → Run workflow*.

## Próximos passos (roadmap)

- [ ] Veredito de concorrência BR de verdade: plugar busca (Serper.dev/Tavily) no `enrich.py` em vez do palpite do modelo
- [ ] Novas fontes: Reddit (PRAW), RSS de newsletters (TLDR), Google Trends (pytrends)
- [ ] Dashboard web lendo o mesmo `garimpo.db` (aqui entra a parte criativa 😉)
- [ ] Feedback loop: marcar oportunidades como "interessante/descartar" para calibrar o score

## Estrutura

```
garimpo/
├── app/
│   ├── main.py              # orquestra o pipeline
│   ├── collectors.py        # Show HN, Product Hunt, YC
│   ├── db.py                # SQLite + deduplicação
│   ├── enrich.py            # classificação com LLM (opcional)
│   └── report.py            # HTML do e-mail + envio SMTP
├── docs/                    # roadmap e contexto do projeto
├── data/                    # banco e relatórios gerados
└── .github/workflows/daily.yml
```
