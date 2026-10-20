"""Atalho de compatibilidade com a v1.

    python -m app.main               -> python -m app rodar
    python -m app.main --no-mail     -> python -m app rodar --sem-email
    python -m app.main --investigar X -> python -m app investigar X
"""
import sys

from app.cli import main


def translate(argv: list[str]) -> list[str]:
    if "--investigar" in argv:
        idx = argv.index("--investigar")
        if idx + 1 >= len(argv):
            print('uso: python -m app investigar "nome ou url"')
            sys.exit(2)
        return ["investigar", argv[idx + 1]]
    return ["rodar"] + (["--sem-email"] if "--no-mail" in argv else [])


if __name__ == "__main__":
    sys.exit(main(translate(sys.argv[1:])))
