"""SQLite connection handling and schema migrations."""
from .connection import Database
from .migrations import SCHEMA_VERSION, migrate

__all__ = ['Database', 'SCHEMA_VERSION', 'migrate']
