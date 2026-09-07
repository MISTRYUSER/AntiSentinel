"""Lightweight multi-language symbol parser for Go and TypeScript."""
from __future__ import annotations
import re
from .python_parser import ParsedFile
from .identity import content_hash, node_id_for
from .models import CodeNode

class MultiLanguageParser:
    extensions = {'.go': 'go', '.ts': 'typescript', '.tsx': 'typescript'}
    def __init__(self, parser_revision: str): self.parser_revision = parser_revision
    def parse_file(self, path: str, data: bytes, node_scope: str) -> ParsedFile:
        try: text=data.decode('utf-8')
        except UnicodeDecodeError: return ParsedFile(path,data,'unknown',content_hash(data),(),(),{},('decode_error',))
        lang = next((v for k,v in self.extensions.items() if path.endswith(k)), None)
        if not lang: return ParsedFile(path,data,'utf-8',content_hash(data),(),(),{})
        pats = ([(r'^\s*type\s+(\w+)\s+struct\b','class'),(r'^\s*func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(','function')] if lang=='go' else [(r'^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)','class'),(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)','function'),(r'^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(','function')])
        nodes=[]
        for i,line in enumerate(text.splitlines(),1):
            for pat,kind in pats:
                m=re.search(pat,line)
                if m:
                    name=m.group(1); q=name; nid=node_id_for(node_scope,path,kind,q,i,i)
                    nodes.append(CodeNode(nid,node_scope,'unbound','unbound',kind,q,path,i,i,content_hash(line.encode()),0,len(line.encode())))
                    break
        return ParsedFile(path,data,'utf-8',content_hash(data),tuple(nodes),(),{})
    def contains_edges(self, parsed_files, snapshot_id): return ()
