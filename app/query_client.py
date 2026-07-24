from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local_query import LocalQueryExecutor


@dataclass
class QueryResult:
    column_names: tuple[str, ...]
    result_rows: list[tuple[Any, ...]]


class LocalClientAdapter:
    """Small clickhouse-connect compatible adapter for legacy snapshot builders."""

    def __init__(self, root: Path, executor: LocalQueryExecutor | None = None):
        self.root = root.resolve()
        self.executor = executor or LocalQueryExecutor(self.root)

    def _describe(self, table: str) -> QueryResult:
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("pyarrow is required for local Warehouse queries") from exc
        table_name = table.split(".")[-1]
        pattern = self.root / table_name / "*.parquet"
        if not pattern.exists():
            candidates = sorted((self.root / table_name).glob("**/*.parquet"))
        else:
            candidates = [pattern]
        files = [path for path in candidates if path.is_file()]
        if not files:
            return QueryResult(("name",), [])
        schema = pq.ParquetFile(files[0]).schema_arrow
        return QueryResult(("name",), [(field.name,) for field in schema])

    def query(self, query: str, parameters: dict[str, Any] | None = None) -> QueryResult:
        stripped = query.strip().upper()
        if stripped.startswith("DESCRIBE TABLE"):
            table = query.strip().split()[-1]
            return self._describe(table)
        rows = self.executor.rows(query, parameters or {})
        if not rows:
            return QueryResult(tuple(), [])
        columns = tuple(rows[0].keys())
        return QueryResult(columns, [tuple(row.get(column) for column in columns) for row in rows])
