"""Linha de comando do Garimpo: `python -m app <comando>`.

`rodar` é o pipeline diário (e semanal aos domingos). Os outros comandos rodam
uma etapa isolada — útil para testar, reprocessar ou investigar à mão.
Cada etapa do `rodar` é isolada num try/except: falhou, entra no registro da
execução e no rodapé do e-mail, e o resto segue. O e-mail sempre sai.
"""
import argparse
import copy
import json
import re
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone

from app import config, db, extract, llm, queue, resolve, revisit, score
from app.config import get

BRT = timezone(timedelta(hours=-3))


def _jsonable(value):
    """Resumo da saída de uma etapa para a tabela runs (nada de HTML ou listas enormes)."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return f"<{type(value).__name__}>"
    return value if len(text) <= 2000 else f"<{type(value).__name__} grande>"


class Pipeline:
    """Acumula estatísticas e erros das etapas de uma execução."""

    def __init__(self, conn, command: str):
        self.conn = conn
        self.run_id = db.start_run(conn, command)
        self.stats: dict = {}
        self.errors: list[str] = []
        self.source_counts: dict = {}

    def stage(self, name: str, fn, *args, **kwargs):
        """Roda uma etapa. Sucesso ou teto de orçamento: commita o que ela gravou.
        Exceção: desfaz o que ficou pela metade. Ctrl+C/timeout: desfaz e repassa."""
        try:
            result = fn(*args, **kwargs)
            self.stats[name] = _jsonable(result)
            self._commit()
            return result
        except llm.BudgetExceeded as e:
            self.errors.append(f"{name}: {e}")
            print(f"[{name}] {e}")
            self._commit()
        except Exception as e:
            self.errors.append(f"{name}: {type(e).__name__}: {e}")
            print(f"[{name}] etapa falhou: {e}")
            traceback.print_exc()
            self._rollback()
        except BaseException:
            self._rollback()
            raise
        return None

    def _commit(self) -> None:
        try:
            self.conn.commit()
        except Exception as e:
            print(f"[pipeline] commit falhou: {e}")

    def _rollback(self) -> None:
        try:
            self.conn.rollback()
        except Exception as e:
            print(f"[pipeline] rollback falhou: {e}")

    def finish(self) -> None:
        db.finish_run(self.conn, self.run_id, self.stats, self.source_counts,
                      llm.usage(), self.errors)


def _one_second_after(iso: str) -> str:
    """Carimbos têm resolução de segundo: o que foi gravado no mesmo segundo em que a
    execução anterior terminou pertence a ela, não a esta."""
    try:
        return (datetime.fromisoformat(iso) + timedelta(seconds=1)).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return db.days_ago_iso(26 / 24)


def is_weekly_day(cfg: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(BRT)
    return now.astimezone(BRT).weekday() == int(get(cfg, "fontes.entrega.weekly_day", 6))


def _save_html(name: str, html: str) -> str:
    out = db.DB_PATH.parent / f"{name}-{date.today().isoformat()}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[entrega] salvo em {out}")
    return str(out)


def _collect_and_ingest(p: Pipeline, cfg: dict) -> None:
    from app import sources
    collected = p.stage("coleta", sources.collect_all, cfg)
    if not collected:
        return
    items, counts = collected
    p.source_counts = counts
    p.stats["coleta"] = counts
    p.stage("resolucao", resolve.ingest, p.conn, items,
            int(get(cfg, "fontes.coleta.max_new_per_run", 150)))


def cmd_rodar(args, cfg: dict) -> int:
    from app import deliver, dig, feedback, health, signals, trends
    conn = db.connect()
    llm.configure(conn, budget_usd=float(get(cfg, "fontes.orcamento.max_usd_per_run", 0.30)))
    previous = db.recent_runs(conn, 1, "rodar")
    # o e-mail mostra só o que apareceu desde a última execução terminada (nada repetido)
    since = (_one_second_after(previous[0]["finished_at"]) if previous
             else db.days_ago_iso(26 / 24))
    p = Pipeline(conn, "rodar")
    weekly = args.semanal or is_weekly_day(cfg)

    _collect_and_ingest(p, cfg)
    p.stage("sondagem", signals.probe_new, conn, cfg)
    p.stage("extracao", extract.run, conn, cfg)
    p.stage("embeddings", trends.embed_missing, conn, cfg)
    p.stage("score", score.run, conn, cfg)
    p.stage("sinais", signals.run, conn, cfg)
    p.stage("score_sinais", score.run, conn, cfg)
    p.stage("feedback", feedback.read, conn, cfg)
    p.stage("fila", queue.refresh, conn, cfg)
    p.stage("escavacao", dig.run, conn, cfg)
    p.stage("revisitas", revisit.run, conn, cfg)
    kit_dossier = None
    if weekly:
        p.stage("tendencias", trends.run, conn, cfg)
        kit_dossier = p.stage("kit_semana", dig.weekly_kit, conn, cfg)
    p.stage("issues", deliver.issues.sync, conn, cfg)
    if kit_dossier:
        p.stage("kit_comentario", deliver.issues.post_kit_comment, conn, cfg, kit_dossier)

    alerts = health.alerts(db.recent_runs(conn, 7, "rodar"), p.source_counts, p.errors)
    run_info = {"stats": p.stats, "source_counts": p.source_counts, "alerts": alerts,
                "usage": llm.usage(), "since": since}
    built = p.stage("email_diario", deliver.daily.build, conn, cfg, run_info)
    if built:
        subject, html = built
        p.stats["email_diario"] = subject
        _save_html("relatorio", html)
        if not args.sem_email:
            deliver.mail.send(html, subject)
    if weekly:
        built = p.stage("email_semanal", deliver.weekly.build, conn, cfg)
        if built:
            subject, html = built
            p.stats["email_semanal"] = subject
            _save_html("semanal", html)
            if not args.sem_email:
                deliver.mail.send(html, subject)

    p.finish()
    _maintenance(conn, cfg)
    conn.close()
    print(f"[rodar] fim · custo US${llm.usage()['usd']:.4f} · {len(p.errors)} erro(s)")
    return 0


def _maintenance(conn, cfg: dict) -> None:
    try:
        pruned = db.prune_payloads(conn, db.days_ago_iso(int(get(cfg, "fontes.manutencao.prune_payload_days", 90))))
        db.prune_cache(conn, db.days_ago_iso(120))
        db.vacuum(conn)
        if pruned:
            print(f"[manutencao] {pruned} payloads antigos apagados")
    except Exception as e:
        print(f"[manutencao] falhou: {e}")


def cmd_coletar(args, cfg: dict) -> int:
    conn = db.connect()
    p = Pipeline(conn, "coletar")
    _collect_and_ingest(p, cfg)
    p.finish()
    print(json.dumps(p.stats, ensure_ascii=False, indent=2))
    return 0


def cmd_sondar(args, cfg: dict) -> int:
    from app import signals
    conn = db.connect()
    signals.probe_new(conn, cfg)
    return 0


def cmd_extrair(args, cfg: dict) -> int:
    conn = db.connect()
    if args.legado:
        budget = args.orcamento or float(get(cfg, "fontes.orcamento.max_usd_legacy_reprocess", 1.5))
    else:
        budget = args.orcamento or float(get(cfg, "fontes.orcamento.max_usd_per_run", 0.30))
    llm.configure(conn, budget_usd=budget)
    p = Pipeline(conn, "extrair")
    if args.legado:
        minutes = args.minutos or 35
        deadline = time.monotonic() + minutes * 60
        p.stage("legado", _legacy_reprocess, conn, cfg, deadline, args.limite)
    else:
        p.stage("extracao", extract.run, conn, cfg, limit=args.limite)
    p.stage("score", score.run, conn, cfg)
    p.finish()
    print(f"[extrair] custo US${llm.usage()['usd']:.4f} (teto US${budget:.2f})")
    return 0


def _legacy_reprocess(conn, cfg: dict, deadline: float, limit: int | None) -> int:
    """Reprocessa as empresas da v1 em blocos: lê os sites e extrai os fatos de cada bloco.

    Para no prazo (o job do Actions tem limite de tempo); rodar de novo continua de onde parou.
    """
    from app import signals
    total = 0
    while time.monotonic() < deadline:
        probed = signals.probe_new(conn, cfg) or 0
        extracted = extract.run(conn, cfg, limit=60, legacy_only=True, workers=4, deadline=deadline)
        total += extracted
        if (not probed and not extracted) or (limit and total >= limit):
            break
    else:
        print("[extrair] tempo esgotado — rode de novo para continuar")
    return total


def cmd_pontuar(args, cfg: dict) -> int:
    conn = db.connect()
    score.run(conn, cfg, everything=args.todos)
    return 0


def cmd_sinais(args, cfg: dict) -> int:
    from app import signals
    conn = db.connect()
    signals.run(conn, cfg)
    return 0


def cmd_escavar(args, cfg: dict) -> int:
    from app import dig
    conn = db.connect()
    llm.configure(conn, budget_usd=float(get(cfg, "fontes.orcamento.max_usd_per_run", 0.30)))
    if args.limite:
        cfg.setdefault("fontes", {}).setdefault("fila", {})["max_digs_per_run"] = args.limite
    queue.refresh(conn, cfg)
    dig.run(conn, cfg)
    return 0


def cmd_investigar(args, cfg: dict) -> int:
    from app import deliver, dig
    conn = db.connect()
    llm.configure(conn, budget_usd=args.orcamento or 0.50)
    dossier_id = dig.investigate(conn, args.termo, cfg)
    if not dossier_id:
        print("[investigar] não foi possível gerar o dossiê")
        return 1
    d = db.get_dossier(conn, dossier_id)
    slug = re.sub(r"[^a-z0-9]+", "-", d["company"].get("name", "").lower()).strip("-")[:40] or "empresa"
    _save_html(f"dossie-{slug}", deliver.components.dossier_page(d))
    return 0


def cmd_revisitar(args, cfg: dict) -> int:
    conn = db.connect()
    revisit.run(conn, cfg)
    return 0


def cmd_tendencias(args, cfg: dict) -> int:
    from app import trends
    conn = db.connect()
    llm.configure(conn, budget_usd=float(get(cfg, "fontes.orcamento.max_usd_per_run", 0.30)))
    for t in trends.run(conn, cfg) or []:
        print(f"  {t.get('label')}: {t.get('size')} empresas, crescimento {t.get('growth')}")
    return 0


def cmd_feedback(args, cfg: dict) -> int:
    from app import feedback
    conn = db.connect()
    if db.get_company(conn, args.empresa) is None:
        print(f"[feedback] empresa {args.empresa} não existe")
        return 1
    feedback.record(conn, args.empresa, args.sinal, "cli", note=args.nota)
    conn.commit()
    print(f"[feedback] {args.sinal} registrado para a empresa {args.empresa}")
    return 0


def cmd_ler_feedback(args, cfg: dict) -> int:
    from app import feedback
    conn = db.connect()
    print(feedback.read(conn, cfg))
    return 0


def cmd_enviar(args, cfg: dict) -> int:
    from app import deliver, health
    conn = db.connect()
    if args.tipo == "semanal":
        subject, html = deliver.weekly.build(conn, cfg)
        _save_html("semanal", html)
    else:
        runs = db.recent_runs(conn, 8, "rodar")
        last = runs[0] if runs else {"source_counts": {}, "errors": []}
        info = {"stats": last.get("stats", {}), "source_counts": last.get("source_counts", {}),
                "alerts": health.alerts(runs[1:], last.get("source_counts", {}), last.get("errors", [])),
                "usage": {"usd": last.get("cost_usd", 0)}}
        subject, html = deliver.daily.build(conn, cfg, info)
        _save_html("relatorio", html)
    if not args.sem_email:
        deliver.mail.send(html, subject)
    return 0


def cmd_avaliar(args, cfg: dict) -> int:
    from app import evaluate
    conn = db.connect()
    evaluate.evaluate(conn)
    return 0


def cmd_golden(args, cfg: dict) -> int:
    from app import evaluate
    conn = db.connect()
    evaluate.sample(conn, args.amostra)
    return 0


def cmd_status(args, cfg: dict) -> int:
    conn = db.connect()
    print(f"schema v{db.schema_version(conn)} · empresas por status: {db.count_companies(conn)}")
    print(f"fila: {[c['name'] for c in db.queued_companies(conn, 5)]}")
    print(f"concorrentes BR mapeados: {db.count_br_competitors(conn)} · sinais do dono: {db.count_feedback(conn)}")
    for r in db.recent_runs(conn, 3):
        print(f"execução {r['started_at']} ({r['command']}): fontes {r['source_counts']} · "
              f"US${r['cost_usd']:.4f} · {len(r['errors'])} erro(s)")
    return 0


def cmd_migrar(args, cfg: dict) -> int:
    conn = db.connect()
    print(f"[db] schema na versão {db.schema_version(conn)}")
    return 0


def cmd_baixar_banco(args, cfg: dict) -> int:
    """Traz o banco de produção do branch `data` para data/garimpo.db."""
    try:
        subprocess.run(["git", "fetch", "--depth=1", "origin", "data"], check=True)
        content = subprocess.run(["git", "show", "FETCH_HEAD:garimpo.db"], check=True,
                                 capture_output=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[banco] não deu para baixar do branch data: {e}")
        return 1
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db.DB_PATH.write_bytes(content)
    print(f"[banco] {len(content) / 1e6:.1f} MB salvos em {db.DB_PATH}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app", description="Garimpo v2")
    sub = parser.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("rodar", help="pipeline diário completo (e semanal aos domingos)")
    s.add_argument("--sem-email", action="store_true", help="gera o HTML sem enviar")
    s.add_argument("--semanal", action="store_true", help="força as etapas semanais hoje")
    s.set_defaults(func=cmd_rodar)

    sub.add_parser("coletar", help="só coleta e grava empresas").set_defaults(func=cmd_coletar)
    sub.add_parser("sondar", help="lê sites pendentes").set_defaults(func=cmd_sondar)

    s = sub.add_parser("extrair", help="extrai fatos com LLM (e recalcula o score)")
    s.add_argument("--legado", action="store_true", help="reprocessa as empresas vindas da v1")
    s.add_argument("--limite", type=int)
    s.add_argument("--orcamento", type=float, help="teto em US$ desta execução")
    s.add_argument("--minutos", type=int, help="prazo do reprocessamento (padrão 35)")
    s.set_defaults(func=cmd_extrair)

    s = sub.add_parser("pontuar", help="recalcula o score prévio")
    s.add_argument("--todos", action="store_true", help="recalcula todas, não só as desatualizadas")
    s.set_defaults(func=cmd_pontuar)

    sub.add_parser("sinais", help="busca sinais de tração das candidatas").set_defaults(func=cmd_sinais)

    s = sub.add_parser("escavar", help="atualiza a fila e escava")
    s.add_argument("--limite", type=int)
    s.set_defaults(func=cmd_escavar)

    s = sub.add_parser("investigar", help="escava uma empresa específica na hora")
    s.add_argument("termo", help="nome ou URL")
    s.add_argument("--orcamento", type=float)
    s.set_defaults(func=cmd_investigar)

    sub.add_parser("revisitar", help="revisitas D+30/90/180").set_defaults(func=cmd_revisitar)
    sub.add_parser("tendencias", help="agrupa as empresas recentes em temas").set_defaults(func=cmd_tendencias)

    s = sub.add_parser("feedback", help="registra um sinal seu para uma empresa")
    s.add_argument("empresa", type=int, help="id da empresa")
    s.add_argument("sinal", choices=db.SIGNALS)
    s.add_argument("--nota")
    s.set_defaults(func=cmd_feedback)

    sub.add_parser("ler-feedback", help="lê os sinais das Issues do GitHub").set_defaults(func=cmd_ler_feedback)

    s = sub.add_parser("enviar", help="gera (e envia) o e-mail")
    s.add_argument("tipo", choices=("diario", "semanal"))
    s.add_argument("--sem-email", action="store_true")
    s.set_defaults(func=cmd_enviar)

    sub.add_parser("avaliar", help="compara o score com o golden set").set_defaults(func=cmd_avaliar)
    s = sub.add_parser("golden", help="acrescenta empresas para rotular em data/golden.csv")
    s.add_argument("--amostra", type=int, default=50)
    s.set_defaults(func=cmd_golden)

    sub.add_parser("status", help="resumo do banco e das últimas execuções").set_defaults(func=cmd_status)
    sub.add_parser("migrar", help="aplica migrações pendentes").set_defaults(func=cmd_migrar)
    sub.add_parser("baixar-banco", help="traz o banco do branch data").set_defaults(func=cmd_baixar_banco)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = copy.deepcopy(config.load())
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
