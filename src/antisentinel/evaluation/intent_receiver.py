"""Durable contract receiver for tests/Cases; does not run plans, answers or controls."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from antisentinel.domain.intent import canonical
from antisentinel.ports.intent_store import IntentConflict


class SQLiteIntentReceiver:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS accepted (key TEXT PRIMARY KEY, request TEXT NOT NULL, receipt TEXT NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def lookup(self, key):
        with self._db() as db:
            row = db.execute('SELECT receipt FROM accepted WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def submit(self, request, validate_current):
        with validate_current(), self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT request,receipt FROM accepted WHERE key=?', (request['key'],)).fetchone()
            if row:
                if row[0] != canonical(request):
                    raise IntentConflict('same delivery key with different payload')
                return json.loads(row[1])
            receipt = {k: request[k] for k in ('key', 'intent_ref', 'route', 'step')}
            receipt.update(receipt_id=str(uuid4()), status='accepted', receiver_kind='contract',
                           downstream_id=f"contract-{request['route']}-{uuid4()}")
            db.execute('INSERT INTO accepted VALUES (?,?,?)', (request['key'], canonical(request), canonical(receipt)))
            return receipt

    def complete(self, key):
        """Simulate a downstream terminal acknowledgment in a contract-only Case."""
        with self._db() as db:
            row = db.execute('SELECT receipt FROM accepted WHERE key=?', (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            receipt = json.loads(row[0])
            receipt['status'] = 'completed'
            db.execute('UPDATE accepted SET receipt=? WHERE key=?', (canonical(receipt), key))

    def records(self):
        with self._db() as db:
            return [{'key': r[0], 'request': json.loads(r[1]), 'receipt': json.loads(r[2])}
                    for r in db.execute('SELECT key,request,receipt FROM accepted ORDER BY rowid')]
