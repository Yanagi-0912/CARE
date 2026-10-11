"""Durable, local experiment journal. All mutations commit before returning."""
import json
import sqlite3

def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

class Journal:

    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('\n        CREATE TABLE IF NOT EXISTS config (version TEXT PRIMARY KEY, identity TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS results (run_key TEXT PRIMARY KEY, payload TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS receipts (run_key TEXT, stage TEXT, input_hash TEXT, status TEXT, payload TEXT, PRIMARY KEY(run_key,stage));\n        CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, input_hash TEXT, model TEXT, payload TEXT, state TEXT, job TEXT, response TEXT, queued REAL, submitted REAL, completed REAL, local_cancelled INTEGER DEFAULT 0);\n        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, name TEXT, state TEXT, payload TEXT, created REAL);\n        ')
        if 'raw_output' not in {r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute('ALTER TABLE jobs ADD COLUMN raw_output TEXT')
        if 'error' not in {r[1] for r in self.db.execute('PRAGMA table_info(requests)')}:
            self.db.execute('ALTER TABLE requests ADD COLUMN error TEXT')
        self.db.execute("UPDATE jobs SET state='SUBMIT_UNKNOWN' WHERE state='SUBMITTING'")
        self.db.execute("UPDATE receipts SET status='unknown' WHERE status='started'")
        self.db.commit()

    def assert_no_uncertain_receipts(self):
        if self.db.execute("SELECT 1 FROM receipts WHERE status='unknown' LIMIT 1").fetchone():
            raise ValueError('Uncertain receipt: manual same-run receipt reconcile required; automatic retries disabled')

    def bind_config(self, config_version, identity):
        row = self.db.execute('SELECT * FROM config').fetchone()
        if row and (row['version'] != config_version or row['identity'] != encode(identity)):
            raise ValueError('Journal configuration identity mismatch')
        self.db.execute('INSERT OR IGNORE INTO config VALUES (?,?)', (config_version, encode(identity)))
        self.db.commit()

    def get_result(self, run_key):
        row = self.db.execute('SELECT payload FROM results WHERE run_key=?', (run_key,)).fetchone()
        return json.loads(row['payload']) if row else None

    def save_result(self, run_key, row):
        self.db.execute('INSERT OR REPLACE INTO results VALUES (?,?)', (run_key, encode(row)))
        self.db.commit()

    def results(self):
        return [json.loads(r['payload']) for r in self.db.execute('SELECT payload FROM results ORDER BY run_key')]

    def get_receipt(self, run_key, stage, input_hash):
        row = self.db.execute('SELECT * FROM receipts WHERE run_key=? AND stage=?', (run_key, stage)).fetchone()
        if not row:
            return None
        if row['input_hash'] != input_hash:
            raise ValueError('Receipt input hash mismatch')
        return {'status': row['status'], 'payload': json.loads(row['payload'])}

    def save_receipt(self, run_key, stage, input_hash, status, payload):
        self.get_receipt(run_key, stage, input_hash)
        self.db.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?,?)', (run_key, stage, input_hash, status, encode(payload)))
        self.db.commit()

    def close(self):
        self.db.close()
