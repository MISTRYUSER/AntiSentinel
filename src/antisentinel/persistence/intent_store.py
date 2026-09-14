"""SQLite intent revisions. Each operation owns its connection and closes it."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from antisentinel.domain.intent import ResolvedIntent, canonical
from antisentinel.ports.intent_store import IntentAccessDenied, IntentConflict


class SQLiteIntentStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS heads (
                    intent_id TEXT PRIMARY KEY, actor TEXT NOT NULL, session TEXT NOT NULL,
                    incident TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    intent_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(intent_id, revision));
                CREATE TABLE IF NOT EXISTS messages (
                    actor TEXT NOT NULL, session TEXT NOT NULL, incident TEXT NOT NULL,
                    message_id TEXT NOT NULL, context_version TEXT NOT NULL, input_hash TEXT NOT NULL,
                    intent_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    PRIMARY KEY(actor, session, message_id, context_version));
                CREATE TABLE IF NOT EXISTS outbox (
                    intent_id TEXT NOT NULL, revision INTEGER NOT NULL, route TEXT NOT NULL,
                    operation TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(intent_id, revision, route, operation));
                CREATE TABLE IF NOT EXISTS questions (
                    question_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    rounds INTEGER NOT NULL, state TEXT NOT NULL, expires_at REAL NOT NULL,
                    question TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, intent_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    message_id TEXT NOT NULL, session TEXT NOT NULL, type TEXT NOT NULL,
                    created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS route_receipts (
                    intent_id TEXT NOT NULL, revision INTEGER NOT NULL, step TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(intent_id, revision, step));
                CREATE TABLE IF NOT EXISTS recognition_failures (
                    actor TEXT NOT NULL, session TEXT NOT NULL, incident TEXT NOT NULL,
                    message_id TEXT NOT NULL, context_version TEXT NOT NULL,
                    code TEXT NOT NULL, attempts INTEGER NOT NULL,
                    PRIMARY KEY(actor, session, message_id, context_version));
            ''')
            # Upgrade stage-2 databases without rewriting immutable revisions.
            if 'metadata' not in {row['name'] for row in db.execute('PRAGMA table_info(events)')}:
                db.execute("ALTER TABLE events ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _scope(db, iid, actor, session, incident):
        row = db.execute('SELECT * FROM heads WHERE intent_id=?', (iid,)).fetchone()
        if row is None or (row['actor'], row['session'], row['incident']) != (actor, session, incident):
            raise IntentAccessDenied('intent unavailable in authorized scope')
        return row

    @staticmethod
    def _replay(db, ctx, input_hash):
        row = db.execute('SELECT * FROM messages WHERE actor=? AND session=? AND message_id=? AND context_version=?',
                         (ctx['actor_id'], ctx['session_id'], ctx['message_id'], ctx['context_version'])).fetchone()
        if row is None:
            return None
        if row['incident'] != ctx['incident_id']:
            raise IntentAccessDenied('message unavailable in authorized scope')
        if row['input_hash'] != input_hash:
            raise IntentConflict('same message key with different content')
        payload = db.execute('SELECT payload FROM revisions WHERE intent_id=? AND revision=?',
                             (row['intent_id'], row['revision'])).fetchone()['payload']
        return ResolvedIntent.from_dict(json.loads(payload))

    def replay(self, ctx, input_hash):
        with self._connect() as db:
            return self._replay(db, ctx, input_hash)

    def prior_message(self, ctx):
        with self._connect() as db:
            row = db.execute('SELECT intent_id,incident FROM messages WHERE actor=? AND session=? AND message_id=? ORDER BY rowid DESC LIMIT 1',
                             (ctx['actor_id'], ctx['session_id'], ctx['message_id'])).fetchone()
            if row is not None and row['incident'] != ctx['incident_id']:
                raise IntentAccessDenied('message unavailable in authorized scope')
            return row['intent_id'] if row else None

    def get(self, intent_id, actor, session, incident):
        with self._connect() as db:
            head = self._scope(db, intent_id, actor, session, incident)
            row = db.execute('SELECT payload FROM revisions WHERE intent_id=? AND revision=?',
                             (intent_id, head['revision'])).fetchone()
            return ResolvedIntent.from_dict(json.loads(row['payload']))

    def question(self, intent_id, actor, session, incident, *, now):
        with self._connect() as db:
            self._scope(db, intent_id, actor, session, incident)
            db.execute("UPDATE questions SET state='expired' WHERE intent_id=? AND state IN ('open','waiting_user') AND expires_at<=?", (intent_id, now))
            row = db.execute('SELECT * FROM questions WHERE intent_id=? ORDER BY revision DESC LIMIT 1', (intent_id,)).fetchone()
            return dict(row) if row else None

    def commit(self, intent, ctx, input_hash, expected_revision, *, now):
        value = intent.to_dict()
        ident = value['identity']
        iid, revision = ident['intent_id'], ident['revision']
        if revision != expected_revision + 1:
            raise IntentConflict('revision must increment exactly once')
        if any(ident[k] != ctx[k] for k in ('session_id', 'incident_id', 'message_id')):
            raise IntentAccessDenied('snapshot scope mismatch')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            replay = self._replay(db, ctx, input_hash)
            if replay is not None:
                return replay
            if expected_revision:
                head = self._scope(db, iid, ctx['actor_id'], ctx['session_id'], ctx['incident_id'])
                if head['revision'] != expected_revision:
                    raise IntentConflict('stale expected revision')
                db.execute('UPDATE heads SET revision=? WHERE intent_id=?', (revision, iid))
            else:
                if db.execute('SELECT 1 FROM heads WHERE intent_id=?', (iid,)).fetchone():
                    raise IntentConflict('intent already exists')
                db.execute('INSERT INTO heads VALUES (?,?,?,?,?)', (iid, ctx['actor_id'], ctx['session_id'], ctx['incident_id'], revision))
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (iid, revision, canonical(value)))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',
                       (ctx['actor_id'], ctx['session_id'], ctx['incident_id'], ctx['message_id'], ctx['context_version'], input_hash, iid, revision))
            db.execute("UPDATE outbox SET status='superseded' WHERE intent_id=? AND status='pending'", (iid,))
            last = db.execute('SELECT * FROM questions WHERE intent_id=? ORDER BY revision DESC LIMIT 1', (iid,)).fetchone()
            db.execute("UPDATE questions SET state='superseded' WHERE intent_id=? AND state IN ('open','waiting_user')", (iid,))
            if value['resolution_status'] == 'needs_clarification':
                rounds = min((last['rounds'] if last else 0) + 1, 3)
                paused = last is not None and last['rounds'] >= 3
                expiry = last['expires_at'] if paused else now + 86400
                state = ('expired' if expiry <= now else 'waiting_user') if paused else 'open'
                db.execute('INSERT INTO questions VALUES (?,?,?,?,?,?,?)',
                           (str(uuid4()), iid, revision, rounds, state, expiry, value['clarification']['question']))
            elif value['resolution_status'] == 'resolved':
                operation = value['control']['operation'] if value['control'] else ''
                db.execute('INSERT INTO outbox VALUES (?,?,?,?,?)', (iid, revision, value['route'], operation, 'pending'))
            event_types = ['intent.received', 'intent.resolved']
            if expected_revision:
                event_types.append('intent.revised')
            if value['resolution_status'] == 'needs_clarification' and (last is None or last['rounds'] < 3):
                event_types.append('intent.clarification_requested')
            for event in event_types:
                db.execute('INSERT INTO events(intent_id,revision,message_id,session,type,created_at) VALUES (?,?,?,?,?,?)',
                           (iid, revision, ctx['message_id'], ctx['session_id'], event, now))
        return intent

    def pending(self, actor, session, incident):
        with self._connect() as db:
            rows = db.execute("SELECT o.* FROM outbox o JOIN heads h USING(intent_id) WHERE h.actor=? AND h.session=? AND h.incident=? AND o.status='pending' AND o.revision=h.revision", (actor, session, incident)).fetchall()
            return [dict(row) for row in rows]

    def receipt(self, iid, revision, step, actor, session, incident):
        with self._connect() as db:
            self._scope(db, iid, actor, session, incident)
            row = db.execute('SELECT payload FROM route_receipts WHERE intent_id=? AND revision=? AND step=?',
                             (iid, revision, step)).fetchone()
            return json.loads(row['payload']) if row else None

    @contextmanager
    def revision_guard(self, iid, revision, actor, session, incident):
        """Serialize short receiver acceptance against local revision publication.

        Hold only across bounded acceptance, never across business execution.
        """
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            head = self._scope(db, iid, actor, session, incident)
            if head['revision'] != revision:
                raise IntentConflict('intent revision superseded at acceptance')
            yield

    def save_receipt(self, receipt, actor, session, incident):
        from time import time
        ref = receipt['intent_ref']
        iid, rev, step = ref['intent_id'], ref['revision'], receipt['step']
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._scope(db, iid, actor, session, incident)
            row = db.execute('SELECT payload FROM revisions WHERE intent_id=? AND revision=?', (iid, rev)).fetchone()
            if row is None:
                raise IntentConflict('receipt revision missing')
            intent = ResolvedIntent.from_dict(json.loads(row['payload'])).to_dict()
            if intent['identity']['content_hash'] != ref['content_hash']:
                raise IntentConflict('receipt hash mismatch')
            existing = db.execute('SELECT payload FROM route_receipts WHERE intent_id=? AND revision=? AND step=?', (iid, rev, step)).fetchone()
            if existing:
                old = json.loads(existing['payload'])
                if old == receipt:
                    return
                before, after = dict(old), dict(receipt)
                before.pop('status'); after.pop('status')
                if before != after or old['status'] != 'accepted' or receipt['status'] not in ('completed', 'failed'):
                    raise IntentConflict('invalid receipt transition')
            db.execute('INSERT OR REPLACE INTO route_receipts VALUES (?,?,?,?)', (iid, rev, step, canonical(receipt)))
            if step == 'main':
                db.execute("UPDATE outbox SET status=? WHERE intent_id=? AND revision=? AND status!='superseded'", (receipt['status'], iid, rev))
            db.execute('INSERT INTO events(intent_id,revision,message_id,session,type,created_at,metadata) VALUES (?,?,?,?,?,?,?)',
                       (iid, rev, intent['identity']['message_id'], session,
                        'intent.failed' if receipt['status'] == 'failed' else 'intent.routed', time(),
                        canonical({'receipt_id': receipt['receipt_id'], 'status': receipt['status'],
                                   'step': step, 'receiver_kind': receipt['receiver_kind'],
                                   'plan_id': receipt['downstream_id'] if receipt['route'] == 'plan' else None})))

    def counts(self):
        """Operational table counts, without payloads or authorization assertions."""
        with self._connect() as db:
            return {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                    for table in ('revisions', 'messages', 'outbox', 'questions', 'events')}

    def record_recognition_failure(self, ctx, code, attempts):
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO recognition_failures VALUES (?,?,?,?,?,?,?)',
                       (ctx['actor_id'], ctx['session_id'], ctx['incident_id'], ctx['message_id'],
                        ctx['context_version'], code, attempts))
