"""Permite `python -m app <comando>`."""
import sys

from app.cli import main

sys.exit(main())
