"""Conexão, migrações e utilitários do banco.

Migrações ficam em app/migrations/NNN_nome.(sql|py), aplicadas em ordem e
registradas em PRAGMA user_version. O banco de produção vive num branch do
git e não pode ser recriado do zero — toda mudança de schema é uma migração nova.
"""
import importlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
# GARIMPO_DB permite testar com uma cópia do banco sem tocar no data/garimpo.db
DB_PATH = Path(os.environ.get("GARIMPO_DB") or ROOT / "data" / "garimpo.db")
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def days_ago_iso(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def to_json(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def from_json(text, expected_type=None, default=None):
    """Parse defensivo de JSON persistido — nunca levanta."""
    if text is None:
        return default
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return default
    if expected_type is not None and not isinstance(data, expected_type):
        return default
    return data


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migration_files() -> list[tuple[int, Path]]:
    found = []
    for path in MIGRATIONS_DIR.iterdir():
        m = re.fullmatch(r"(\d{3})_[a-z0-9_]+\.(sql|py)", path.name)
        if m:
            found.append((int(m.group(1)), path))
    return sorted(found)


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Aplica as migrações pendentes, cada uma numa transação. Retorna as aplicadas."""
    applied = []
    current = schema_version(conn)
    for number, path in _migration_files():
        if number <= current:
            continue
        try:
            conn.execute("BEGIN")
            if path.suffix == ".sql":
                for statement in _split_sql(path.read_text(encoding="utf-8")):
                    conn.execute(statement)
            else:
                module = importlib.import_module(f"app.migrations.{path.stem}")
                module.run(conn)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print(f"[db] migração {path.name} aplicada")
        applied.append(number)
        current = number
    return applied


def _split_sql(text: str) -> list[str]:
    """Divide um .sql em comandos (sem ';' dentro de strings nos nossos arquivos)."""
    lines = [l for l in text.splitlines() if not l.strip().startswith("--")]
    cleaned = "\n".join(re.sub(r"--[^\n']*$", "", l) for l in lines)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def vacuum(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("VACUUM")
