import csv, hashlib, os, secrets, sqlite3, time
from datetime import datetime, timedelta, timezone

DURATIONS = {
    '5m': 5 * 60,
    '10m': 10 * 60,
    '20m': 20 * 60,
    '1d': 1 * 86400,
    '2d': 2 * 86400,
    '3d': 3 * 86400,
    '8d': 8 * 86400,
    '16d': 16 * 86400,
    '31d': 31 * 86400,
}
CODES_PER_SESSION = 40


def utc_now():
    return datetime.now(timezone.utc)


def db_init(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS license_codes (
        code TEXT PRIMARY KEY,
        plan TEXT NOT NULL,
        duration_seconds INTEGER NOT NULL,
        used INTEGER DEFAULT 0,
        device_hash TEXT,
        activated_at TEXT,
        expires_at TEXT
    );
    CREATE TABLE IF NOT EXISTS license_sessions (
        token_hash TEXT PRIMARY KEY,
        code TEXT NOT NULL,
        device_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_license_sessions_device ON license_sessions(device_hash);
    CREATE INDEX IF NOT EXISTS idx_license_codes_used ON license_codes(used);
    ''')
    c.commit()


def ensure_codes(c, csv_path):
    """Import the 9 x 40 seed codes once. Never overwrites used codes."""
    db_init(c)
    count = c.execute('SELECT COUNT(*) FROM license_codes').fetchone()[0]
    if count:
        return count
    if not os.path.exists(csv_path):
        os.makedirs(os.path.dirname(csv_path) or '.', exist_ok=True)
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['code','plan','duration_seconds'])
            for plan, seconds in DURATIONS.items():
                for _ in range(CODES_PER_SESSION):
                    code = secrets.token_urlsafe(9).replace('-', '').replace('_', '')[:12].upper()
                    w.writerow([code, plan, seconds])
    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                c.execute('INSERT OR IGNORE INTO license_codes(code,plan,duration_seconds) VALUES(?,?,?)',
                          (row['code'].strip().upper(), row['plan'], int(row['duration_seconds'])))
            except Exception:
                pass
    c.commit()
    return c.execute('SELECT COUNT(*) FROM license_codes').fetchone()[0]


def device_hash(device_id, user_agent=''):
    raw = f'{device_id}|{user_agent}'.encode('utf-8', 'ignore')
    return hashlib.sha256(raw).hexdigest()


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def activate(c, code, device_id, user_agent=''):
    code = (code or '').strip().upper()
    if not code or not device_id:
        return {'ok': False, 'error': 'Code et appareil requis.'}
    now = utc_now()
    row = c.execute('SELECT * FROM license_codes WHERE code=?', (code,)).fetchone()
    if not row:
        return {'ok': False, 'error': 'Code invalide.'}
    if row['used']:
        return {'ok': False, 'error': 'Mot de passe déjà utilisé.'}
    dh = device_hash(device_id, user_agent)
    expires = now + timedelta(seconds=int(row['duration_seconds']))
    token = secrets.token_urlsafe(32)
    cur = c.execute('UPDATE license_codes SET used=1,device_hash=?,activated_at=?,expires_at=? WHERE code=? AND used=0',
                     (dh, now.isoformat(), expires.isoformat(), code))
    if cur.rowcount != 1:
        c.rollback()
        return {'ok': False, 'error': 'Mot de passe déjà utilisé.'}
    c.execute('INSERT INTO license_sessions(token_hash,code,device_hash,expires_at,created_at) VALUES(?,?,?,?,?)',
              (_token_hash(token), code, dh, expires.isoformat(), now.isoformat()))
    c.commit()
    return {'ok': True, 'token': token, 'plan': row['plan'], 'expires_at': expires.isoformat()}


def session(c, token, device_id, user_agent=''):
    if not token or not device_id:
        return None
    row = c.execute('SELECT * FROM license_sessions WHERE token_hash=?', (_token_hash(token),)).fetchone()
    if not row:
        return None
    if row['device_hash'] != device_hash(device_id, user_agent):
        return None
    try:
        exp = datetime.fromisoformat(row['expires_at'])
    except Exception:
        return None
    if utc_now() >= exp:
        return None
    return dict(row)


def stats(c):
    rows = c.execute('SELECT plan, COUNT(*) total, SUM(used) used FROM license_codes GROUP BY plan ORDER BY plan').fetchall()
    return [dict(r) for r in rows]
