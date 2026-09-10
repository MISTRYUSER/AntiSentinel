"""Atomic publication of a reconciled SQLite lexical projection."""
from dataclasses import asdict
import json
import hashlib
from .models import normalize_document_record


def record(document):
    return {'document_id':document.document_id, **asdict(document)}


def fts_record(document):
    return {k: document[k] for k in ('document_id','path','symbol')} | {
        'identifiers':f"{document['path']} {document['symbol']}", 'comments':'', 'code':document['text']}


class LexicalPublication:
    def __init__(self, database):
        self.database=database
        with database.transaction() as c:
            fields={r['name'] for r in c.execute('PRAGMA table_info(code_search_manifests)')}
            for name in ('expected_json','reconciliation_json','base_revision'):
                if name not in fields:c.execute(f'ALTER TABLE code_search_manifests ADD COLUMN {name} TEXT')
            if 'published_once' not in fields:
                c.execute('ALTER TABLE code_search_manifests ADD COLUMN published_once INTEGER NOT NULL DEFAULT 0')
            c.execute('''CREATE TABLE IF NOT EXISTS code_search_active_projections (
                repository_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, published_generation INTEGER NOT NULL,
                commit_sha TEXT NOT NULL, projection_revision TEXT NOT NULL,
                PRIMARY KEY(repository_id,snapshot_id,published_generation,commit_sha))''')
            c.execute('''CREATE TABLE IF NOT EXISTS code_search_publication_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, repository_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL, published_generation INTEGER NOT NULL, commit_sha TEXT NOT NULL,
                projection_revision TEXT NOT NULL, status TEXT NOT NULL, report_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    def begin(self, scope, revision, documents):
        if not isinstance(revision,str) or not revision.strip():raise ValueError('projection revision required')
        values=[record(d) for d in documents]
        if len({d['document_id'] for d in values})!=len(values):raise ValueError('duplicate expected document')
        for d in values:
            if tuple(d[k] for k in ('repository_id','snapshot_id','published_generation','commit_sha'))!=scope.key or d['projection_revision']!=revision:
                raise ValueError('expected document scope or projection mismatch')
        encoded=json.dumps(sorted(values,key=lambda d:d['document_id']),sort_keys=True)
        with self.database.transaction() as c:
            old=c.execute('SELECT status,expected_json,base_revision FROM code_search_manifests WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND projection_revision=?',(*scope.key,revision)).fetchone()
            pointer=c.execute('SELECT projection_revision FROM code_search_active_projections WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=?',scope.key).fetchone()
            base_revision=pointer[0] if pointer else None
            if old and old['expected_json'] is not None:
                legacy = json.dumps(sorted((normalize_document_record(d) for d in json.loads(old['expected_json'])), key=lambda d:d['document_id']), sort_keys=True)
                if legacy!=encoded:raise ValueError('manifest expectation is immutable; use a new revision')
                if old['status'] in ('ready','building'):return
                base_revision=old['base_revision']
            c.execute('''INSERT INTO code_search_manifests
                (repository_id,snapshot_id,published_generation,commit_sha,projection_revision,status,expected_json,base_revision)
                VALUES (?,?,?,?,?,'building',?,?) ON CONFLICT(repository_id,snapshot_id,published_generation,commit_sha,projection_revision)
                DO UPDATE SET status='building',expected_json=excluded.expected_json,base_revision=excluded.base_revision,reconciliation_json=NULL''',(*scope.key,revision,encoded,base_revision))

    def publish(self, scope, revision):
        with self.database.transaction() as c:
            manifest=c.execute('SELECT status,expected_json,base_revision FROM code_search_manifests WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND projection_revision=?',(*scope.key,revision)).fetchone()
            if manifest is None or manifest['expected_json'] is None:raise ValueError('manifest expectation required before publication')
            pointer=c.execute('SELECT projection_revision FROM code_search_active_projections WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=?',scope.key).fetchone()
            current=pointer[0] if pointer else None
            conflict=current!=(revision if manifest['status']=='ready' else manifest['base_revision'])
            expected={d['document_id']:normalize_document_record(d) for d in json.loads(manifest['expected_json'])}
            actual={r['document_id']:dict(r) for r in c.execute('SELECT * FROM code_search_documents WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND projection_revision=?',(*scope.key,revision))}
            mismatches={}
            for key in expected.keys() & actual.keys():
                differences=[field for field,value in expected[key].items() if actual[key].get(field)!=value]
                lexical=list(c.execute('SELECT * FROM code_search_documents_fts WHERE document_id=?',(key,)))
                if len(lexical)!=1:differences.append('fts_row_count')
                else:differences += ['fts.'+field for field,value in fts_record(expected[key]).items() if lexical[0][field]!=value]
                if differences:mismatches[key]=sorted(differences)
            report={'expected_ids':sorted(expected),'actual_ids':sorted(actual),'missing_ids':sorted(expected.keys()-actual.keys()),'extra_ids':sorted(actual.keys()-expected.keys()),'field_mismatches':mismatches,'publication_conflict':conflict,'base_revision':manifest['base_revision'],'observed_active_revision':current}
            report['expected_digest']=hashlib.sha256(manifest['expected_json'].encode()).hexdigest()
            report['actual_digest']=hashlib.sha256(json.dumps(sorted(actual.values(),key=lambda d:d['document_id']),sort_keys=True).encode()).hexdigest()
            consistent=not report['missing_ids'] and not report['extra_ids'] and not mismatches and not conflict
            if consistent:
                c.execute("INSERT INTO code_search_documents_fts(code_search_documents_fts) VALUES('integrity-check')")
            c.execute('UPDATE code_search_manifests SET status=?,reconciliation_json=?,published_once=CASE WHEN ? THEN 1 ELSE published_once END WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND projection_revision=?',('ready' if consistent else 'failed',json.dumps(report,sort_keys=True),consistent,*scope.key,revision))
            c.execute('INSERT INTO code_search_publication_attempts(repository_id,snapshot_id,published_generation,commit_sha,projection_revision,status,report_json) VALUES(?,?,?,?,?,?,?)',(*scope.key,revision,'ready' if consistent else 'failed',json.dumps(report,sort_keys=True)))
            if consistent:
                c.execute('''INSERT INTO code_search_active_projections VALUES(?,?,?,?,?)
                    ON CONFLICT(repository_id,snapshot_id,published_generation,commit_sha)
                    DO UPDATE SET projection_revision=excluded.projection_revision''',(*scope.key,revision))
        if not consistent:raise ValueError('lexical reconciliation failed')
        return report

    def active(self, scope, revision=None):
        rows=self.database.query('''SELECT a.projection_revision FROM code_search_active_projections a
            JOIN code_search_manifests m USING(repository_id,snapshot_id,published_generation,commit_sha,projection_revision)
            WHERE a.repository_id=? AND a.snapshot_id=? AND a.published_generation=? AND a.commit_sha=?
            AND m.status='ready' AND m.expected_json IS NOT NULL''',scope.key)
        value=rows[0][0] if rows else None
        return value if revision is None or revision==value else None
