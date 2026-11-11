"""cronlint — audit crontabs the way an attacker reads them.

The package is importable with no third-party modules installed; the optional
MCP server and the CLI import their own extras lazily.
"""

from .__about__ import __version__
from .engine import run
from .model import Report
from .parser import parse, parse_file

__all__ = ["Report", "__version__", "parse", "parse_file", "run"]
