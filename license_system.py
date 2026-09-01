#!/usr/bin/env python3
"""
Système de licences Xcode Suit Card
- Lots de 40 codes par durée
- Usage unique + lié à 1 appareil (device fingerprint)
- Compte à rebours serveur
"""
import os, sqlite3, secrets, string
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "baccarat.db")

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "XCODE-ADMIN-2026-CHANGE-ME")
DURATIONS = {
    "5m":  {"seconds": 5 * 60,         "label": "5 minutes"},
    "10m": {"seconds": 10 * 60,        "label": "10 minutes"},
    "20m": {"seconds": 20 * 60,        "label": "20 minutes"},
    "1d":  {"seconds": 24 * 3600,      "label": "1 jour"},
    "2d":  {"seconds": 2 * 24 * 3600,  "label": "2 jours"},
    "3d":  {"seconds": 3 * 24 * 3600,  "label": "3 jours"},
    "8d":  {"seconds": 8 * 24 * 3600,  "label": "8 jours"},
    "31d": {"seconds": 31 * 24 * 3600, "label": "31 jours"},
}
CODES_PER_BATCH = 40
CODE_LENGTH = 14

def gen_code():
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    return "".join(secrets.choice(alphabet) for _ in range(CODE_LENGTH))

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA busy_timeout=60000")
    except Exception:
        pass
    return c

