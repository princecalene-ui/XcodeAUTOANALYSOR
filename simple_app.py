#!/usr/bin/env python3
"""
Xcode SUIT CARD — Moteur auto-apprenant
Collecte → Analyse → Strategies scorees → Prediction → Validation → Bascule
Focus : enseigne 1ere carte JOUEUR | Stockage 100% serveur
"""
import os, json, sqlite3, re, time, threading
from datetime import datetime
from collections import Counter, defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
from bs4 import BeautifulSoup
import secrets, string
from datetime import timedelta

# ═══ LICENCE SYSTEM ═══
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "XCODE-ADMIN-2026-CHANGE-ME")
DURATIONS = {
    "5m":  {"seconds": 5*60, "label": "5 minutes"},
    "10m": {"seconds": 10*60, "label": "10 minutes"},
    "20m": {"seconds": 20*60, "label": "20 minutes"},
    "1d":  {"seconds": 24*3600, "label": "1 jour"},
    "2d":  {"seconds": 2*24*3600, "label": "2 jours"},
    "3d":  {"seconds": 3*24*3600, "label": "3 jours"},
    "8d":  {"seconds": 8*24*3600, "label": "8 jours"},
    "31d": {"seconds": 31*24*3600, "label": "31 jours"},
}
CODES_PER_BATCH = 40
CODE_LENGTH = 14

def gen_code():
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace("O","").replace("0","").replace("I","").replace("1","")
    return "".join(secrets.choice(alphabet) for _ in range(CODE_LENGTH))

def init_license_tables():
    c = get_conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS license_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        duration_key TEXT NOT NULL, duration_seconds INTEGER NOT NULL,
        label TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT);
    CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL,
        code TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'unused',
        device_id TEXT, activated_at TEXT, expires_at TEXT, last_seen TEXT,
        created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_lic_code ON licenses(code);
    CREATE INDEX IF NOT EXISTS idx_lic_device ON licenses(device_id);
    """)
    c.commit(); c.close()

def create_batch(duration_key, note=""):
    if duration_key not in DURATIONS:
        raise ValueError("Duree inconnue: "+duration_key)
    info = DURATIONS[duration_key]
    now = datetime.utcnow().isoformat()
    c = get_conn()
    cur = c.execute("INSERT INTO license_batches (duration_key,duration_seconds,label,created_at,note) VALUES (?,?,?,?,?)",
                    (duration_key, info["seconds"], info["label"], now, note or ""))
    batch_id = cur.lastrowid
    codes = []
    for _ in range(CODES_PER_BATCH):
        while True:
            code = gen_code()
            try:
                c.execute("INSERT INTO licenses (batch_id,code,status,created_at) VALUES (?,?,?,?)",
                          (batch_id, code, "unused", now))
                codes.append(code); break
            except sqlite3.IntegrityError: continue
    c.commit(); c.close()
    return {"batch_id": batch_id, "duration_key": duration_key, "label": info["label"],
            "seconds": info["seconds"], "codes": codes, "count": len(codes)}

def activate_license(code, device_id):
    code = (code or "").strip().upper()
    device_id = (device_id or "").strip()
    if not code or not device_id or len(device_id) < 8:
        return {"ok": False, "error": "Code ou device_id manquant"}
    c = get_conn()
    row = c.execute("SELECT * FROM licenses WHERE code=?", (code,)).fetchone()
    if not row:
        c.close(); return {"ok": False, "error": "Code invalide"}
    now = datetime.utcnow(); now_iso = now.isoformat()
    if row["status"] == "active" and row["device_id"] and row["device_id"] != device_id:
        c.close(); return {"ok": False, "error": "Mot de passe deja utilise sur un autre appareil"}
    if row["status"] == "active" and row["device_id"] == device_id:
        expires = datetime.fromisoformat(row["expires_at"])
        if expires <= now:
            c.execute("UPDATE licenses SET status='expired' WHERE id=?", (row["id"],))
            c.commit(); c.close()
            return {"ok": False, "error": "Licence expiree"}
        c.execute("UPDATE licenses SET last_seen=? WHERE id=?", (now_iso, row["id"]))
        c.commit(); remaining = int((expires-now).total_seconds()); c.close()
        return {"ok": True, "status": "active", "code": code, "expires_at": row["expires_at"],
                "remaining_seconds": remaining, "device_id": device_id, "reactivated": True}
    if row["status"] in ("expired", "revoked"):
        c.close(); return {"ok": False, "error": "Licence "+row["status"]}
    batch = c.execute("SELECT * FROM license_batches WHERE id=?", (row["batch_id"],)).fetchone()
    if not batch:
        c.close(); return {"ok": False, "error": "Lot introuvable"}
    expires = now + timedelta(seconds=batch["duration_seconds"])
    expires_iso = expires.isoformat()
    c.execute("UPDATE licenses SET status='active',device_id=?,activated_at=?,expires_at=?,last_seen=? WHERE id=? AND status='unused'",
              (device_id, now_iso, expires_iso, now_iso, row["id"]))
    if c.total_changes == 0:
        c.close(); return {"ok": False, "error": "Activation concurrente"}
    c.commit(); c.close()
    return {"ok": True, "status": "active", "code": code, "expires_at": expires_iso,
            "remaining_seconds": batch["duration_seconds"], "device_id": device_id,
            "label": batch["label"], "first_activation": True}

def check_license(device_id):
    device_id = (device_id or "").strip()
    if not device_id: return {"ok": False, "error": "device_id manquant"}
    c = get_conn()
    row = c.execute("SELECT * FROM licenses WHERE device_id=? AND status='active' ORDER BY activated_at DESC LIMIT 1", (device_id,)).fetchone()
    if not row:
        c.close(); return {"ok": False, "error": "Aucune licence active"}
    now = datetime.utcnow()
    expires = datetime.fromisoformat(row["expires_at"])
    if expires <= now:
        c.execute("UPDATE licenses SET status='expired' WHERE id=?", (row["id"],))
        c.commit(); c.close()
        return {"ok": False, "error": "Licence expiree", "expired": True}
    remaining = int((expires-now).total_seconds())
    c.execute("UPDATE licenses SET last_seen=? WHERE id=?", (now.isoformat(), row["id"]))
    c.commit(); c.close()
    return {"ok": True, "status": "active", "code": row["code"], "expires_at": row["expires_at"],
            "remaining_seconds": remaining, "device_id": device_id}

def list_batches(limit=50):
    c = get_conn()
    rows = c.execute("""SELECT b.*,
        (SELECT COUNT(*) FROM licenses l WHERE l.batch_id=b.id) as total,
        (SELECT COUNT(*) FROM licenses l WHERE l.batch_id=b.id AND l.status='unused') as unused,
        (SELECT COUNT(*) FROM licenses l WHERE l.batch_id=b.id AND l.status='active') as active,
        (SELECT COUNT(*) FROM licenses l WHERE l.batch_id=b.id AND l.status='expired') as expired
        FROM license_batches b ORDER BY b.id DESC LIMIT ?""", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

def get_batch_codes(batch_id):
    c = get_conn()
    batch = c.execute("SELECT * FROM license_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch: c.close(); return None
    codes = c.execute("SELECT code,status,device_id,activated_at,expires_at FROM licenses WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
    c.close()
    return {"batch": dict(batch), "codes": [dict(x) for x in codes]}


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "baccarat.db")
CHANNEL_WEB = "https://t.me/s/statistika_baccara"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
PORT = int(os.environ.get("PORT", 8000))
AUTO_COLLECT_INTERVAL = 90
MIN_SAMPLE, MIN_RATE, DEACTIVATE_RATE, MIN_VALIDATIONS = 12, 28.0, 20.0, 6
# Anti-série / rattrapage
STREAK_COOLDOWN = 2          # après 1 perte, cette strat est en cooldown pendant N prochains picks
MAX_CONSEC_LOSS_SOFT = 2     # à partir de 2 pertes d'affilée → mode prudent (2e choix)
MAX_CONSEC_LOSS_HARD = 3     # à partir de 3 pertes → exclusion forte des strats récentes + note alerte
SUITS = ["H", "D", "S", "C"]
EMOJI = {"H": "♥", "D": "♦", "S": "♠", "C": "♣"}
SUIT_NAME = {"H": "Coeur", "D": "Carreau", "S": "Pique", "C": "Trefle"}
SUIT_MAP = {"♥":"H","♦":"D","♠":"S","♣":"C","♥️":"H","♦️":"D","♠️":"S","♣️":"C"}
COLOR_MAP = {"H":"R","D":"R","S":"B","C":"B"}
RANK_VALUE = {"A":1,"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":0,"T":0,"J":0,"Q":0,"K":0}
# Robust to live prefixes: ⏰ #Nxxx . ▶ 3(...)  or plain #Nxxx . 3(...)
HAND_RE = re.compile(
    r"#N(\d+)\s*\.\s*"
    r"(?:[▶►→>]+\s*)?"          # optional live arrow
    r"(\d+)\s*\(([^)]+)\)\s*"
    r"-\s*"
    r"(\d+)\s*\(([^)]+)\)\s*"
    r"(#T\d+)?\s*"
    r"(#R)?",
    re.I
)
CARD_RE = re.compile(r"(A|10|[2-9JQKT])\s*([♥♦♠♣]|♥️|♦️|♠️|♣️)", re.I)

def parse_cards(s):
    cards=[]
    for rank, sc in CARD_RE.findall(s):
        rank=rank.upper()
        if rank=="T": rank="10"
        suit=SUIT_MAP.get(sc)
        if not suit: continue
        cards.append({"rank":rank,"suit":suit,"color":COLOR_MAP[suit],"value":RANK_VALUE.get(rank,0)})
    return cards

def parse_message(text, msg_id=None):
    text = text.strip()
    # Strip common leading noise (clock emoji, etc.) so #N is easier to find
    text_clean = re.sub(r"^[^\w#]*", "", text)
    m = HAND_RE.search(text_clean) or HAND_RE.search(text)
    if not m: return None
    n,ps,pc,bs,bc,t_tag,r_tag=m.groups()
    p,b=parse_cards(pc),parse_cards(bc)
    if not p and not b: return None
    # Live incompleteness: arrow ▶ means Player is still drawing a 3rd card
    has_live_arrow = bool(re.search(r"[▶►→>]", text))
    n_p, n_b = len(p), len(b)
    # Player hand is COMPLETE when:
    #  - final post with #T tag, OR
    #  - player already has 3 cards, OR
    #  - no live arrow and we have at least 2 player cards (natural 2-card stand / finished)
    # INCOMPLETE when: live arrow present (P still drawing) and player has only 2 cards
    player_complete = False
    if n_p >= 3:
        player_complete = True
    elif t_tag:
        player_complete = True  # message finalisé
    elif not has_live_arrow and n_p >= 2:
        player_complete = True  # format 2-x sans ▶ → main terminée
    else:
        player_complete = False  # ▶ présent + seulement 2 cartes P → attendre 3e carte
    return {
        "n": int(n), "player_score": int(ps), "banker_score": int(bs),
        "player_cards": p, "banker_cards": b,
        "t_tag": t_tag, "is_r": bool(r_tag), "message_id": msg_id, "raw": text.strip(),
        "format": f"{n_p}-{n_b}",
        "is_33": n_p == 3 and n_b == 3,
        "is_22": n_p == 2 and n_b == 2,
        "player_drew_3": n_p == 3,
        "has_live_arrow": has_live_arrow,
        "player_complete": player_complete,
        "is_incomplete": not player_complete,
    }

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c=sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    c.row_factory=sqlite3.Row
    try:
        c.execute("PRAGMA busy_timeout=60000")
    except Exception:
        pass
    return c

def init_db():
    c=get_conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS hands (
        id INTEGER PRIMARY KEY AUTOINCREMENT, n INTEGER UNIQUE NOT NULL,
        player_score INTEGER, banker_score INTEGER, player_suits TEXT, banker_suits TEXT,
        player_first_suit TEXT, banker_first_suit TEXT, player_first_color TEXT, banker_first_color TEXT,
        player_first_val INTEGER, banker_first_val INTEGER,
        player_red INTEGER DEFAULT 0, player_black INTEGER DEFAULT 0,
        banker_red INTEGER DEFAULT 0, banker_black INTEGER DEFAULT 0,
        t_tag TEXT, is_r INTEGER DEFAULT 0, player_card_count INTEGER, banker_card_count INTEGER,
        format TEXT, is_33 INTEGER DEFAULT 0, is_22 INTEGER DEFAULT 0, player_drew_3 INTEGER DEFAULT 0,
        message_id INTEGER, collected_at TEXT, source TEXT DEFAULT 'web');
    CREATE INDEX IF NOT EXISTS idx_n ON hands(n);
    CREATE INDEX IF NOT EXISTS idx_pfs ON hands(player_first_suit);
    CREATE TABLE IF NOT EXISTS collection_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, hands_found INTEGER, hands_new INTEGER, status TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, target_n INTEGER UNIQUE NOT NULL, created_at TEXT NOT NULL,
        prediction_suit TEXT NOT NULL, strategy TEXT, strategy_id INTEGER,
        confidence REAL DEFAULT 0, hit_rate REAL DEFAULT 0, margin REAL DEFAULT 0,
        basis_n INTEGER, basis_first_suit TEXT, status TEXT DEFAULT 'PENDING',
        actual_first_suit TEXT, validated_at TEXT, note TEXT);
    CREATE INDEX IF NOT EXISTS idx_pred_target ON predictions(target_n);
    CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, description TEXT,
        side TEXT NOT NULL, from_suit TEXT NOT NULL, to_suit TEXT NOT NULL,
        sample_size INTEGER DEFAULT 0, hist_rate REAL DEFAULT 0,
        real_hits INTEGER DEFAULT 0, real_total INTEGER DEFAULT 0, real_rate REAL DEFAULT 0,
        confidence REAL DEFAULT 0, is_active INTEGER DEFAULT 1, deactivated_reason TEXT,
        created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS pattern_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pattern TEXT UNIQUE NOT NULL,
        occurrences INTEGER DEFAULT 0, last_seen_n INTEGER, updated_at TEXT NOT NULL, context TEXT);
    """)
    try:
        cols={r[1] for r in c.execute("PRAGMA table_info(hands)")}
        for col,typ in [
            ("format","TEXT"),("is_33","INTEGER DEFAULT 0"),("is_22","INTEGER DEFAULT 0"),
            ("player_drew_3","INTEGER DEFAULT 0"),("player_complete","INTEGER DEFAULT 1"),
            ("is_incomplete","INTEGER DEFAULT 0"),
        ]:
            if col not in cols: c.execute(f"ALTER TABLE hands ADD COLUMN {col} {typ}")
        pcols={r[1] for r in c.execute("PRAGMA table_info(predictions)")}
        if "strategy_id" not in pcols: c.execute("ALTER TABLE predictions ADD COLUMN strategy_id INTEGER")
        if "note" not in pcols: c.execute("ALTER TABLE predictions ADD COLUMN note TEXT")
    except Exception: pass
    c.commit(); c.close()

