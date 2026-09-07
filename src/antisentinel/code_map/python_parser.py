"""Python AST facts and byte-preserving source chunks."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import io
import tokenize
from typing import Mapping

from .identity import content_hash, node_id_for
from .models import CodeChunk, CodeEdge, CodeNode


@dataclass(frozen=True)
class ParsedFile:
    path: str
    data: bytes
    encoding: str
    file_hash: str
    symbols: tuple[CodeNode, ...]
    chunks: tuple[CodeChunk, ...]
    parents: Mapping[str, str]
    errors: tuple[str, ...] = ()
    tree: ast.Module | None = None


class PythonAstParser:
    def __init__(self, parser_revision: str, *, max_chunk_bytes: int = 16_384, max_chunk_lines: int = 200) -> None:
        self.parser_revision = parser_revision
        self.max_chunk_bytes = max_chunk_bytes
        self.max_chunk_lines = max_chunk_lines

    def parse_file(self, path: str, data: bytes, node_scope: str) -> ParsedFile:
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            text = data.decode(encoding)
            tree = ast.parse(text, filename=path)
        except (SyntaxError, UnicodeError, LookupError):
            return ParsedFile(path, data, "unknown", content_hash(data), (), (), {}, ("syntax_error",))
        line_offsets = _line_offsets(data)
        symbols: list[CodeNode] = []
        chunks: list[CodeChunk] = []
        parents: dict[str, str] = {}
        stack: list[CodeNode] = []

        def visit(body: list[ast.stmt]) -> None:
            for node in body:
                if isinstance(node, ast.ClassDef):
                    add(node, "class")
                elif isinstance(node, ast.FunctionDef):
                    add(node, "function")
                elif isinstance(node, ast.AsyncFunctionDef):
                    add(node, "async_function")

        def add(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
            start_line = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
            end_line = node.end_lineno or node.lineno
            qualified_name = f"{stack[-1].qualified_name}.{node.name}" if stack else node.name
            identifier = node_id_for(node_scope, path, kind, qualified_name, start_line, end_line)
            start_byte = line_offsets[start_line - 1]
            end_byte = line_offsets[end_line] if end_line < len(line_offsets) else len(data)
            symbol = CodeNode(
                node_id=identifier, snapshot_id=node_scope, repository_id="unbound", commit_sha="unbound", kind=kind,
                qualified_name=qualified_name, path=path, start_line=start_line, end_line=end_line,
                start_col=node.col_offset, end_col=node.end_col_offset,
                content_hash=content_hash(data[start_byte:end_byte]),
            )
            symbols.append(symbol)
            if stack:
                parents[symbol.node_id] = stack[-1].node_id
            chunks.extend(self._chunks_for(symbol, data, line_offsets, encoding))
            stack.append(symbol)
            visit(node.body)
            stack.pop()

        visit(tree.body)
        return ParsedFile(path, data, encoding, content_hash(data), tuple(symbols), tuple(chunks), parents, tree=tree)

    def contains_edges(self, parsed_files: tuple[ParsedFile, ...], snapshot_id: str) -> tuple[CodeEdge, ...]:
        edges: list[CodeEdge] = []
        for parsed in parsed_files:
            for child_id, parent_id in parsed.parents.items():
                identity = f"{snapshot_id}|{parent_id}|contains|{child_id}"
                edges.append(CodeEdge(
                    edge_id=hashlib.sha256(identity.encode()).hexdigest(), snapshot_id=snapshot_id,
                    source_node_id=parent_id, target_node_id=child_id, relation="contains",
                    resolution="resolved", basis="ast_parent",
                ))
        return tuple(edges)

    def _chunks_for(self, symbol: CodeNode, data: bytes, line_offsets: list[int], encoding: str) -> list[CodeChunk]:
        start = line_offsets[symbol.start_line - 1]
        end = line_offsets[symbol.end_line] if symbol.end_line < len(line_offsets) else len(data)
        pieces: list[tuple[int, int, bool]] = []
        cursor = start
        while cursor < end:
            line_limit = min(symbol.end_line, symbol.start_line + self.max_chunk_lines - 1)
            next_line = line_offsets[line_limit] if line_limit < len(line_offsets) else end
            target = min(end, next_line, cursor + self.max_chunk_bytes)
            partial_line = target < end and target != next_line
            if target == cursor:
                target = min(end, cursor + self.max_chunk_bytes)
                partial_line = True
            pieces.append((cursor, target, partial_line))
            cursor = target
        chunks: list[CodeChunk] = []
        for index, (byte_start, byte_end, partial_line) in enumerate(pieces):
            start_line = _line_for_offset(line_offsets, byte_start)
            end_line = _line_for_offset(line_offsets, max(byte_start, byte_end - 1))
            identity = f"{symbol.node_id}|{byte_start}|{byte_end}"
            chunks.append(CodeChunk(
                chunk_id=hashlib.sha256(identity.encode()).hexdigest(), node_id=symbol.node_id,
                snapshot_id=symbol.snapshot_id, path=symbol.path, start_line=start_line, end_line=end_line,
                byte_start=byte_start, byte_end=byte_end, content_hash=content_hash(data[byte_start:byte_end]),
                commit_sha="unbound", encoding=encoding, truncated=len(pieces) > 1, partial_line=partial_line,
            ))
        return chunks


def _line_offsets(data: bytes) -> list[int]:
    offsets = [0]
    cursor = 0
    for line in data.splitlines(keepends=True):
        cursor += len(line)
        offsets.append(cursor)
    if offsets[-1] != len(data):
        offsets.append(len(data))
    return offsets


def _line_for_offset(offsets: list[int], byte_offset: int) -> int:
    for index, value in enumerate(offsets[1:], start=1):
        if byte_offset < value:
            return index
    return max(1, len(offsets) - 1)
