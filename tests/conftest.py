"""Fixtures compartilhadas. Banco SQLite real em arquivo temporario."""
import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def make_product(conn, name="FooApp", score=8.0, points=50):
    """Insere um produto e devolve seu id."""
    db.upsert(conn, {
        "name": name,
        "tagline": f"{name} faz algo util",
        "url": f"https://{name.lower()}.example.com",
        "source": "hackernews",
        "points": points,
    })
    conn.execute(
        "UPDATE products SET opportunity_score=?, niche='nicho teste', pain='dor teste' WHERE name=?",
        (score, name),
    )
    conn.commit()
    return conn.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()["id"]


@pytest.fixture(autouse=True)
def _sem_serper(monkeypatch):
    """Nenhum teste usa Serper real; quem precisa da chave seta explicitamente."""
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
