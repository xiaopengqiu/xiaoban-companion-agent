"""SQLite persistence. Every access to personal objects requires a user id."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from uuid import uuid4


def uid() -> str:
    return uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class EventSuppressedError(ValueError):
    """A user-deleted source record must not be recreated by an automatic sync."""


class Store:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.connection.executescript('''
                PRAGMA foreign_keys=ON;
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, salt TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    source TEXT NOT NULL, external_id TEXT, data TEXT NOT NULL,
                    UNIQUE(user_id,source,external_id));
                CREATE TABLE IF NOT EXISTS event_suppressions (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    source TEXT NOT NULL, external_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id,source,external_id));
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preferences (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    dedupe_key TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(user_id,dedupe_key));
                CREATE TABLE IF NOT EXISTS integrations (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS webhook_receipts (
                    message_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    state TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_user ON events(user_id);
                CREATE INDEX IF NOT EXISTS memory_user ON memories(user_id);
                CREATE INDEX IF NOT EXISTS chat_user ON chat(user_id);
                CREATE INDEX IF NOT EXISTS reminder_user ON reminders(user_id);
            ''')

    @contextmanager
    def transaction(self):
        with self.lock, self.connection:
            yield

    def close(self):
        with self.lock:
            self.connection.close()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()

    def create_user(self, name, email, password, timezone_name, now, is_demo=False):
        salt = secrets.token_hex(16)
        user = {'id':uid(), 'name':name, 'email':email.lower(), 'timezone':timezone_name,
                'is_demo':is_demo, 'consent_health_data':True, 'created_at':now.isoformat()}
        hashed = self.password_hash(password, salt)
        with self.transaction():
            self.connection.execute('INSERT INTO users VALUES (?,?,?,?,?)',
                                    (user['id'],user['email'],hashed,salt,encode(user)))
            prefs = {'enabled':True, 'quiet_start':'22:00', 'quiet_end':'08:00', 'daily_limit':2,
                     'reminder_categories':['sleep','activity','meal'], 'timezone':timezone_name,'model_consent':False}
            self.connection.execute('INSERT INTO preferences VALUES (?,?)',(user['id'],encode(prefs)))
        return user

    def login(self, email, password):
        with self.lock:
            row = self.connection.execute('SELECT * FROM users WHERE email=?',(email.lower(),)).fetchone()
        if not row:
            # Keep expensive work on both branches rather than disclosing account existence by timing.
            self.password_hash(password, '00'*16)
            return None
        if hmac.compare_digest(self.password_hash(password,row['salt']),row['password_hash']):
            return json.loads(row['data'])
        return None

    def session(self, user_id, now):
        token = secrets.token_urlsafe(40)
        with self.transaction():
            self.connection.execute('INSERT INTO sessions VALUES (?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(),user_id,(now+timedelta(days=30)).isoformat()))
        return token

    def authenticate(self, token, now):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            row = self.connection.execute('SELECT users.data,sessions.expires_at FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=?',(digest,)).fetchone()
        if not row or datetime.fromisoformat(row['expires_at']) <= now:
            return None
        return json.loads(row['data'])

    def users(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.connection.execute('SELECT data FROM users')]

    def delete_user(self, user_id):
        with self.transaction():
            self.connection.execute('DELETE FROM users WHERE id=?',(user_id,))

    def list(self, table, user_id):
        assert table in {'events','memories','chat','reminders','devices'}
        with self.lock:
            rows = self.connection.execute(f'SELECT data FROM {table} WHERE user_id=? ORDER BY rowid',(user_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, table, user_id, item_id):
        assert table in {'events','memories','chat','reminders','devices'}
        with self.lock:
            row = self.connection.execute(f'SELECT data FROM {table} WHERE id=? AND user_id=?',(item_id,user_id)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, table, user_id, item):
        assert table in {'memories','chat','reminders'}
        with self.transaction():
            if table == 'reminders':
                self.connection.execute('INSERT INTO reminders VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE reminders.user_id=excluded.user_id',
                    (item['id'],user_id,item['dedupe_key'],encode(item)))
            else:
                self.connection.execute(f'INSERT INTO {table} VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE {table}.user_id=excluded.user_id',
                    (item['id'],user_id,encode(item)))
        return item

    def delete(self, table, user_id, item_id):
        assert table in {'events','memories','reminders'}
        with self.transaction():
            return self.connection.execute(f'DELETE FROM {table} WHERE id=? AND user_id=?',(item_id,user_id)).rowcount > 0

    def singleton(self, table, user_id, value=None):
        assert table in {'preferences','integrations'}
        if value is not None:
            with self.transaction():
                self.connection.execute(f'INSERT INTO {table} VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data',(user_id,encode(value)))
            return value
        with self.lock:
            row=self.connection.execute(f'SELECT data FROM {table} WHERE user_id=?',(user_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def invalidate(self, user_id, event_id):
        """Delete machine claims and cancel undelivered advice derived from a changed fact."""
        for memory in self.list('memories',user_id):
            if event_id in memory.get('evidence_ids',[]) and memory['origin'] != 'user':
                self.delete('memories',user_id,memory['id'])
        for reminder in self.list('reminders',user_id):
            if event_id in reminder.get('evidence_ids',[]) and reminder['status'] in {'pending','snoozed'}:
                reminder.update(status='cancelled',delivery_status='cancelled',cancel_reason='evidence_changed')
                self.put('reminders',user_id,reminder)

    def bump_context(self,user_id):
        prefs=self.singleton('preferences',user_id)
        prefs['_context_revision']=prefs.get('_context_revision',0)+1
        self.singleton('preferences',user_id,prefs)

    def upsert_event(self, user_id, event, now):
        event = dict(event)
        external_id=event.get('external_id') or None
        with self.transaction():
            if self.is_event_suppressed(user_id,event['source'],external_id):
                raise EventSuppressedError('该来源记录已删除，自动导入已屏蔽')
            row = self.connection.execute('SELECT data FROM events WHERE user_id=? AND source=? AND external_id=?',(user_id,event['source'],external_id)).fetchone() if external_id else None
            old = json.loads(row[0]) if row else None
            event.update(id=old['id'] if old else uid(),external_id=external_id,
                         created_at=old['created_at'] if old else now.isoformat(),updated_at=now.isoformat())
            # Do not invalidate evidence on an identical sync retry.
            keys = ('kind','value','unit','start_at','end_at','source','external_id','metadata')
            changed = old and any(old.get(k)!=event.get(k) for k in keys)
            if changed:
                self.bump_context(user_id)
                self.invalidate(user_id,old['id'])
            self.connection.execute('INSERT INTO events VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                (event['id'],user_id,event['source'],external_id,encode(event)))
        return event, old is None

    def is_event_suppressed(self,user_id,source,external_id):
        if not external_id:
            return False
        with self.lock:
            return self.connection.execute('SELECT 1 FROM event_suppressions WHERE user_id=? AND source=? AND external_id=?',
                (user_id,source,external_id)).fetchone() is not None

    def suppress_event(self,user_id,event,now):
        if event.get('external_id'):
            with self.transaction():
                self.connection.execute('INSERT OR IGNORE INTO event_suppressions VALUES (?,?,?,?)',
                    (user_id,event['source'],event['external_id'],now.isoformat()))

    def register_device(self, user_id, token, platform):
        item={'id':uid(),'token':token,'platform':platform}
        with self.transaction():
            # A device changing account must stop receiving the previous account's notifications.
            self.connection.execute('DELETE FROM devices WHERE token=?',(token,))
            self.connection.execute('INSERT INTO devices VALUES (?,?,?,?)',(item['id'],user_id,token,encode(item)))
        return item

    def claim_webhook(self,message_id,user_id,now):
        with self.transaction():
            row=self.connection.execute('SELECT state,created_at FROM webhook_receipts WHERE message_id=?',(message_id,)).fetchone()
            if row and (row['state']=='done' or now-datetime.fromisoformat(row['created_at'])<timedelta(minutes=5)):
                return False
            self.connection.execute('INSERT INTO webhook_receipts VALUES (?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET state=excluded.state,created_at=excluded.created_at',
                (message_id,user_id,'processing',now.isoformat()))
            return True

    def finish_webhook(self,message_id,success):
        with self.transaction():
            if success:
                self.connection.execute('UPDATE webhook_receipts SET state=? WHERE message_id=?',('done',message_id))
            else:
                self.connection.execute('DELETE FROM webhook_receipts WHERE message_id=?',(message_id,))
