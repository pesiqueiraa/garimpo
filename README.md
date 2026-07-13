# ⛏️ Garimpo

Radar pessoal de oportunidades de SaaS para tropicalizar. Todo dia ele coleta lançamentos do **Show HN**, **Product Hunt** e **Y Combinator**, deduplica, classifica com IA (opcional) e manda um e-mail com as melhores descobertas.

## Como funciona

```
coleta (APIs) → SQLite (dedup) → enriquecimento (LLM, opcional) → escavação (dossiê, opcional) → relatório HTML → e-mail
```

- Produtos que aparecem em **mais de uma fonte** ganham destaque (sinal forte de tração).
- O enriquecimento classifica nicho, dor, público e dá um palpite sobre concorrência no Brasil, com score 0-10.
- A escavação pega os produtos com score ≥ 7 (máx. 2 por dia) e monta um dossiê: concorrentes confirmados na Play Store, mapa de dores extraído de reviews 1-3★, um veredito de concorrência BR com evidências de busca real (Serper) e um ângulo de ataque para tropicalização. Se o score final ficar ≥ 8, o dossiê ganha também um kit de validação (copy de landing, perguntas Mom Test, mensagem de abordagem no WhatsApp).

### Investigar um produto manualmente

Para escavar um produto específico na hora, sem esperar a fila do dia:

```bash
python3 -m app.main --investigar "nome ou url"
```

Gera um `data/dossie-<produto>-AAAA-MM-DD.html` avulso.

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
| `OPENAI_API_KEY` | Classificação + score com IA | platform.openai.com/api-keys |
| `SERPER_API_KEY` | Busca real (Google BR) para o veredito de concorrência no dossiê | serper.dev — sem ela, o veredito do dossiê continua palpite do LLM |
| `SMTP_HOST` / `SMTP_PORT` | Servidor de e-mail | Resend: `smtp.resend.com` / `587` · Gmail: `smtp.gmail.com` / `587` |
| `SMTP_USER` / `SMTP_PASS` | Login do e-mail | Resend: usuário `resend` + API key (resend.com/api-keys) · Gmail: seu e-mail + **senha de app** (exige 2FA) |
| `MAIL_FROM` | Remetente | Resend sem domínio próprio: `onboarding@resend.dev` · Gmail: omitir (usa SMTP_USER) |
| `MAIL_TO` | Destinatário | seu e-mail — no Resend sem domínio, precisa ser o e-mail da conta Resend |

Localmente, exporte no terminal (`export PH_TOKEN=...`) ou use um `.env` com a lib de sua preferência.

## Automatizando de graça (GitHub Actions)

1. Crie um repositório **privado** no GitHub e suba este projeto.
2. Em *Settings → Secrets and variables → Actions*, cadastre os secrets da tabela acima.
3. Pronto: o workflow em `.github/workflows/daily.yml` roda todo dia às 7h (Brasília), envia o e-mail e commita o banco atualizado de volta no repositório.
4. Para testar na hora: aba *Actions → Garimpo diario → Run workflow*.

## Próximos passos (roadmap)

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
│   ├── llm.py               # cliente OpenAI (gpt-5-mini)
│   ├── excavate.py          # escavação de concorrentes + dossiê (opcional)
│   ├── search.py            # busca real (Serper) para o veredito BR
│   ├── kit.py               # kit de validação para dossiês fortes (score ≥ 8)
│   └── report.py            # HTML do e-mail + envio SMTP
├── tests/                   # suíte pytest
├── docs/                    # roadmap e contexto do projeto
├── data/                    # banco e relatórios gerados
└── .github/workflows/daily.yml
```

## Desenvolvimento

Para rodar os testes localmente:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
```