def upsert_hands(parsed_list):
    """
    Insert nouvelles mains. Si #N existe déjà mais la nouvelle version a PLUS de cartes
    joueur (passage 2→3 après tirage), on MET À JOUR (main complète).
    """
    c=get_conn()
    existing={r[0]: r[1] for r in c.execute("SELECT n, player_card_count FROM hands")}
    new=0
    for h in parsed_list:
        p,b=h["player_cards"],h["banker_cards"]
        n_p,n_b=len(p),len(b)
        p_suits=",".join(x["suit"] for x in p)
        b_suits=",".join(x["suit"] for x in b)
        p_complete=1 if h.get("player_complete", True) else 0
        is_inc=0 if p_complete else 1
        core=(
            h["player_score"],h["banker_score"],p_suits,b_suits,
            p[0]["suit"] if p else None,b[0]["suit"] if b else None,
            p[0]["color"] if p else None,b[0]["color"] if b else None,
            p[0]["value"] if p else None,b[0]["value"] if b else None,
            sum(1 for x in p if x["color"]=="R"),sum(1 for x in p if x["color"]=="B"),
            sum(1 for x in b if x["color"]=="R"),sum(1 for x in b if x["color"]=="B"),
            h["t_tag"],1 if h["is_r"] else 0,n_p,n_b,
            h.get("format"),1 if h.get("is_33") else 0,1 if h.get("is_22") else 0,1 if h.get("player_drew_3") else 0,
            p_complete,is_inc,
            h.get("message_id"),datetime.utcnow().isoformat(),"web"
        )
        if h["n"] in existing:
            old_count=existing[h["n"]] or 0
            # Update if more player cards (2→3) OR final tag appears OR becoming complete
            if (n_p > old_count or h.get("t_tag") or p_complete or
                    (n_p == 2 and not p_complete and h.get("has_live_arrow"))):
                c.execute("""UPDATE hands SET
                    player_score=?,banker_score=?,player_suits=?,banker_suits=?,
                    player_first_suit=?,banker_first_suit=?,player_first_color=?,banker_first_color=?,
                    player_first_val=?,banker_first_val=?,player_red=?,player_black=?,banker_red=?,banker_black=?,
                    t_tag=?,is_r=?,player_card_count=?,banker_card_count=?,format=?,is_33=?,is_22=?,player_drew_3=?,
                    player_complete=?,is_incomplete=?,
                    message_id=?,collected_at=?,source=? WHERE n=?""",
                    core+(h["n"],))
            continue
        c.execute("""INSERT INTO hands (n,player_score,banker_score,player_suits,banker_suits,
            player_first_suit,banker_first_suit,player_first_color,banker_first_color,
            player_first_val,banker_first_val,player_red,player_black,banker_red,banker_black,
            t_tag,is_r,player_card_count,banker_card_count,format,is_33,is_22,player_drew_3,
            player_complete,is_incomplete,
            message_id,collected_at,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (h["n"],)+core)
        new+=1
    c.commit(); c.close()
    return new

def get_stats():
    c=get_conn()
    t=c.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
    o=c.execute("SELECT MIN(n) FROM hands").fetchone()[0]
    l=c.execute("SELECT MAX(n) FROM hands").fetchone()[0]
    sa=c.execute("SELECT COUNT(*) FROM strategies WHERE is_active=1").fetchone()[0]
    st=c.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    c.close()
    return {"total_hands":t,"oldest_n":o,"latest_n":l,"strategies_active":sa,"strategies_total":st}

def get_all_hands():
    c=get_conn(); rows=c.execute("SELECT * FROM hands ORDER BY n ASC").fetchall(); c.close()
    return [dict(r) for r in rows]

def get_recent_hands(limit=40):
    c=get_conn()
    rows=c.execute("SELECT n,player_suits,banker_suits,player_first_suit,banker_first_suit,format,is_33,is_22,player_card_count,banker_card_count FROM hands ORDER BY n DESC LIMIT ?",(limit,)).fetchall()
    c.close(); return [dict(r) for r in rows]

def rebuild_strategies(hands):
    if len(hands)<10: return 0
    def transitions(side):
        key="player_first_suit" if side=="player" else "banker_first_suit"
        counts=defaultdict(lambda:defaultdict(int))
        for i in range(len(hands)-1):
            s1,s2=hands[i].get(key),hands[i+1].get(key)
            if s1 and s2: counts[s1][s2]+=1
        return counts
    now=datetime.utcnow().isoformat(); c=get_conn(); created=0
    for side in ("player","banker"):
        counts=transitions(side)
        for fs in SUITS:
            total=sum(counts[fs].values())
            if total<MIN_SAMPLE: continue
            for ts in SUITS:
                cnt=counts[fs][ts]; rate=100.0*cnt/total
                if rate<MIN_RATE: continue
                name=f"Trans_{side[0].upper()}_{fs}_to_{ts}"
                conf=round(min(rate/50.0,1.0)*min(total,150)/150.0,3)
                desc=f"Si 1ere {side}={EMOJI[fs]} alors ~{rate:.1f}% {EMOJI[ts]} (n={total})"
                row=c.execute("SELECT id FROM strategies WHERE name=?",(name,)).fetchone()
                if row:
                    c.execute("UPDATE strategies SET description=?,sample_size=?,hist_rate=?,confidence=?,updated_at=? WHERE id=?",
                              (desc,total,round(rate,2),conf,now,row["id"]))
                else:
                    c.execute("""INSERT INTO strategies (name,description,side,from_suit,to_suit,sample_size,hist_rate,confidence,is_active,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,1,?,?)""",(name,desc,side,fs,ts,total,round(rate,2),conf,now,now))
                    created+=1
    c.commit(); c.close(); return created

def score_strategies_from_validations():
    c=get_conn()
    rows=c.execute("SELECT strategy,status FROM predictions WHERE status IN ('VALID','INVALID') AND strategy IS NOT NULL").fetchall()
    stats=defaultdict(lambda:{"hits":0,"total":0})
    for r in rows:
        stats[r["strategy"]]["total"]+=1
        if r["status"]=="VALID": stats[r["strategy"]]["hits"]+=1
    now=datetime.utcnow().isoformat()
    for name,st in stats.items():
        rate=round(100.0*st["hits"]/st["total"],2) if st["total"] else 0
        c.execute("UPDATE strategies SET real_hits=?,real_total=?,real_rate=?,updated_at=? WHERE name=?",
                  (st["hits"],st["total"],rate,now,name))
    c.commit(); c.close()

def prune_bad_strategies():
    c=get_conn()
    rows=c.execute("SELECT id,name,real_rate,real_total FROM strategies WHERE is_active=1 AND real_total>=?",(MIN_VALIDATIONS,)).fetchall()
    deactivated=[]; now=datetime.utcnow().isoformat()
    for r in rows:
        if r["real_rate"]<DEACTIVATE_RATE:
            reason=f"real_rate={r['real_rate']}% < {DEACTIVATE_RATE}% sur {r['real_total']} val"
            c.execute("UPDATE strategies SET is_active=0,deactivated_reason=?,updated_at=? WHERE id=?",(reason,now,r["id"]))
            deactivated.append({"name":r["name"],"reason":reason})
    # Coupe rapide : 3 pertes d'affilée sur la même strat (même si sample encore petit)
    hist=c.execute("""SELECT strategy,status FROM predictions
        WHERE status IN ('VALID','INVALID') AND strategy IS NOT NULL
        ORDER BY target_n DESC LIMIT 40""").fetchall()
    by_strat=defaultdict(list)
    for h in hist:
        by_strat[h["strategy"]].append(h["status"])
    for name, statuses in by_strat.items():
        # 3 INVALID consécutifs en tête
        if len(statuses)>=3 and all(s=="INVALID" for s in statuses[:3]):
            row=c.execute("SELECT id,is_active FROM strategies WHERE name=?",(name,)).fetchone()
            if row and row["is_active"]:
                reason=f"3 pertes d'affilée récentes — coupe temporaire"
                c.execute("UPDATE strategies SET is_active=0,deactivated_reason=?,updated_at=? WHERE id=?",
                          (reason,now,row["id"]))
                deactivated.append({"name":name,"reason":reason})
    c.commit(); c.close(); return deactivated

def get_active_strategies(limit=25):
    c=get_conn()
    rows=c.execute("""SELECT * FROM strategies WHERE is_active=1
        ORDER BY CASE WHEN real_total>=? THEN real_rate ELSE hist_rate END DESC, confidence DESC, sample_size DESC LIMIT ?""",
        (MIN_VALIDATIONS,limit)).fetchall()
    c.close(); return [dict(r) for r in rows]

def get_all_strategies(limit=50):
    c=get_conn()
    rows=c.execute("""SELECT * FROM strategies ORDER BY is_active DESC,
        CASE WHEN real_total>=? THEN real_rate ELSE hist_rate END DESC LIMIT ?""",(MIN_VALIDATIONS,limit)).fetchall()
    c.close(); return [dict(r) for r in rows]

def get_recent_pred_outcomes(limit=12):
    """Retourne l'historique récent des prédictions validées (du plus récent au plus ancien)."""
    c=get_conn()
    rows=c.execute("""SELECT target_n, prediction_suit, strategy, status
        FROM predictions WHERE status IN ('VALID','INVALID')
        ORDER BY target_n DESC LIMIT ?""",(limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

def consecutive_losses(hist):
    """Nombre de pertes d'affilée en tête d'historique (VALID/INVALID)."""
    n=0
    for h in hist:
        if h["status"]=="INVALID": n+=1
        else: break
    return n

def strategies_on_cooldown(hist, cooldown=STREAK_COOLDOWN):
    """
    Stratégies qui ont perdu récemment → cooldown.
    Une strat qui vient de perdre est exclue pendant `cooldown` picks suivants.
    """
    banned=set()
    # Dernière utilisation de chaque strat dans l'historique
    seen_since_loss={}
    for i,h in enumerate(hist):
        name=h.get("strategy")
        if not name: continue
        if name in seen_since_loss: continue
        if h["status"]=="INVALID":
            # Perte → cooldown si on est encore dans la fenêtre
            if i < cooldown:
                banned.add(name)
            seen_since_loss[name]=i
        else:
            seen_since_loss[name]=i
    return banned


def invert_suit(suit, rate):
    """Inversion selon le taux de la strat."""
    if rate is None:
        return suit, None
    r = float(rate)
    if 31.5 <= r <= 40.0:
        m = {"H": "C", "C": "H", "S": "D", "D": "S"}
        return m.get(suit, suit), "INV 31.5-40 ♥↔♣ / ♠↔♦"
    if 21.5 <= r <= 30.4:
        m = {"C": "S", "S": "C", "H": "D", "D": "H"}
        return m.get(suit, suit), "INV 21.5-30.4 ♣↔♠ / ♥↔♦"
    return suit, None

def all_check_prev(suit, latest):
    """
    Confirmation finale universelle (AllCheckPrev / CtrlTour).
    S'applique après toutes les autres inversions, sur TOUTE prédiction
    (victoire ou défaite, formats 2-2 / 2-3 / 3-3), en se basant
    uniquement sur la 1ère carte JOUEUR du jeu précédent (données Telegram).
    """
    if not latest or not suit:
        return suit, None
    prev_suit = latest.get("player_first_suit")
    prev_val = latest.get("player_first_val")
    if not prev_suit or prev_val is None:
        return suit, None
    prev_color = COLOR_MAP.get(prev_suit)
    pred_color = COLOR_MAP.get(suit)
    if not prev_color or not pred_color:
        return suit, None
    try:
        is_even = (int(prev_val) % 2 == 0)
    except (TypeError, ValueError):
        return suit, None
    is_same_suit = (suit == prev_suit)
    is_same_color = (pred_color == prev_color)

    # 1) Enseigne prédite identique à la 1ère carte joueur du jeu précédent
    if is_same_suit:
        m = {"S": "C", "C": "S", "H": "D", "D": "H"}
        new_suit = m.get(suit, suit)
        return new_suit, "AllCheckPrev · CtrlTour · INV same-suit ♠↔♣ / ♥↔♦"

    # 2) Couleurs différentes + valeur paire du P1 précédent
    if (not is_same_color) and is_even:
        m = {"D": "C", "C": "D", "H": "S", "S": "H"}
        new_suit = m.get(suit, suit)
        return new_suit, "AllCheckPrev · CtrlTour · INV diff-color+even ♦↔♣ / ♥↔♠"

    # 3) Couleurs identiques + valeur impaire du P1 précédent
    if is_same_color and (not is_even):
        m = {"S": "H", "H": "S", "D": "C", "C": "D"}
        new_suit = m.get(suit, suit)
        return new_suit, "AllCheckPrev · CtrlTour · INV same-color+odd ♠↔♥ / ♦↔♣"

    return suit, None

def pick_prediction(hands, strategies):
    if not hands: return None, None, "Aucune main"
    latest=hands[-1]
    hist=get_recent_pred_outcomes(12)
    consec=consecutive_losses(hist)
    banned=strategies_on_cooldown(hist, STREAK_COOLDOWN)

    # Mode rattrapage
    recovery_soft = consec >= MAX_CONSEC_LOSS_SOFT
    recovery_hard = consec >= MAX_CONSEC_LOSS_HARD

    if not strategies:
        pf=Counter(h["player_first_suit"] for h in hands if h.get("player_first_suit"))
        if not pf: return None, latest, "Pas de data"
        best_s=pf.most_common(1)[0][0]; total=sum(pf.values()); rate=round(100*pf[best_s]/total,2)
        note="Fallback frequence"
        if recovery_hard: note="⚠ RATTRAPAGE (3+ pertes) · "+note
        elif recovery_soft: note="Rattrapage (2 pertes) · "+note
        final_suit, inv_note = invert_suit(best_s, rate)
        if inv_note:
            note = (note + " · " if note else "") + inv_note + f" → {EMOJI[final_suit]}"
        # Confirmation finale universelle AllCheckPrev / CtrlTour
        final_suit, check_note = all_check_prev(final_suit, latest)
        if check_note:
            note = (note + " · " if note else "") + check_note + f" → {EMOJI[final_suit]}"
        pred={"suit":final_suit,"symbol":EMOJI[final_suit],"hit_rate":rate,"confidence":0.12,"sample":total,
              "margin":round(rate-25,2),"strategy":"FREQ_PLAYER","strategy_id":None,"note":note,
              "raw_suit":best_s,"inverted":bool(inv_note or check_note)}
        return pred, latest, pred["note"]

    applicable=[s for s in strategies if s["side"]=="player" and s["from_suit"]==latest.get("player_first_suit")]
    if not applicable:
        applicable=[s for s in strategies if s["side"]=="banker" and s["from_suit"]==latest.get("banker_first_suit")]

    # Filtrer les stratégies en cooldown (sauf si aucune autre option)
    free=[s for s in applicable if s["name"] not in banned]
    if free:
        applicable=free
    elif applicable and recovery_hard:
        # En mode hard on préfère vraiment éviter les strats qui viennent de perdre
        pass  # on garde applicable mais on notera

    if not applicable:
        pf=Counter(h["player_first_suit"] for h in hands if h.get("player_first_suit"))
        if pf:
            best_s=pf.most_common(1)[0][0]; total=sum(pf.values()); rate=round(100*pf[best_s]/total,2)
            note=f"Aucune transition pour {latest.get('player_first_suit')}"
            if recovery_hard: note="⚠ RATTRAPAGE · "+note
            final_suit, inv_note = invert_suit(best_s, rate)
            if inv_note:
                note = (note + " · " if note else "") + inv_note + f" → {EMOJI[final_suit]}"
            # Confirmation finale universelle AllCheckPrev / CtrlTour
            final_suit, check_note = all_check_prev(final_suit, latest)
            if check_note:
                note = (note + " · " if note else "") + check_note + f" → {EMOJI[final_suit]}"
            pred={"suit":final_suit,"symbol":EMOJI[final_suit],"hit_rate":rate,"confidence":0.1,"sample":total,
                  "margin":round(rate-25,2),"strategy":"FREQ_PLAYER","strategy_id":None,"note":note,
                  "raw_suit":best_s,"inverted":bool(inv_note or check_note)}
            return pred, latest, pred["note"]
        return None, latest, "Rien d'applicable"

    def score(s):
        rate=s["real_rate"] if s["real_total"]>=MIN_VALIDATIONS else s["hist_rate"]
        # Légère pénalité si la strat est encore dans banned (cas de secours)
        pen = -5 if s["name"] in banned else 0
        return (rate + pen, s["confidence"], s["sample_size"], s["real_total"])

    ranked=sorted(applicable, key=score, reverse=True)
    best=ranked[0]

    # Mode soft rattrapage : si 2+ pertes d'affilée et qu'il existe un 2e choix décent, on le prend
    if recovery_soft and len(ranked)>=2:
        second=ranked[1]
        r1=best["real_rate"] if best["real_total"]>=MIN_VALIDATIONS else best["hist_rate"]
        r2=second["real_rate"] if second["real_total"]>=MIN_VALIDATIONS else second["hist_rate"]
        # On bascule si le 2e n'est pas trop loin (max 8 pts d'écart) → diversification
        if r1 - r2 <= 8.0:
            best=second

    # Mode hard : après 3 pertes, on force la diversification si possible
    if recovery_hard and len(ranked)>=2 and ranked[0]["name"]==best["name"]:
        # Évite la strat qui était #1 si elle a participé aux pertes récentes
        recent_losers={h["strategy"] for h in hist[:MAX_CONSEC_LOSS_HARD] if h["status"]=="INVALID"}
        for cand in ranked:
            if cand["name"] not in recent_losers:
                best=cand
                break

    rate=best["real_rate"] if best["real_total"]>=MIN_VALIDATIONS else best["hist_rate"]
    note=None
    if recovery_hard:
        note=f"⚠ RATTRAPAGE ({consec} pertes d'affilée) — strat alternative"
    elif recovery_soft:
        note=f"Rattrapage ({consec} pertes) — diversification"
    elif best["name"] in banned:
        note="Strat en cooldown évitée (perte récente)"
    if latest.get("is_33"):
        note=(note+" · " if note else "")+"⚠ 3-3 precedent: vigilance"
    elif latest.get("player_drew_3"):
        note=(note+" · " if note else "")+"Joueur 3 cartes precedemment"
    diag=(f"Score REEL {best['real_rate']}% ({best['real_hits']}/{best['real_total']})"
          if best["real_total"]>=MIN_VALIDATIONS else f"Score HISTO {best['hist_rate']}% (n={best['sample_size']})")
    note=f"{note} · {diag}" if note else diag
    final_suit, inv_note = invert_suit(best["to_suit"], rate)
    if inv_note:
        note = (note + " · " if note else "") + inv_note + f" → {EMOJI[final_suit]}"
    # Confirmation finale universelle AllCheckPrev / CtrlTour
    final_suit, check_note = all_check_prev(final_suit, latest)
    if check_note:
        note = (note + " · " if note else "") + check_note + f" → {EMOJI[final_suit]}"
    pred={"suit":final_suit,"symbol":EMOJI[final_suit],"hit_rate":rate,"confidence":best["confidence"],
          "sample":best["sample_size"],"margin":round(rate-25,2),"strategy":best["name"],"strategy_id":best["id"],"note":note,
          "raw_suit":best["to_suit"],"inverted":bool(inv_note or check_note)}
    return pred, latest, note

def upsert_prediction(target_n, prediction, basis_n=None, basis_first_suit=None):
    if not prediction: return
    try:
        c=get_conn()
        c.execute("""INSERT INTO predictions (target_n,created_at,prediction_suit,strategy,strategy_id,confidence,hit_rate,margin,basis_n,basis_first_suit,note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(target_n) DO UPDATE SET
            prediction_suit=excluded.prediction_suit,strategy=excluded.strategy,strategy_id=excluded.strategy_id,
            confidence=excluded.confidence,hit_rate=excluded.hit_rate,margin=excluded.margin,
            basis_n=excluded.basis_n,basis_first_suit=excluded.basis_first_suit,note=excluded.note,created_at=excluded.created_at""",
            (target_n,datetime.utcnow().isoformat(),prediction["suit"],prediction.get("strategy"),prediction.get("strategy_id"),
             prediction.get("confidence",0),prediction.get("hit_rate",0),prediction.get("margin",0),basis_n,basis_first_suit,prediction.get("note")))
        c.commit(); c.close()
    except Exception as e:
        print("upsert_prediction error:", e)

def validate_predictions(recheck_all=False):
    """
    Gagnant si l'enseigne predite apparait PARMI les cartes JOUEUR (2 ou 3),
    pas seulement en premiere position.
    """
    c=get_conn()
    if recheck_all:
        rows=c.execute("""SELECT p.id,p.prediction_suit,h.player_first_suit,h.player_suits,
                                  h.player_complete,h.player_card_count,h.format
            FROM predictions p JOIN hands h ON h.n=p.target_n
            WHERE p.status IN ('PENDING','VALID','INVALID')""").fetchall()
    else:
        rows=c.execute("""SELECT p.id,p.prediction_suit,h.player_first_suit,h.player_suits,
                                  h.player_complete,h.player_card_count,h.format
            FROM predictions p JOIN hands h ON h.n=p.target_n
            WHERE p.status='PENDING'""").fetchall()
    n=0
    for r in rows:
        # Si le joueur est encore en phase de tirage (▶ + 2 cartes),
        # aucun verdict ne doit être rendu avant la 3e carte.
        n_p = r["player_card_count"] or 0
        player_incomplete = (r["player_complete"] == 0)
        if player_incomplete:
            # Répare aussi un éventuel ancien INVALID lors d'un recheck manuel.
            if recheck_all:
                c.execute(
                    "UPDATE predictions SET status='PENDING',actual_first_suit=NULL,validated_at=NULL,note=? WHERE id=?",
                    ("⏳ Attente de la 3e carte JOUEUR avant verdict", r["id"])
                )
            continue

        suits=(r["player_suits"] or "").split(",")
        suits=[s.strip() for s in suits if s.strip()]
        hit = r["prediction_suit"] in suits  # n'importe quelle carte joueur
        st="VALID" if hit else "INVALID"
        # actual = toutes les enseignes joueur (pour l'historique)
        actual = r["player_suits"] or r["player_first_suit"]
        c.execute("UPDATE predictions SET status=?,actual_first_suit=?,validated_at=? WHERE id=?",
                  (st, actual, datetime.utcnow().isoformat(), r["id"]))
        n+=1
    c.commit(); c.close(); return n

def get_prediction_history(limit=200):
    c=get_conn(); rows=c.execute("SELECT * FROM predictions ORDER BY target_n DESC LIMIT ?",(int(limit),)).fetchall(); c.close()
    return [dict(r) for r in rows]

def record_patterns(hands):
    if len(hands)<3: return
    c=get_conn(); now=datetime.utcnow().isoformat()
    for i in range(2,len(hands)):
        vals=[hands[j].get("player_first_suit") for j in (i-2,i-1,i)]
        if not all(vals): continue
        pattern="P:"+">".join(vals)
        c.execute("""INSERT INTO pattern_observations(pattern,occurrences,last_seen_n,updated_at,context) VALUES(?,?,?,?,?)
            ON CONFLICT(pattern) DO UPDATE SET occurrences=occurrences+1,last_seen_n=excluded.last_seen_n,updated_at=excluded.updated_at""",
            (pattern,1,hands[i]["n"],now,hands[i].get("format") or ""))
        if hands[i-1].get("is_33"):
            p33="AFTER33:"+(hands[i].get("player_first_suit") or "?")
            c.execute("""INSERT INTO pattern_observations(pattern,occurrences,last_seen_n,updated_at,context) VALUES(?,?,?,?,?)
                ON CONFLICT(pattern) DO UPDATE SET occurrences=occurrences+1,last_seen_n=excluded.last_seen_n,updated_at=excluded.updated_at""",
                (p33,1,hands[i]["n"],now,"post-33"))
    c.commit(); c.close()

def get_patterns(limit=20):
    c=get_conn()
    rows=c.execute("SELECT pattern,occurrences,last_seen_n,context FROM pattern_observations ORDER BY occurrences DESC,last_seen_n DESC LIMIT ?",(int(limit),)).fetchall()
    c.close(); return [dict(r) for r in rows]

def run_learning_cycle(recheck=False):
    hands=get_all_hands()
    validated=0; created=0; deactivated=[]; strategies=[]; pred=None; latest=None; diag="—"
    try: validated=validate_predictions(recheck_all=recheck)
    except Exception as e: print("validate err", e)
    try: score_strategies_from_validations()
    except Exception as e: print("score err", e)
    try: deactivated=prune_bad_strategies()
    except Exception as e: print("prune err", e)
    try: created=rebuild_strategies(hands)
    except Exception as e: print("rebuild err", e)
    try: record_patterns(hands)
    except Exception as e: print("patterns err", e)
    try: strategies=get_active_strategies()
    except Exception as e: print("active err", e)
    player_ready=False
    try:
        latest=hands[-1] if hands else None
        # NE PREDIRE QUE si la main JOUEUR du dernier jeu est COMPLETE
        # Règle : ▶ live + seulement 2 cartes P → attendre la 3e carte
        # Prêt si: player_complete=1 en DB, ou 3 cartes P, ou #T présent
        if latest:
            n_p = latest.get("player_card_count") or 0
            pc = latest.get("player_complete")
            if pc is not None:
                player_ready = bool(pc)
            elif n_p >= 3 or latest.get("t_tag"):
                player_ready = True
            elif n_p >= 2:
                # Anciennes lignes sans le flag : considérer prêt
                player_ready = True
            else:
                player_ready = False
        if latest and not player_ready:
            pred=None
            diag=f"⏳ Attente 3e carte JOUEUR #N{latest['n']} (▶ encore actif) — pred #N{latest['n']+1} en pause"
        else:
            pred,latest,diag=pick_prediction(hands,strategies)
            if pred and latest:
                upsert_prediction(latest["n"]+1,pred,latest["n"],latest.get("player_first_suit"))
    except Exception as e:
        print("pick err", e); diag=str(e)
    return {"validated":validated,"strategies_created":created,"deactivated":deactivated,
            "active_count":len(strategies),"prediction":pred,"latest_n":latest["n"] if latest else None,
            "diagnosis":diag,"player_ready":player_ready}

def fetch_page(before=None):
    url=CHANNEL_WEB+(f"?before={before}" if before else "")
    r=requests.get(url,headers=HEADERS,timeout=15); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser"); messages,ids=[],[]
    for w in soup.select(".tgme_widget_message"):
        post=w.get("data-post",""); mid=None
        if post and "/" in post:
            try: mid=int(post.split("/")[-1]); ids.append(mid)
            except ValueError: pass
        te=w.select_one(".tgme_widget_message_text")
        if te:
            text=te.get_text(separator=" ",strip=True)
            # Accept any message containing #N (live posts can have leading emojis)
            if text and "#N" in text:
                messages.append({"id":mid,"text":text})
    return messages,(min(ids) if ids else None)

def collect(pages=10, delay=0.4):
    all_parsed,seen,before=[],set(),None
    for i in range(pages):
        try: raw,min_id=fetch_page(before)
        except Exception as e:
            print(f"Erreur page {i+1}: {e}"); break
        if not raw:
            print(f" Page {i+1}/{pages} vide — stop")
            break
        for msg in raw:
            p=parse_message(msg["text"],msg["id"])
            if p and p["n"] not in seen:
                seen.add(p["n"])
                all_parsed.append(p)
        before=min_id
        print(f" Page {i+1}/{pages} +{len(raw)} msgs uniques={len(all_parsed)} (max N={max((x['n'] for x in all_parsed), default=0)})")
        if i<pages-1: time.sleep(delay)
    all_parsed.sort(key=lambda x:x["n"]); return all_parsed

def analyze_report(hands):
    if not hands: return {"error":"Aucune donnee"}
    p_first=Counter(h["player_first_suit"] for h in hands if h.get("player_first_suit"))
    def pct(c):
        t=sum(c.values()) or 1
        return {s:round(100*c[s]/t,2) for s in SUITS}
    strats=get_active_strategies(15)
    return {"n_hands":len(hands),"n_range":{"min":hands[0]["n"],"max":hands[-1]["n"]},
            "player_first":pct(p_first),"strategies":strats,"strategies_count":len(strats)}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")
    def send_json(self, data, code=200):
        body=json.dumps(data,ensure_ascii=False,indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Content-Length",len(body)); self.end_headers(); self.wfile.write(body)
    def send_html(self, html):
        body=html.encode("utf-8"); self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",len(body)); self.end_headers(); self.wfile.write(body)
    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0: return {}
        try: return json.loads(self.rfile.read(length).decode("utf-8"))
        except: return {}
    def _device_id(self):
        did = self.headers.get("X-Device-Id") or ""
        if not did:
            qs = parse_qs(urlparse(self.path).query)
            did = (qs.get("device_id") or [""])[0]
        return did
    def _require_license(self):
        res = check_license(self._device_id())
        return res.get("ok", False), res
    def do_GET(self):
        path=urlparse(self.path).path; qs=parse_qs(urlparse(self.path).query)
        # Pages publiques
        if path in ("/","/index.html","/login"):
            self.send_html(LOGIN_PAGE); return
        if path == "/dashboard":
            self.send_html(DASHBOARD); return
        if path == "/admin":
            self.send_html(ADMIN_PAGE); return
        if path == "/api/license/status":
            self.send_json(check_license(self._device_id())); return
        if path == "/api/admin/batches":
            secret = self.headers.get("X-Admin-Secret") or (qs.get("secret") or [""])[0]
            if secret != ADMIN_SECRET: self.send_json({"error":"Admin secret invalide"},403); return
            self.send_json({"batches": list_batches(100)}); return
        if path == "/api/admin/batch":
            secret = self.headers.get("X-Admin-Secret") or (qs.get("secret") or [""])[0]
            if secret != ADMIN_SECRET: self.send_json({"error":"Admin secret invalide"},403); return
            bid = int((qs.get("id") or [0])[0])
            data = get_batch_codes(bid)
            self.send_json(data if data else {"error":"Batch introuvable"}, 200 if data else 404); return
        # Protection API
        if path.startswith("/api/"):
            ok, lic = self._require_license()
            if not ok:
                self.send_json({"error":"Licence requise","detail":lic.get("error","")},403); return
        if path in ("/","/index.html"): self.send_html(DASHBOARD)
        elif path=="/api/stats/overview": self.send_json(get_stats())
        elif path=="/api/analysis/full": self.send_json(analyze_report(get_all_hands()))
        elif path=="/api/strategies": self.send_json({"active":get_active_strategies(30),"all":get_all_strategies(40)})
        elif path=="/api/live":
            cycle=run_learning_cycle(); hands=get_all_hands(); latest=hands[-1] if hands else None
            hist=get_prediction_history(200)
            n_valid=sum(1 for x in hist if x["status"]=="VALID")
            n_invalid=sum(1 for x in hist if x["status"]=="INVALID")
            n_pending=sum(1 for x in hist if x["status"]=="PENDING")
            ready=bool(cycle.get("player_ready"))
            # Ne publier la cible suivante que si P (joueur) est complet
            target_n=(latest["n"]+1) if (latest and ready) else None
            self.send_json({"timestamp":datetime.now().isoformat(timespec="seconds"),"latest":latest,
                "prediction":cycle.get("prediction") if ready else None,
                "prediction_target_n":target_n,
                "player_ready":ready,
                "prediction_history":hist[:100],"patterns":get_patterns(15),"strategies_active":get_active_strategies(10),
                "learning":{"validated_now":cycle.get("validated",0),"deactivated":cycle.get("deactivated",[]),
                    "active_count":cycle.get("active_count",0),"diagnosis":cycle.get("diagnosis"),
                    "player_ready":ready},
                "pred_stats":{"total":len(hist),"valid":n_valid,"invalid":n_invalid,"pending":n_pending},
                "license": check_license(self._device_id())})
        elif path=="/api/predictions":
            validate_predictions(); self.send_json(get_prediction_history(int(qs.get("limit",[200])[0])))
        elif path=="/api/patterns": self.send_json(get_patterns(int(qs.get("limit",[30])[0])))
        elif path=="/api/hands": self.send_json(get_recent_hands(int(qs.get("limit",[40])[0])))
        else: self.send_json({"error":"Not found"},404)
    def do_POST(self):
        path=urlparse(self.path).path; qs=parse_qs(urlparse(self.path).query)
        body = self._read_json()
        if path == "/api/license/activate":
            code = body.get("code") or (qs.get("code") or [""])[0]
            device_id = body.get("device_id") or self._device_id()
            res = activate_license(code, device_id)
            self.send_json(res, 200 if res.get("ok") else 400); return
        if path == "/api/admin/generate":
            secret = self.headers.get("X-Admin-Secret") or body.get("secret") or (qs.get("secret") or [""])[0]
            if secret != ADMIN_SECRET: self.send_json({"error":"Admin secret invalide"},403); return
            try:
                batch = create_batch(body.get("duration") or (qs.get("duration") or [""])[0], body.get("note") or "")
                self.send_json({"status":"ok","batch":batch})
            except Exception as e:
                self.send_json({"status":"error","error":str(e)},400)
            return
        # Protection
        ok, lic = self._require_license()
        if not ok:
            self.send_json({"error":"Licence requise","detail":lic.get("error","")},403); return
        if path=="/api/collect":
            pages=int(qs.get("pages",[8])[0]); print(f"=== Collecte {pages} pages ===")
            try:
                parsed=collect(pages=pages); new=upsert_hands(parsed); cycle=run_learning_cycle()
                c=get_conn(); c.execute("INSERT INTO collection_logs (ts,hands_found,hands_new,status) VALUES (?,?,?,?)",
                    (datetime.utcnow().isoformat(),len(parsed),new,"success")); c.commit(); c.close()
                self.send_json({"status":"ok","hands_found":len(parsed),"hands_new":new,
                    "message":f"{new} nouvelles · apprentissage maj","learning":cycle,"next_prediction":cycle.get("prediction")})
            except Exception as e:
                import traceback; traceback.print_exc()
                self.send_json({"status":"error","error":str(e),"hint":"Telegram inaccessible depuis le serveur?"},500)
        elif path=="/api/learn":
            self.send_json({"status":"ok","learning":run_learning_cycle(recheck=True)})
        else: self.send_json({"error":"Not found"},404)


LOGIN_PAGE = r"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xcode Suit Card — Accès</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Rajdhani:wght@400;600;700&display=swap');
:root{--card:#1a0e12;--gold:#d4a84b;--gold-dim:#a07830;--ivory:#f0e6d0;--muted:#a89878;--ok:#5ecf9a;--bad:#e07080;--border:#3a2028}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(ellipse at 30% 20%,#2a1018 0%,#0c0608 60%);color:var(--ivory);font-family:Rajdhani,system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.box{background:var(--card);border:1px solid var(--border);border-top:3px solid var(--gold);border-radius:10px;padding:32px 28px;max-width:420px;width:100%}
h1{font-family:Cinzel,serif;font-size:1.5rem;color:var(--gold);text-align:center;margin-bottom:6px}
.sub{text-align:center;font-size:.72rem;color:var(--muted);letter-spacing:.12em;text-transform:uppercase;margin-bottom:28px}
label{display:block;font-size:.7rem;color:var(--gold-dim);text-transform:uppercase;margin-bottom:6px}
input{width:100%;padding:14px 16px;border-radius:6px;border:1px solid var(--border);background:#12080c;color:var(--ivory);font-size:1.05rem;letter-spacing:.12em;text-transform:uppercase;outline:none}
input:focus{border-color:var(--gold)}
button{width:100%;margin-top:18px;padding:14px;border-radius:6px;border:2px solid var(--gold);background:linear-gradient(180deg,#8b2240,#5a1228);color:var(--gold);font-family:Cinzel,serif;font-size:.95rem;font-weight:700;cursor:pointer;text-transform:uppercase}
button:disabled{opacity:.5}
.msg{margin-top:14px;padding:10px 12px;border-radius:5px;font-size:.82rem;display:none}
.msg.err{background:#2a1218;border:1px solid #6a3038;color:var(--bad);display:block}
.msg.ok{background:#1a2a18;border:1px solid #2a4a30;color:var(--ok);display:block}
.foot{text-align:center;margin-top:22px;font-size:.65rem;color:#5a4048}
</style></head><body>
<div class="box">
  <h1>XCODE SUIT CARD</h1>
  <div class="sub">Accès par licence · 1 code = 1 appareil</div>
  <label>Code d'accès</label>
  <input id="code" type="text" maxlength="20" placeholder="XXXXXXXXXXXXXX" autocomplete="off">
  <button id="btn" onclick="activate()">ACTIVER LA LICENCE</button>
  <div class="msg" id="msg"></div>
  <div class="foot">1 code = 1 téléphone uniquement</div>
</div>
<script>
function getDeviceId(){
  let id=localStorage.getItem('xcode_device_id');
  if(id&&id.length>=12)return id;
  const raw=[navigator.userAgent||'',screen.width+'x'+screen.height,Math.random().toString(36)].join('|');
  let h=0;for(let i=0;i<raw.length;i++){h=((h<<5)-h)+raw.charCodeAt(i);h|=0;}
  id='DEV'+Math.abs(h).toString(36).toUpperCase()+Date.now().toString(36).toUpperCase().slice(-6);
  localStorage.setItem('xcode_device_id',id);return id;
}
async function activate(){
  const code=(document.getElementById('code').value||'').trim().toUpperCase();
  const msg=document.getElementById('msg'),btn=document.getElementById('btn');
  if(!code||code.length<8){msg.className='msg err';msg.textContent='Code trop court';return;}
  btn.disabled=true;btn.textContent='VÉRIFICATION…';
  try{
    const device_id=getDeviceId();
    const r=await fetch('/api/license/activate',{method:'POST',headers:{'Content-Type':'application/json','X-Device-Id':device_id},body:JSON.stringify({code,device_id})});
    const d=await r.json();
    if(d.ok){msg.className='msg ok';msg.textContent='Licence activée !';setTimeout(()=>location.href='/dashboard',800);}
    else{msg.className='msg err';msg.textContent=d.error||'Échec';btn.disabled=false;btn.textContent='ACTIVER LA LICENCE';}
  }catch(e){msg.className='msg err';msg.textContent='Erreur: '+e.message;btn.disabled=false;btn.textContent='ACTIVER LA LICENCE';}
}
document.getElementById('code').addEventListener('keydown',e=>{if(e.key==='Enter')activate();});
(async()=>{try{const d=await(await fetch('/api/license/status',{headers:{'X-Device-Id':getDeviceId()}})).json();if(d.ok)location.href='/dashboard';}catch(e){}})();
</script></body></html>
"""

ADMIN_PAGE = r"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xcode Admin</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Rajdhani:wght@400;600;700&family=Courier+Prime&display=swap');
body{background:#0c0608;color:#f0e6d0;font-family:Rajdhani,system-ui,sans-serif;padding:24px;max-width:900px;margin:auto}
h1{font-family:Cinzel,serif;color:#d4a84b;font-size:1.4rem;margin-bottom:18px}
.panel{background:#1a0e12;border:1px solid #3a2028;border-radius:8px;padding:18px;margin-bottom:16px}
label{font-size:.7rem;color:#a07830;text-transform:uppercase;display:block;margin-bottom:4px}
input,select{width:100%;padding:10px;border-radius:5px;border:1px solid #3a2028;background:#12080c;color:#f0e6d0;font-size:.95rem;margin-bottom:12px}
button{padding:10px 18px;border-radius:5px;border:1px solid #d4a84b;background:#5a1228;color:#d4a84b;font-family:Cinzel,serif;cursor:pointer;margin-right:8px}
.codes{font-family:'Courier Prime',monospace;font-size:.78rem;background:#12080c;padding:12px;border-radius:5px;max-height:320px;overflow:auto;white-space:pre-wrap;border:1px solid #3a2028}
.batch{border-bottom:1px solid #2a181c;padding:8px 0;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;font-size:.85rem}
.ok{color:#5ecf9a}.bad{color:#e07080}
</style></head><body>
<h1>ADMIN — Génération de lots (40 codes)</h1>
<div class="panel">
  <label>Secret Admin</label><input id="secret" type="password" placeholder="ADMIN_SECRET">
  <label>Durée</label>
  <select id="duration">
    <option value="5m">5 minutes</option><option value="10m">10 minutes</option>
    <option value="20m">20 minutes</option><option value="1d">1 jour</option>
    <option value="2d">2 jours</option><option value="3d">3 jours</option>
    <option value="8d">8 jours</option><option value="31d">31 jours</option>
  </select>
  <label>Note</label><input id="note" type="text" placeholder="Lot client…">
  <button onclick="generate()">GÉNÉRER 40 CODES</button>
  <button onclick="loadBatches()">RAFRAÎCHIR</button>
</div>
<div class="panel"><div id="result" class="codes">Codes ici</div></div>
<div class="panel"><h3 style="color:#d4a84b;font-size:.9rem;margin-bottom:10px">Lots</h3><div id="batches">—</div></div>
<script>
async function generate(){
  const secret=document.getElementById('secret').value,duration=document.getElementById('duration').value,note=document.getElementById('note').value;
  const r=await fetch('/api/admin/generate',{method:'POST',headers:{'Content-Type':'application/json','X-Admin-Secret':secret},body:JSON.stringify({duration,note,secret})});
  const d=await r.json();
  if(d.status==='ok'){
    const b=d.batch; let txt='LOT #'+b.batch_id+' — '+b.label+'\n';
    b.codes.forEach((c,i)=>txt+=String(i+1).padStart(2,'0')+'. '+c+'\n');
    document.getElementById('result').textContent=txt; loadBatches();
  }else document.getElementById('result').textContent='ERREUR: '+(d.error||JSON.stringify(d));
}
async function loadBatches(){
  const secret=document.getElementById('secret').value;
  const d=await(await fetch('/api/admin/batches',{headers:{'X-Admin-Secret':secret}})).json();
  if(d.error){document.getElementById('batches').textContent=d.error;return;}
  document.getElementById('batches').innerHTML=(d.batches||[]).map(b=>'<div class="batch"><span>#'+b.id+' · <b>'+b.label+'</b></span><span>'+b.unused+' libres · '+b.active+' actifs <button style="padding:4px 8px;font-size:.65rem" onclick="showBatch('+b.id+')">Voir</button></span></div>').join('')||'Aucun';
}
async function showBatch(id){
  const secret=document.getElementById('secret').value;
  const d=await(await fetch('/api/admin/batch?id='+id,{headers:{'X-Admin-Secret':secret}})).json();
  if(d.error){alert(d.error);return;}
  let txt='LOT #'+d.batch.id+' — '+d.batch.label+'\n';
  d.codes.forEach((c,i)=>{txt+=String(i+1).padStart(2,'0')+'. '+c.code+' ['+c.status+']\n';});
  document.getElementById('result').textContent=txt;
}
</script></body></html>
"""


DASHBOARD = r"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xcode Suit Card Auto</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Rajdhani:wght@400;600;700&family=Courier+Prime&display=swap');
:root{--black:#0c0608;--card:#1a0e12;--gold:#d4a84b;--gold-dim:#a07830;--ivory:#f0e6d0;--muted:#a89878;--ok:#5ecf9a;--bad:#e07080;--border:#3a2028}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(ellipse at 20% 0%,#2a1018 0%,#0c0608 55%);color:var(--ivory);font-family:Rajdhani,system-ui,sans-serif;min-height:100vh}
.shell{max-width:1100px;margin:auto;padding:18px 12px 40px}
header{text-align:center;padding:22px 8px 14px;border-bottom:1px solid var(--border);margin-bottom:16px}
header::after{content:'';display:block;width:130px;height:2px;background:linear-gradient(90deg,transparent,var(--gold),transparent);margin:10px auto 0}
h1{font-family:Cinzel,serif;font-size:clamp(1.3rem,4vw,1.85rem);font-weight:900;color:var(--gold);letter-spacing:.1em;margin:6px 0}
.sub{font-size:.7rem;letter-spacing:.16em;color:var(--muted);text-transform:uppercase}
.panel{background:var(--card);border:1px solid var(--border);border-radius:6px;margin-bottom:14px;overflow:hidden}
.gt{border-top:2px solid var(--gold)}
.ph{padding:11px 13px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px}
.pb{padding:13px}
.ey{font-family:Cinzel,serif;font-size:.58rem;letter-spacing:.16em;color:var(--gold);text-transform:uppercase}
.title{font-weight:700;font-size:.9rem}
.pill{border-radius:999px;padding:3px 8px;font-size:.62rem;font-weight:700;border:1px solid var(--gold-dim);color:var(--gold);background:#1a1008}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--ok);margin-right:4px;animation:p 1.4s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.hero{display:grid;grid-template-columns:1.1fr .9fr;gap:11px}
@media(max-width:800px){.hero{grid-template-columns:1fr}}
.livegrid{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.box{background:#12080c;border:1px solid var(--border);border-radius:5px;padding:11px}
.num{font-family:Cinzel,serif;font-size:1.45rem;font-weight:900;color:var(--gold);margin:3px 0 7px}
.cards{display:flex;gap:4px;flex-wrap:wrap}
.card{width:42px;height:52px;border-radius:6px;background:#f5f0e6;color:#1a1010;display:flex;flex-direction:column;align-items:center;justify-content:center;font-weight:900;font-size:.75rem}
.card small{font-size:.9rem}.card.red{color:#b82030}
.pred-row{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.suit-big{font-size:4.2rem;line-height:1}
.pname{font-family:Cinzel,serif;font-size:1.1rem;font-weight:900;color:var(--gold)}
.btn-copy-pred{margin-top:8px;border:1px solid var(--gold-dim);background:#1a1010;color:var(--gold);
  padding:6px 12px;border-radius:4px;font-family:Cinzel,serif;font-size:.65rem;letter-spacing:.08em;
  cursor:pointer;text-transform:uppercase}
.btn-copy-pred:hover{border-color:var(--gold);background:#2a1810}
.btn-copy-pred.ok{background:#1a2a18;border-color:var(--ok);color:var(--ok)}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.metric{background:#12080c;border-radius:4px;padding:7px;border:1px solid var(--border)}
.metric span{display:block;font-size:.55rem;text-transform:uppercase;color:var(--muted)}.metric b{font-size:.9rem}
.note{margin-top:7px;padding:7px 9px;border-radius:4px;background:#2a1410;border:1px solid #6a3a20;color:#e0a870;font-size:.72rem;display:none}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin-bottom:14px}
@media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}}
.stat{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:11px;text-align:center}
.stat .val{font-family:Cinzel,serif;font-size:1.2rem;font-weight:900;color:var(--gold)}
.stat .val.ok{color:var(--ok)}.stat .val.bad{color:var(--bad)}.stat .small{font-size:.6rem;color:var(--muted)}
.btn-demarrer{width:100%;padding:14px;border-radius:6px;cursor:pointer;background:linear-gradient(180deg,#8b2240,#5a1228);border:2px solid var(--gold);color:var(--gold);font-family:Cinzel,serif;font-size:.92rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;box-shadow:0 0 18px rgba(212,168,75,.2)}
.btn-demarrer.off{background:linear-gradient(180deg,#2a1212,#1a0a0c);border-color:#6a3038;color:#c09090;box-shadow:none}
.btn-row{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
button.act{border:1px solid var(--gold-dim);background:#1a1010;color:var(--gold);padding:7px 10px;border-radius:4px;font-family:Cinzel,serif;font-size:.65rem;letter-spacing:.06em;cursor:pointer;text-transform:uppercase}
button.act.pri{background:linear-gradient(180deg,#6b1a2a,#4a1020);border-color:var(--gold)}
.live-status{margin-top:7px;padding:8px 10px;background:#12080c;border:1px solid var(--border);border-radius:4px;font-size:.8rem}
.demarrer-desc{font-size:.68rem;color:var(--muted);text-align:center;margin-top:6px;line-height:1.4}
.lt-wrap{margin-top:11px;border:1px solid var(--border);border-radius:5px;overflow:hidden;background:#12080c}
.lt-head{display:flex;justify-content:space-between;padding:7px 10px;background:linear-gradient(180deg,#2a1218,#1a0c10);border-bottom:1px solid var(--border)}
.lt-title{font-family:Cinzel,serif;font-size:.65rem;letter-spacing:.1em;color:var(--gold)}
.lt-badge{font-size:.58rem;padding:2px 6px;border-radius:999px;border:1px solid var(--gold-dim);color:var(--gold)}
.lt-scroll{max-height:260px;overflow:auto}
.lt{width:100%;border-collapse:collapse;font-size:.72rem;font-family:'Courier Prime',monospace}
.lt th{position:sticky;top:0;background:#1a0e12;color:var(--gold-dim);text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);font-size:.58rem;text-transform:uppercase}
.lt td{padding:6px 8px;border-bottom:1px solid #2a181c}
.lt tr.now td{background:#2a1810;border-left:3px solid var(--gold)}
.lt-num{color:var(--gold);font-weight:700}.lt-ok{color:var(--ok);font-weight:700}.lt-ko{color:var(--bad);font-weight:700}.lt-pend{color:var(--gold-dim)}
.grid2{display:grid;grid-template-columns:1.15fr .85fr;gap:11px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.tablewrap{max-height:380px;overflow:auto}
.table{width:100%;border-collapse:collapse;font-size:.7rem}
.table th,.table td{padding:6px 8px;border-bottom:1px solid #2a181c;text-align:left;white-space:nowrap}
.table th{position:sticky;top:0;background:#1a0e12;color:var(--muted);font-size:.55rem}
.status{padding:2px 5px;border-radius:999px;font-weight:800;font-size:.62rem}
.valid{background:#1a2a18;color:var(--ok)}.invalid{background:#2a1218;color:var(--bad)}.pending{background:#2a2010;color:var(--gold)}
.patterns,.strat-list{max-height:170px;overflow:auto}
.pattern,.strat-item{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #2a181c;font-family:'Courier Prime',monospace;font-size:.72rem}
.count{color:var(--gold);font-weight:700}.off{opacity:.45;text-decoration:line-through}
.out{margin-top:7px;padding:8px;background:#12080c;border:1px solid var(--border);border-radius:4px;font:10px 'Courier Prime',monospace;color:var(--muted);white-space:pre-wrap;min-height:36px}
.foot{text-align:center;color:#5a4048;font-size:.6rem;padding:14px 6px}
.license-bar{background:linear-gradient(90deg,#1a1008,#2a1810);border:1px solid var(--gold-dim);border-radius:6px;padding:12px 14px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.license-bar .cd{font-family:Cinzel,serif;font-size:1.6rem;font-weight:900;color:var(--gold);letter-spacing:.06em}
.license-bar .cd.warn{color:#e0a040}.license-bar .cd.danger{color:var(--bad);animation:p 1s infinite}
.license-bar .info{font-size:.72rem;color:var(--muted)}
.pname{font-family:Cinzel,serif;font-size:1.35rem;font-weight:900;color:var(--gold)}
#target{font-size:.85rem;padding:5px 12px}

</style></head><body><div class="shell">
<header><div style="color:var(--gold-dim);letter-spacing:.25em">♠ ♥ ♣ ♦</div>
<h1>XCODE SUIT CARD</h1>
<div class="sub">Auto-apprenant · pred des P1 suffisant · cartes live · bascule strategies</div></header>

<div class="license-bar" id="license-bar">
  <div>
    <div class="ey">Temps restant</div>
    <div class="cd" id="countdown">—:—:—</div>
  </div>
  <div class="info" id="lic-info">Vérification licence…</div>
</div>

<section class="panel gt"><div class="ph"><div><div class="ey">Telegram</div><div class="title">@statistika_baccara LIVE</div></div>
<span class="pill" id="live-badge">OFF</span></div>
<div class="pb">
<button class="btn-demarrer off" id="btn-live" onclick="toggleLive()">DEMARRER LIVE</button>
<div class="demarrer-desc">Scan 4s · pred des cartes P1 connues (2 ou 3) · pas besoin fin de main · vigilance 3-3</div>
<div class="live-status" id="live-status">LIVE arrete.</div>
<div class="btn-row">
<button class="act pri" onclick="collect(8)">Collecter 8</button>
<button class="act" onclick="collect(15)">Collecter 15</button>
<button class="act" onclick="collect(30)">Collecter 30</button>
<button class="act" onclick="learn()">Apprendre</button>
<button class="act" onclick="refreshAll()">Actualiser</button>
</div>
<div class="lt-wrap"><div class="lt-head"><span class="lt-title">SUIVI LIVE DES JEUX</span><span class="lt-badge" id="lt-count">0</span></div>
<div class="lt-scroll"><table class="lt"><thead><tr><th>Jeu</th><th>P1</th><th>P2</th><th>Fmt</th><th>Pred</th><th>Statut</th></tr></thead>
<tbody id="lt-body"><tr><td colspan="6" style="text-align:center;color:var(--muted);padding:12px">En attente…</td></tr></tbody></table></div></div>
</div></section>

<section class="hero">
<div class="panel gt"><div class="ph"><div><div class="ey">Dernier jeu</div><div class="title">Flux</div></div><span class="pill" id="clock">—</span></div>
<div class="pb"><div class="livegrid">
<div class="box"><div class="ey">PLAYER</div><div class="num" id="gn">—</div><div class="cards" id="pc">—</div>
<div style="margin-top:6px" class="ey">Format</div><b id="fmt">—</b></div>
<div class="box"><div class="ey">BANKER</div><div class="cards" id="bc" style="margin-top:5px">—</div>
<div style="margin-top:8px" class="ey">Cadence</div><b>~60 min</b></div>
</div><div class="note" id="alert33">Main 3-3 — vigilance algo.</div></div></div>
<div class="panel gt"><div class="ph"><div><div class="ey">Prediction live</div><div class="title">Prochain JOUEUR (des P1 connu)</div></div><span class="pill" id="strat">AUTO</span></div>
<div class="pb"><div class="pred-row"><div class="suit-big" id="ps">—</div>
<div><div class="ey">Enseigne</div><div class="pname" id="pn">En attente</div><span class="pill" id="target" style="font-size:1rem;padding:6px 14px;letter-spacing:.05em">Cible —</span>
<button type="button" class="btn-copy-pred" id="btn-copy-one" onclick="copyOnePred()">⧉ COPIER #N…</button></div></div>
<div class="metrics">
<div class="metric"><span>Taux</span><b id="rate">—</b></div><div class="metric"><span>Marge</span><b id="margin">—</b></div>
<div class="metric"><span>Echantillon</span><b id="sample">—</b></div><div class="metric"><span>Confiance</span><b id="conf">—</b></div>
</div><div class="note" id="pnote"></div></div></div>
</section>

<section class="stats">
<div class="stat"><div class="ey">Jeux</div><div class="val" id="total">—</div><div class="small">serveur</div></div>
<div class="stat"><div class="ey">Preds</div><div class="val" id="preds">—</div><div class="small">histo</div></div>
<div class="stat"><div class="ey">OK</div><div class="val ok" id="valid">—</div><div class="small">vert</div></div>
<div class="stat"><div class="ey">KO</div><div class="val bad" id="invalid">—</div><div class="small">rouge</div></div>
<div class="stat"><div class="ey">Strat</div><div class="val" id="sactive">—</div><div class="small">actives</div></div>
</section>

<div class="grid2">
<section class="panel gt"><div class="ph"><div><div class="ey">Memoire</div><div class="title">Historique</div></div>
<button class="act" onclick="copyPred()">Copier</button></div>
<div class="tablewrap"><table class="table"><thead><tr><th>Jeu</th><th>Pred</th><th>Strat</th><th>Taux</th><th>Statut</th><th>Reel</th></tr></thead>
<tbody id="hist"><tr><td colspan="6">…</td></tr></tbody></table></div></section>
<section class="panel gt"><div class="ph"><div><div class="ey">Moteur</div><div class="title">Strategies</div></div></div>
<div class="pb"><div class="strat-list" id="strats">—</div>
<div style="margin-top:10px" class="ey">Schemas</div><div class="patterns" id="patterns" style="margin-top:5px">—</div>
<div class="out" id="out">collecter → valider → scorer → pruner → predire</div></div></section>
</div>
<div class="foot">VALID = enseigne predite PARMI les cartes P1 · pred des P1 connu · bascule si real_rate &lt; 22%</div>
</div>
<script>
const sm={H:'♥',D:'♦',S:'♠',C:'♣'},sn={H:'Coeur',D:'Carreau',S:'Pique',C:'Trefle'};
const red=s=>s==='H'||s==='D';
const tx=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v??'—'};
const card=(lab,s)=>`<div class="card ${red(s)?'red':''}"><strong>${lab}</strong><small>${sm[s]||s}</small></div>`;
function getDeviceId(){
  let id=localStorage.getItem('xcode_device_id');
  if(id&&id.length>=12)return id;
  const raw=[navigator.userAgent||'',screen.width+'x'+screen.height,Math.random().toString(36)].join('|');
  let h=0;for(let i=0;i<raw.length;i++){h=((h<<5)-h)+raw.charCodeAt(i);h|=0;}
  id='DEV'+Math.abs(h).toString(36).toUpperCase()+Date.now().toString(36).toUpperCase().slice(-6);
  localStorage.setItem('xcode_device_id',id);return id;
}
async function j(u,o){
  const ctrl=new AbortController();
  const ms=(o&&o.timeout)||25000;
  const to=setTimeout(()=>ctrl.abort(),ms);
  try{
    const headers=Object.assign({'X-Device-Id':getDeviceId()},(o&&o.headers)||{});
    const r=await fetch(u,Object.assign({},o||{},{signal:ctrl.signal,headers}));
    clearTimeout(to);
    if(r.status===403){localStorage.removeItem('xcode_license');location.href='/';throw new Error('Licence expiree');}
    const txt=await r.text();
    try{return JSON.parse(txt)}catch(e){throw new Error('Reponse invalide HTTP '+r.status)}
  }catch(e){
    clearTimeout(to);
    if(e.name==='AbortError')throw new Error('Timeout '+ms/1000+'s — Telegram lent ou bloque');
    throw e;
  }
}
let liveOn=false,liveTimer=null,liveBusy=false;
function setLiveUI(on){const b=document.getElementById('btn-live'),g=document.getElementById('live-badge');
if(on){b.textContent='LIVE AUTO ACTIF';b.classList.remove('off');g.innerHTML='<span class="dot"></span>LIVE'}
else{b.textContent='DEMARRER LIVE';b.classList.add('off');g.textContent='OFF'}}
function toggleLive(){if(liveOn){liveOn=false;if(liveTimer){clearInterval(liveTimer);liveTimer=null}setLiveUI(false);tx('live-status','LIVE arrete.');return}
liveOn=true;setLiveUI(true);tx('live-status','LIVE…');runLiveTick();liveTimer=setInterval(runLiveTick,4000)}
async function runLiveTick(){
if(liveBusy)return;liveBusy=true;
tx('live-status','Collecte + learn…');
try{
  const d=await j('/api/collect?pages=2',{method:'POST',timeout:35000});
  await refreshAll();
  if(d&&d.status==='error'){tx('live-status','Collecte: '+(d.error||'erreur'));}
  else{
    const L=(d&&d.learning)||{};
    let m='+'+(d&&d.hands_new!=null?d.hands_new:0)+' nouvelles · '+(L.active_count||0)+' strat';
    if(d&&d.next_prediction)m+=' · pred '+(d.next_prediction.symbol||'');
    if(d&&d.hands_found!=null)m+=' · vus '+d.hands_found;
    tx('live-status',m);
  }
}catch(e){
  tx('live-status','Erreur: '+e.message);
  try{await refreshAll()}catch(e2){}
}finally{liveBusy=false}
}
async function refreshAll(){try{
const [l,h,p,st,hands,stR]=await Promise.all([j('/api/live'),j('/api/predictions?limit=200'),j('/api/patterns?limit=15'),j('/api/stats/overview'),j('/api/hands?limit=30'),j('/api/strategies')]);
const x=l.latest;tx('clock',new Date().toLocaleTimeString());
if(x){tx('gn','#N'+x.n);document.getElementById('pc').innerHTML=(x.player_suits||'').split(',').filter(Boolean).map((s,i)=>card('P'+(i+1),s)).join('')||'—';
document.getElementById('bc').innerHTML=(x.banker_suits||'').split(',').filter(Boolean).map((s,i)=>card('B'+(i+1),s)).join('')||'—';
tx('fmt',x.format||'?');const nP=(x.player_suits||'').split(',').filter(Boolean).length;const fmtEl=document.getElementById('fmt');if(fmtEl&&nP>=2)fmtEl.innerHTML=(x.format||(nP+'-?'))+' <span style="color:var(--ok)">· P1 OK</span>';const al=document.getElementById('alert33');if(al)al.style.display=x.is_33?'block':'none';
if(x.is_33){const ls=document.getElementById('live-status');if(ls&&!ls.textContent.includes('3-3'))ls.textContent=(ls.textContent||'')+' · ⚠ dernier jeu 3-3'}}
if(!l.prediction){
  const waiting=l.player_ready===false || (l.learning&&l.learning.player_ready===false);
  tx('ps','—');tx('pn',waiting?'⏳ Attente 3e carte P':'En attente');
  tx('target',waiting?(x?'#N'+x.n+' en cours':'—'):'Cible —');
  tx('rate','—');tx('margin','—');tx('sample','—');tx('conf','—');
  const b0=document.getElementById('btn-copy-one');if(b0)b0.textContent='⧉ COPIER #N…';
  const pn0=document.getElementById('pnote');
  if(waiting&&pn0){pn0.style.display='block';pn0.textContent='▶ Main joueur encore en cours — prediction du prochain jeu apres la 3e carte (ou fin 2-2)';}
  else if(pn0){pn0.style.display='none';}
  if(waiting){const rs=document.getElementById('live-status');if(rs)rs.textContent='⏳ #N'+(x?x.n:'?')+' P incomplete (▶) — pred en pause';}
}if(l.prediction){const q=l.prediction;const p1n=(x&&x.player_suits)?x.player_suits.split(',').filter(Boolean).length:0;if(p1n>=2){const rs=document.getElementById('live-status');if(rs&&!String(rs.textContent).includes('P1 pret'))rs.textContent='P1 complet ('+p1n+' cartes) → pred #N'+(l.prediction_target_n||'?')+' emise';}tx('ps',q.symbol);tx('pn',(q.symbol||'')+' — '+(sn[q.suit]||''));
tx('target','#N'+l.prediction_target_n);tx('strat',q.strategy||'AUTO');
window._lastPredTxt='#N'+l.prediction_target_n+(q.symbol||sm[q.suit]||q.suit||'');
const b1=document.getElementById('btn-copy-one');if(b1)b1.textContent='⧉ COPIER '+window._lastPredTxt;tx('rate',q.hit_rate+'%');
tx('margin',(q.margin>=0?'+':'')+q.margin);tx('sample',q.sample);tx('conf',Math.round((q.confidence||0)*100)+'%');
const pn=document.getElementById('pnote');if(q.note){pn.style.display='block';pn.textContent=q.note}else{pn.style.display='none'}}
const ps=l.pred_stats||{};tx('total',st.total_hands);tx('preds',ps.total||h.length);tx('valid',ps.valid||0);tx('invalid',ps.invalid||0);
tx('sactive',st.strategies_active!=null?st.strategies_active:(stR.active||[]).length);
document.getElementById('hist').innerHTML=(Array.isArray(h)?h:[]).map(x=>`<tr><td>#${x.target_n}</td><td><b>${sm[x.prediction_suit]||x.prediction_suit}</b></td>
<td>${x.strategy||'—'}</td><td>${x.hit_rate}%</td>
<td><span class="status ${x.status==='VALID'?'valid':x.status==='INVALID'?'invalid':'pending'}">${x.status}</span></td>
<td>${(x.actual_first_suit||'').split(',').filter(Boolean).map(s=>sm[s]||s).join(' ')||'—'}</td></tr>`).join('')||'<tr><td colspan="6">Vide</td></tr>';
const allS=Array.isArray(stR.all)?stR.all:(Array.isArray(stR.active)?stR.active:[]);
document.getElementById('strats').innerHTML=allS.slice(0,12).map(s=>{const rate=s.real_total>=8?s.real_rate:s.hist_rate;
return `<div class="strat-item"><span class="${s.is_active?'':'off'}">${s.name}</span><span>${s.is_active?'✓ '+rate+'%':'✗ coupe'}</span></div>`}).join('')||'—';
document.getElementById('patterns').innerHTML=(Array.isArray(p)?p:[]).map(x=>`<div class="pattern"><code>${x.pattern}</code><span class="count">x${x.occurrences}</span></div>`).join('')||'—';
fillLiveTable(Array.isArray(hands)?hands:[], Array.isArray(h)?h:[]);
if(l.learning&&l.learning.diagnosis)tx('out','Diag: '+l.learning.diagnosis);
if(l.license&&l.license.remaining_seconds!=null)remainingSeconds=l.license.remaining_seconds;
}catch(e){tx('out','Err '+e)}}
async function collect(n){tx('out','Collecte…');try{const d=await j('/api/collect?pages='+n,{method:'POST'});document.getElementById('out').textContent=JSON.stringify(d,null,2);await refreshAll()}catch(e){tx('out','Err '+e)}}
async function learn(){tx('out','Learn…');try{const d=await j('/api/learn',{method:'POST'});document.getElementById('out').textContent=JSON.stringify(d,null,2);await refreshAll()}catch(e){tx('out','Err '+e)}}
async function copyOnePred(){
  const txt=window._lastPredTxt||'';
  if(!txt){alert('Pas de prediction');return}
  try{
    await navigator.clipboard.writeText(txt);
    const b=document.getElementById('btn-copy-one');
    if(b){const o=b.textContent;b.textContent='✓ COPIE';b.classList.add('ok');setTimeout(()=>{b.textContent=o;b.classList.remove('ok')},1200)}
  }catch(e){alert('Copie impossible')}
}
async function copyPred(){try{const h=await j('/api/predictions?limit=300');
const t=h.map(x=>`#N${x.target_n} | ${sm[x.prediction_suit]||x.prediction_suit} | ${x.strategy||'-'} | ${x.hit_rate}% | ${x.status} | ${(x.actual_first_suit||'').split(',').map(s=>sm[s]||s).filter(Boolean).join(' ')||'-'}`).join('\n');
await navigator.clipboard.writeText(t||'Vide')}catch(e){alert('Copie KO')}}

let remainingSeconds=0, cdTimer=null;
function formatCD(sec){
  if(sec<0)sec=0;
  const d=Math.floor(sec/86400), h=Math.floor((sec%86400)/3600), m=Math.floor((sec%3600)/60), s=sec%60;
  if(d>0) return d+'j '+String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
  return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
}
function tickCountdown(){
  const el=document.getElementById('countdown');
  if(!el)return;
  el.textContent=formatCD(remainingSeconds);
  el.className='cd'+(remainingSeconds<60?' danger':remainingSeconds<300?' warn':'');
  if(remainingSeconds<=0){
    clearInterval(cdTimer);cdTimer=null;
    localStorage.removeItem('xcode_license');
    location.href='/';
    return;
  }
  remainingSeconds--;
}
async function checkLicenseCD(){
  try{
    const d=await j('/api/license/status');
    if(!d.ok){localStorage.removeItem('xcode_license');location.href='/';return;}
    remainingSeconds=d.remaining_seconds||0;
    const info=document.getElementById('lic-info');
    if(info) info.textContent='Code '+(d.code||'').slice(0,6)+'… · expire '+(d.expires_at||'').replace('T',' ').slice(0,19)+' UTC';
    if(!cdTimer){tickCountdown();cdTimer=setInterval(tickCountdown,1000);}
  }catch(e){}
}

checkLicenseCD();
setInterval(checkLicenseCD,60000);
refreshAll();
setInterval(refreshAll,3000);
// si base vide au demarrage, une collecte legere
setTimeout(async()=>{
  try{
    const st=await j('/api/stats/overview',{timeout:8000});
    if(st&&(st.total_hands||0)<3){
      tx('live-status','Base vide — premiere collecte…');
      const d=await j('/api/collect?pages=5',{method:'POST',timeout:45000});
      await refreshAll();
      tx('live-status',d.status==='ok'?('Init +'+(d.hands_new||0)+' mains'):('Init: '+(d.error||'ko')));
    }
  }catch(e){tx('live-status','Init: '+e.message)}
},800);

</script></body></html>
"""

def auto_collect_loop():
    if AUTO_COLLECT_INTERVAL<=0: return
    while True:
        time.sleep(AUTO_COLLECT_INTERVAL)
        try:
            print("[AUTO] Collecte…"); parsed=collect(pages=4,delay=0.35); new=upsert_hands(parsed)
            if new:
                cycle=run_learning_cycle()
                print(f"[AUTO] +{new} actives={cycle.get('active_count')}")
        except Exception as e: print(f"[AUTO] {e}")

if __name__=="__main__":
    init_db(); init_license_tables(); print(f"DB: {DB_PATH}"); print(f"Admin: {ADMIN_SECRET}"); print(f"http://0.0.0.0:{PORT}")
    if AUTO_COLLECT_INTERVAL>0:
        threading.Thread(target=auto_collect_loop,daemon=True).start()
        print(f"Auto-collect {AUTO_COLLECT_INTERVAL}s")
    try: HTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
    except KeyboardInterrupt: print("\\nArret.")
