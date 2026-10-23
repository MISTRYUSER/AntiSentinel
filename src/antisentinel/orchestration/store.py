"""SQLite snapshots with atomic event records and process-scoped execution locks."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3

from .dag import canonical, nonempty


class PlanBusy(RuntimeError):
    pass


class SQLitePlanStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_dir = self.path.with_name(self.path.name + '.locks')
        self.lock_dir.mkdir(exist_ok=True, mode=0o700)
        with self._db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS plan_runs (
                    run_id TEXT PRIMARY KEY, binding TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plan_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    event TEXT NOT NULL);
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def exclusive(self, run_id):
        nonempty(run_id, 'run_id')
        key = hashlib.sha256(run_id.encode()).hexdigest()
        # Keep lock files: unlinking them permits two processes to lock different inodes.
        with (self.lock_dir / key).open('a+b') as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PlanBusy('plan run is already running') from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self, run_id):
        with self._db() as db:
            row = db.execute('SELECT payload FROM plan_runs WHERE run_id=?', (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, snapshot, event=None):
        payload = canonical(snapshot)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT binding FROM plan_runs WHERE run_id=?', (snapshot['run_id'],)).fetchone()
            if row and row[0] != snapshot['binding']:
                raise ValueError('run binding changed')
            db.execute('INSERT INTO plan_runs VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload',
                       (snapshot['run_id'], snapshot['binding'], payload))
            if event is not None:
                db.execute('INSERT INTO plan_events(run_id,event) VALUES (?,?)', (snapshot['run_id'], canonical(event)))

    def events(self, run_id):
        with self._db() as db:
            rows = db.execute('SELECT event FROM plan_events WHERE run_id=? ORDER BY seq', (run_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]