def init_license_tables():
    c = get_conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS license_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        duration_key TEXT NOT NULL,
        duration_seconds INTEGER NOT NULL,
        label TEXT NOT NULL,
        created_at TEXT NOT NULL,
        note TEXT
    );
    CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL,
        code TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'unused',
        device_id TEXT,
        activated_at TEXT,
        expires_at TEXT,
        last_seen TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (batch_id) REFERENCES license_batches(id)
    );
    CREATE INDEX IF NOT EXISTS idx_lic_code ON licenses(code);
    CREATE INDEX IF NOT EXISTS idx_lic_device ON licenses(device_id);
    CREATE INDEX IF NOT EXISTS idx_lic_status ON licenses(status);
    """)
    c.commit()
    c.close()

def create_batch(duration_key, note=""):
    if duration_key not in DURATIONS:
        raise ValueError(f"Durée inconnue: {duration_key}. Valides: {list(DURATIONS.keys())}")
    info = DURATIONS[duration_key]
    now = datetime.utcnow().isoformat()
    c = get_conn()
    cur = c.execute(
        "INSERT INTO license_batches (duration_key, duration_seconds, label, created_at, note) VALUES (?,?,?,?,?)",
        (duration_key, info["seconds"], info["label"], now, note or "")
    )
    batch_id = cur.lastrowid
    codes = []
    for _ in range(CODES_PER_BATCH):
        while True:
            code = gen_code()
            try:
                c.execute(
                    "INSERT INTO licenses (batch_id, code, status, created_at) VALUES (?,?,?,?)",
                    (batch_id, code, "unused", now)
                )
                codes.append(code)
                break
            except sqlite3.IntegrityError:
                continue
    c.commit()
    c.close()
    return {
        "batch_id": batch_id,
        "duration_key": duration_key,
        "label": info["label"],
        "seconds": info["seconds"],
        "codes": codes,
        "count": len(codes)
    }

def activate_license(code, device_id):
    code = (code or "").strip().upper()
    device_id = (device_id or "").strip()
    if not code or not device_id or len(device_id) < 8:
        return {"ok": False, "error": "Code ou device_id manquant / trop court"}

    c = get_conn()
    row = c.execute("SELECT * FROM licenses WHERE code=?", (code,)).fetchone()
    if not row:
        c.close()
        return {"ok": False, "error": "Code invalide"}

    now = datetime.utcnow()
    now_iso = now.isoformat()

    # Déjà utilisé par un AUTRE appareil
    if row["status"] == "active" and row["device_id"] and row["device_id"] != device_id:
        c.close()
        return {"ok": False, "error": "Mot de passe déjà utilisé sur un autre appareil"}

    # Même appareil → restaurer session
    if row["status"] == "active" and row["device_id"] == device_id:
        expires = datetime.fromisoformat(row["expires_at"])
        if expires <= now:
            c.execute("UPDATE licenses SET status='expired' WHERE id=?", (row["id"],))
            c.commit()
            c.close()
            return {"ok": False, "error": "Licence expirée"}
        c.execute("UPDATE licenses SET last_seen=? WHERE id=?", (now_iso, row["id"]))
        c.commit()
        remaining = int((expires - now).total_seconds())
        c.close()
        return {
            "ok": True, "status": "active", "code": code,
            "expires_at": row["expires_at"], "remaining_seconds": remaining,
            "device_id": device_id, "reactivated": True
        }

    if row["status"] in ("expired", "revoked"):
        c.close()
        return {"ok": False, "error": f"Licence {row['status']}"}

    # Première activation
    batch = c.execute("SELECT * FROM license_batches WHERE id=?", (row["batch_id"],)).fetchone()
    if not batch:
        c.close()
        return {"ok": False, "error": "Lot introuvable"}

    expires = now + timedelta(seconds=batch["duration_seconds"])
    expires_iso = expires.isoformat()
    c.execute(
        """UPDATE licenses SET status='active', device_id=?, activated_at=?, expires_at=?, last_seen=?
           WHERE id=? AND status='unused'""",
        (device_id, now_iso, expires_iso, now_iso, row["id"])
    )
    if c.total_changes == 0:
        c.close()
        return {"ok": False, "error": "Activation concurrente — réessayez"}
    c.commit()
    c.close()
    return {
        "ok": True, "status": "active", "code": code,
        "expires_at": expires_iso, "remaining_seconds": batch["duration_seconds"],
        "device_id": device_id, "label": batch["label"], "first_activation": True
    }

def check_license(device_id):
    device_id = (device_id or "").strip()
    if not device_id:
        return {"ok": False, "error": "device_id manquant"}
    c = get_conn()
    row = c.execute(
        "SELECT * FROM licenses WHERE device_id=? AND status='active' ORDER BY activated_at DESC LIMIT 1",
        (device_id,)
    ).fetchone()
    if not row:
        c.close()
        return {"ok": False, "error": "Aucune licence active pour cet appareil"}

    now = datetime.utcnow()
    expires = datetime.fromisoformat(row["expires_at"])
    if expires <= now:
        c.execute("UPDATE licenses SET status='expired' WHERE id=?", (row["id"],))
        c.commit()
        c.close()
        return {"ok": False, "error": "Licence expirée", "expired": True}

    remaining = int((expires - now).total_seconds())
    c.execute("UPDATE licenses SET last_seen=? WHERE id=?", (now.isoformat(), row["id"]))
    c.commit()
    c.close()
    return {
        "ok": True, "status": "active", "code": row["code"],
        "expires_at": row["expires_at"], "remaining_seconds": remaining,
        "device_id": device_id, "activated_at": row["activated_at"]
    }

def list_batches(limit=50):
    c = get_conn()
    rows = c.execute("""
        SELECT b.*,
               (SELECT COUNT(*) FROM licenses l WHERE l.batch_id=b.id) as total,
               (SELECT COUNT(*) FROM licenses l WHERE l.batch_id=b.id AND l.status='unused') as unused,
               (SELECT COUNT(*) FROM licenses l WHERE l.batch_id=b.id AND l.status='active') as active,
               (SELECT COUNT(*) FROM licenses l WHERE l.batch_id=b.id AND l.status='expired') as expired
        FROM license_batches b ORDER BY b.id DESC LIMIT ?
    """, (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

def get_batch_codes(batch_id):
    c = get_conn()
    batch = c.execute("SELECT * FROM license_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        c.close()
        return None
    codes = c.execute(
        "SELECT code, status, device_id, activated_at, expires_at FROM licenses WHERE batch_id=? ORDER BY id",
        (batch_id,)
    ).fetchall()
    c.close()
    return {"batch": dict(batch), "codes": [dict(x) for x in codes]}
