# ⛏️ Garimpo v2

Radar pessoal de oportunidades de SaaS para tropicalizar. Todo dia ele descobre
empresas lá fora, mede sinais de tração, extrai fatos com IA, calcula um score
explicável, escava as melhores (concorrência no Brasil, dores dos clientes,
mercado) e manda um e-mail curto. Aos domingos, um e-mail semanal mostra temas
em alta, quem ganhou tração e o kit de validação da semana.

A arquitetura completa está em [docs/v2-arquitetura.md](docs/v2-arquitetura.md).

## Como funciona

```
coleta → resolução por empresa → leitura do site → fatos (LLM) → score em código
→ sinais de tração → fila → escavação → revisitas → Issues → e-mail
```

- **A unidade é a empresa.** Lançamentos (Show HN, Launch HN, Product Hunt, YC)
  e listagens com prova de tração (YC antigas que cresceram, TrustMRR, gap do
  top faturamento da App Store EUA × Brasil) viram eventos de uma empresa.
- **O LLM extrai fatos; a nota é calculada em código** a partir de cinco
  subnotas: tração, tropicalizabilidade, timing (catalisadores brasileiros),
  fit com o seu perfil e espaço no Brasil. Pesos em `config/pesos.toml`.
- **Descarte automático** do que não é SaaS tropicalizável (modelos de IA,
  demos técnicas, hardware...).
- **Escavação**: Play Store e App Store (avaliações 1-3★), Google BR, Reclame
  Aqui, G2, América Latina, mapa permanente de concorrentes brasileiros,
  tamanho de mercado por CNAE (opcional) e ângulo de ataque.
- **Kit de validação** (landing, perguntas Mom Test, abordagem de WhatsApp,
  onde achar os primeiros entrevistados) para dossiês com nota ≥ 8 **e** para
  o melhor dossiê da semana.
- **Revisitas** em D+30, D+90 e D+180: quem ganhou tração é promovido.
- **Feedback**: 👍 👎 👀 no e-mail e labels `status:*` nas Issues de dossiê
  mudam o status e ajustam o score ao seu gosto.

## Comandos

```bash
pip install -r requirements.txt
python -m app rodar --sem-email        # pipeline diário, só gera o HTML em data/
python -m app rodar --semanal --sem-email   # inclui as etapas de domingo
python -m app investigar "nome ou url" # escava uma empresa na hora (HTML em data/)
python -m app status                   # resumo do banco e das últimas execuções
python -m app extrair --legado         # reprocessa uma vez as empresas da v1 (teto US$1,50)
python -m app feedback 123 like        # registra um sinal seu
python -m app golden --amostra 50      # cria/acrescenta o golden set em data/golden.csv
python -m app avaliar                  # compara o score com os seus rótulos
python -m app baixar-banco             # traz o banco de produção do branch data
python -m app --help                   # todos os comandos
```

Para testar com uma cópia do banco sem tocar em `data/garimpo.db`:
`GARIMPO_DB=/caminho/copia.db python -m app rodar --sem-email`. Localmente, não
defina `GITHUB_REPOSITORY` com um token de escrita no ambiente, ou os dossiês do
teste viram Issues de verdade.

`python -m app.main` e `python -m app.main --investigar X` continuam
funcionando por compatibilidade com a v1.

## Chaves e variáveis de ambiente (todas opcionais)

| Variável | Para quê | Onde conseguir |
|---|---|---|
| `OPENAI_API_KEY` | Extração de fatos, escavação, tendências | platform.openai.com/api-keys |
| `SERPER_API_KEY` | Busca real no Google BR, Reclame Aqui, G2 e América Latina | serper.dev — sem ela, o veredito fica "desconhecido" |
| `PH_TOKEN` | Coletar do Product Hunt | producthunt.com/v2/oauth/applications → *Developer Token* |
| `GITHUB_TOKEN` | Dossiês como Issues e feedback | o Actions já fornece; localmente, um token com acesso a Issues |
| `SEC_CONTACT` | E-mail de contato no User-Agent da SEC (Form D) | opcional: sem ele usa `MAIL_TO`; sem nenhum, o Form D é pulado |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `MAIL_TO` / `MAIL_FROM` | E-mail | Resend (`smtp.resend.com:587`) ou Gmail com senha de app |

Sem nenhuma chave, Show HN, YC, sites e sinais públicos ainda funcionam e o
e-mail sai sem score.

## Automação (GitHub Actions)

- O workflow `.github/workflows/daily.yml` roda às 05:37 de Brasília (fora da
  hora cheia, com folga para atrasos do GitHub) e o e-mail chega antes das 7h.
- O banco vive no branch `data`: o workflow baixa `garimpo.db` de lá, roda e
  commita de volta. Na primeira execução da v2, sem o branch, ele usa o
  `data/garimpo.db` do `main`, aplica a migração e cria o branch.
- *Actions → Garimpo diario → Run workflow* deixa escolher `rodar`,
  `rodar --semanal`, `extrair --legado` ou `status`.
- O job precisa de `issues: write` (já declarado) para criar os dossiês como Issues.

### Primeira execução depois do merge

1. Reative o workflow (*Actions → Garimpo diario → Enable workflow*).
2. Rode manualmente `extrair --legado`: lê os sites e extrai fatos das 516
   empresas da v1 em blocos, com teto de US$1,50 (configurável em
   `config/fontes.toml`) e prazo de 35 minutos. Se o log disser "tempo
   esgotado", rode de novo — ele continua de onde parou.
3. Rode `rodar` e confira o e-mail. O rodapé mostra a saúde de cada fonte.
4. Rode `python -m app golden` localmente, rotule as empresas em
   `data/golden.csv` e use `python -m app avaliar` para calibrar `config/pesos.toml`.

## Tamanho de mercado por CNAE (opcional)

Baixe os arquivos `Estabelecimentos*.zip` dos dados abertos do CNPJ (Receita
Federal) e rode `python scripts/build_cnae_counts.py Estabelecimentos*.zip`.
Isso gera `data/cnae_counts.csv`, que a escavação usa para estimar quantos
negócios-alvo existem no Brasil. Sem o arquivo, o dossiê sai sem essa linha.

## Desenvolvimento

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
```

Os testes usam banco SQLite real em arquivo temporário e bloqueiam a rede:
toda chamada externa precisa de mock.
