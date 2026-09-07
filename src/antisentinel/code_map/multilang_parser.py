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
    def resolve_edges(self, parsed_files, snapshot_id):
        from .models import CodeEdge
        import hashlib
        by_name={n.qualified_name:n for p in parsed_files for n in p.symbols}
        out=[]
        # lexical inheritance edges for Go embedding and TypeScript extends/implements
        for p in parsed_files:
            text=p.data.decode(p.encoding,errors="replace")
            for i,line in enumerate(text.splitlines(),1):
                m=re.search(r"\bclass\s+(\w+)\s+extends\s+(\w+)", line) or re.search(r"\btype\s+(\w+)\s+struct\s*\{([^}]*)", line)
                if not m: continue
                child=by_name.get(m.group(1)); parent_name=(m.group(2) if m.lastindex==2 and m.group(2) else "")
                if child and parent_name:
                    parent=by_name.get(parent_name); eid=hashlib.sha256(f"{child.node_id}|inherits|{parent.node_id if parent else parent_name}|{i}".encode()).hexdigest()
                    out.append(CodeEdge(eid,snapshot_id,child.node_id,"inherits",parent.node_id if parent else None,parent_name if not parent else None,i,i,"resolved" if parent else "unresolved","text"))
        for p in parsed_files:
            text=p.data.decode(p.encoding,errors='replace')
            for i,line in enumerate(text.splitlines(),1):
                if re.search(r'^\s*import\s|^\s*from\s+\S+\s+import\s+',line):
                    expr=line.strip(); src=p.symbols[0] if p.symbols else None
                    if src:
                        eid=hashlib.sha256(f'{src.node_id}|imports|{expr}|{i}'.encode()).hexdigest()
                        out.append(CodeEdge(eid,snapshot_id,src.node_id,'imports',None,expr,i,i,'unresolved','text'))
                if '(' in line and not any(line.lstrip().startswith(x) for x in ('func ','function ')):
                    src=p.symbols[0] if p.symbols else None
                    if src:
                        for token in re.findall(r'\b[A-Za-z_]\w*', line):
                            target=by_name.get(token)
                            if target and target.node_id != src.node_id:
                                eid=hashlib.sha256(f'{src.node_id}|calls|{target.node_id}|{i}'.encode()).hexdigest()
                                out.append(CodeEdge(eid,snapshot_id,src.node_id,'calls',target.node_id,None,i,i,'resolved','text'))
        return tuple(out)
