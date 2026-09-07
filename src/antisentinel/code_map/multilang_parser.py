"""Lightweight multi-language symbol parser for Go and TypeScript."""
from __future__ import annotations
import re
from .python_parser import ParsedFile
from .identity import content_hash, node_id_for
from .models import CodeNode, CodeChunk

class MultiLanguageParser:
    extensions = {'.go': 'go', '.ts': 'typescript', '.tsx': 'typescript', '.java': 'java', '.kt': 'kotlin', '.rs': 'rust', '.cpp': 'cpp', '.cc': 'cpp', '.h': 'cpp'}
    def __init__(self, parser_revision: str): self.parser_revision = parser_revision
    def parse_file(self, path: str, data: bytes, node_scope: str) -> ParsedFile:
        try: text=data.decode('utf-8')
        except UnicodeDecodeError: return ParsedFile(path,data,'unknown',content_hash(data),(),(),{},('decode_error',))
        syntax_error = _tree_sitter_error(path, data)
        lang = next((v for k,v in self.extensions.items() if path.endswith(k)), None)
        if not lang: return ParsedFile(path,data,'utf-8',content_hash(data),(),(),{})
        pats = {
            "go": [(r"^\s*type\s+(\w+)\s+struct\b", "class"), (r"^\s*func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(", "function")],
            "rust": [(r"^\s*(?:pub\s+)?struct\s+(\w+)", "class"), (r"^\s*(?:pub\s+)?fn\s+(\w+)\s*\(", "function")],
            "kotlin": [(r"^\s*(?:class|interface|object)\s+(\w+)", "class"), (r"^\s*fun\s+(\w+)\s*\(", "function")],
            "cpp": [(r"^\s*(?:class|struct|namespace)\s+(\w+)", "class"), (r"^\s*[\w:<>&*~]+\s+(\w+)\s*\([^;]*\)\s*[{;]", "function")],
            "java": [(r"^\s*(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)", "class"), (r"^\s*[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{", "function")],
            "typescript": [(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", "class"), (r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"), (r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=", "function")],
        }[lang]
        nodes=[]
        parents={}
        module_name=path.rsplit('/',1)[-1].rsplit('.',1)[0]
        module_id=node_id_for(node_scope,path,"module",module_name,1,max(1,len(text.splitlines())))
        module=CodeNode(module_id,node_scope,"unbound","unbound","module",module_name,path,1,max(1,len(text.splitlines())),content_hash(data),0,0)
        nodes.append(module)
        for i,line in enumerate(text.splitlines(),1):
            for pat,kind in pats:
                m=re.search(pat,line)
                if m:
                    name=m.group(1); q=name; nid=node_id_for(node_scope,path,kind,q,i,i)
                    nodes.append(CodeNode(nid,node_scope,'unbound','unbound',kind,q,path,i,i,content_hash(line.encode()),0,len(line.encode())))
                    parents[nid]=module_id
                    break
        lines=text.splitlines(True)
        chunks=tuple(CodeChunk(chunk_id=n.node_id+"-chunk", node_id=n.node_id, snapshot_id=node_scope, path=path, start_line=n.start_line, end_line=n.end_line, byte_start=sum(len(x.encode()) for x in lines[:n.start_line-1]), byte_end=sum(len(x.encode()) for x in lines[:n.end_line]), content_hash=content_hash(lines[n.start_line-1].encode()), commit_sha="unbound") for n in nodes)
        return ParsedFile(path,data,'utf-8',content_hash(data),tuple(nodes),chunks,parents, (('syntax_error',) if syntax_error else ()))
    def contains_edges(self, parsed_files, snapshot_id):
        from .models import CodeEdge
        import hashlib
        out=[]
        for parsed in parsed_files:
            for child,parent in parsed.parents.items():
                eid=hashlib.sha256(f"{parent}|contains|{child}".encode()).hexdigest()
                out.append(CodeEdge(eid,snapshot_id,parent,"contains",child,None,None,None,"resolved","syntax"))
        return tuple(out)
    def resolve_edges(self, parsed_files, snapshot_id):
        from .models import CodeEdge
        import hashlib
        by_name={n.qualified_name:n for p in parsed_files for n in p.symbols}
        out=[]
        def source_for(parsed, line_no):
            candidates=[n for n in parsed.symbols if n.kind != 'module' and n.start_line <= line_no]
            return max(candidates, key=lambda n:n.start_line) if candidates else (parsed.symbols[0] if parsed.symbols else None)
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
                    expr=line.strip(); src=source_for(p, i)
                    if src:
                        eid=hashlib.sha256(f'{src.node_id}|imports|{expr}|{i}'.encode()).hexdigest()
                        out.append(CodeEdge(eid,snapshot_id,src.node_id,'imports',None,expr,i,i,'unresolved','text'))
                if '(' in line and not any(line.lstrip().startswith(x) for x in ('func ','function ')):
                    src=source_for(p, i)
                    if src:
                        for token in re.findall(r'\b[A-Za-z_]\w*', line):
                            target=by_name.get(token)
                            if target and target.node_id != src.node_id:
                                eid=hashlib.sha256(f'{src.node_id}|calls|{target.node_id}|{i}'.encode()).hexdigest()
                                out.append(CodeEdge(eid,snapshot_id,src.node_id,'calls',target.node_id,None,i,i,'resolved','text'))
        return tuple(out)


def _tree_sitter_error(path, data):
    try:
        from tree_sitter_language_pack import get_parser
        ext=path.rsplit('.',1)[-1].lower()
        lang={'go':'go','ts':'typescript','tsx':'tsx','java':'java','kt':'kotlin','rs':'rust','cpp':'cpp','cc':'cpp','h':'cpp'}.get(ext)
        if not lang: return False
        root=get_parser(lang).parse(data).root_node
        return root.has_error
    except Exception:
        return False
