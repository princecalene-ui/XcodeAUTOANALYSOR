#!/usr/bin/env python3
"""
Xcode SUIT CARD STRATÉGIE CREATOR
Version améliorée — prédictions consécutives automatiques
Focus : enseigne (suit) de la 1ère carte JOUEUR
Stockage 100 % serveur (SQLite) — pas de localStorage
"""
import os
import sys
import json
import sqlite3
import re
import time
from datetime import datetime
from collections import Counter, defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import requests
from bs4 import BeautifulSoup

# -------------------------------------------------
# Config
# -------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "baccarat.db")
CHANNEL_WEB = "https://t.me/s/statistika_baccara"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
PORT = int(os.environ.get("PORT", 8000))
AUTO_COLLECT_INTERVAL = 90  # secondes (0 = désactivé)

# -------------------------------------------------
# Parser
# -------------------------------------------------
SUIT_MAP = {
    "♥": "H", "♦": "D", "♠": "S", "♣": "C",
    "♥️": "H", "♦️": "D", "♠️": "S", "♣️": "C",
}
COLOR_MAP = {"H": "R", "D": "R", "S": "B", "C": "B"}
RANK_VALUE = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 0, "T": 0, "J": 0, "Q": 0, "K": 0,
}
SUITS = ["H", "D", "S", "C"]
EMOJI = {"H": "♥", "D": "♦", "S": "♠", "C": "♣"}
SUIT_NAME = {"H": "Cœur", "D": "Carreau", "S": "Pique", "C": "Trèfle"}

HAND_RE = re.compile(
    r"#N(\d+)\s*\.\s*(\d+)\s*\(([^)]+)\)\s*-\s*"
    r"(\d+)\s*\(([^)]+)\)\s*(#T\d+)?\s*(#R)?",
    re.I,
)
CARD_RE = re.compile(r"(A|10|[2-9JQKT])\s*([♥♦♠♣]|♥️|♦️|♠️|♣️)", re.I)

def parse_cards(s):
    cards = []
    for rank, suit_char in CARD_RE.findall(s):
        rank = rank.upper()
        if rank == "T":
            rank = "10"
        suit = SUIT_MAP.get(suit_char)
        if not suit:
            continue
        cards.append({
            "rank": rank,
            "suit": suit,
            "color": COLOR_MAP[suit],
            "value": RANK_VALUE.get(rank, 0),
        })
    return cards

def parse_message(text, msg_id=None):
    m = HAND_RE.search(text.strip())
    if not m:
        return None
    n, ps, pc, bs, bc, t_tag, r_tag = m.groups()
    p_cards = parse_cards(pc)
    b_cards = parse_cards(bc)
    if not p_cards and not b_cards:
        return None
    p_count = len(p_cards)
    b_count = len(b_cards)
    # Format detection
    fmt = f"{p_count}-{b_count}"
    is_33 = (p_count == 3 and b_count == 3)
    is_22 = (p_count == 2 and b_count == 2)
    player_drew_3 = (p_count == 3)
    return {
        "n": int(n),
        "player_score": int(ps),
        "banker_score": int(bs),
        "player_cards": p_cards,
        "banker_cards": b_cards,
        "t_tag": t_tag,
        "is_r": bool(r_tag),
        "message_id": msg_id,
        "raw": text.strip(),
        "format": fmt,
        "is_33": is_33,
        "is_22": is_22,
        "player_drew_3": player_drew_3,
    }

# -------------------------------------------------
# Database
# -------------------------------------------------
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        n INTEGER UNIQUE NOT NULL,
        player_score INTEGER,
        banker_score INTEGER,
        player_suits TEXT,
        banker_suits TEXT,
        player_first_suit TEXT,
        banker_first_suit TEXT,
        player_first_color TEXT,
        banker_first_color TEXT,
        player_first_val INTEGER,
        banker_first_val INTEGER,
        player_red INTEGER DEFAULT 0,
        player_black INTEGER DEFAULT 0,
        banker_red INTEGER DEFAULT 0,
        banker_black INTEGER DEFAULT 0,
        t_tag TEXT,
        is_r INTEGER DEFAULT 0,
        player_card_count INTEGER,
        banker_card_count INTEGER,
        format TEXT,
        is_33 INTEGER DEFAULT 0,
        is_22 INTEGER DEFAULT 0,
        player_drew_3 INTEGER DEFAULT 0,
        message_id INTEGER,
        collected_at TEXT,
        source TEXT DEFAULT 'web'
    );
    CREATE INDEX IF NOT EXISTS idx_n ON hands(n);
    CREATE INDEX IF NOT EXISTS idx_pfs ON hands(player_first_suit);
    CREATE INDEX IF NOT EXISTS idx_bfs ON hands(banker_first_suit);
    CREATE INDEX IF NOT EXISTS idx_fmt ON hands(format);

    CREATE TABLE IF NOT EXISTS collection_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        hands_found INTEGER,
        hands_new INTEGER,
        status TEXT,
        error TEXT
    );

    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_n INTEGER UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        prediction_suit TEXT NOT NULL,
        strategy TEXT,
        confidence REAL DEFAULT 0,
        hit_rate REAL DEFAULT 0,
        margin REAL DEFAULT 0,
        basis_n INTEGER,
        basis_first_suit TEXT,
        status TEXT DEFAULT 'PENDING',
        actual_first_suit TEXT,
        validated_at TEXT,
        note TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target_n);
    CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);

    CREATE TABLE IF NOT EXISTS pattern_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern TEXT UNIQUE NOT NULL,
        occurrences INTEGER DEFAULT 0,
        last_seen_n INTEGER,
        updated_at TEXT NOT NULL,
        context TEXT
    );
    """)
    # Migrations légères
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(hands)").fetchall()}
        for col, typ in [
            ("format", "TEXT"), ("is_33", "INTEGER DEFAULT 0"),
            ("is_22", "INTEGER DEFAULT 0"), ("player_drew_3", "INTEGER DEFAULT 0"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE hands ADD COLUMN {col} {typ}")
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(pattern_observations)").fetchall()}
        if "context" not in pcols:
            conn.execute("ALTER TABLE pattern_observations ADD COLUMN context TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

def upsert_hands(parsed_list):
    conn = get_conn()
    existing = {r[0] for r in conn.execute("SELECT n FROM hands").fetchall()}
    new = 0
    for h in parsed_list:
        if h["n"] in existing:
            continue
        p = h["player_cards"]
        b = h["banker_cards"]
        conn.execute("""
            INSERT INTO hands (
                n, player_score, banker_score,
                player_suits, banker_suits,
                player_first_suit, banker_first_suit,
                player_first_color, banker_first_color,
                player_first_val, banker_first_val,
                player_red, player_black, banker_red, banker_black,
                t_tag, is_r, player_card_count, banker_card_count,
                format, is_33, is_22, player_drew_3,
                message_id, collected_at, source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            h["n"], h["player_score"], h["banker_score"],
            ",".join(c["suit"] for c in p),
            ",".join(c["suit"] for c in b),
            p[0]["suit"] if p else None,
            b[0]["suit"] if b else None,
            p[0]["color"] if p else None,
            b[0]["color"] if b else None,
            p[0]["value"] if p else None,
            b[0]["value"] if b else None,
            sum(1 for c in p if c["color"] == "R"),
            sum(1 for c in p if c["color"] == "B"),
            sum(1 for c in b if c["color"] == "R"),
            sum(1 for c in b if c["color"] == "B"),
            h["t_tag"], 1 if h["is_r"] else 0,
            len(p), len(b),
            h.get("format"), 1 if h.get("is_33") else 0,
            1 if h.get("is_22") else 0, 1 if h.get("player_drew_3") else 0,
            h.get("message_id"),
            datetime.utcnow().isoformat(), "web",
        ))
        new += 1
    conn.commit()
    conn.close()
    return new

def get_stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
    oldest = conn.execute("SELECT MIN(n) FROM hands").fetchone()[0]
    latest = conn.execute("SELECT MAX(n) FROM hands").fetchone()[0]
    n33 = conn.execute("SELECT COUNT(*) FROM hands WHERE is_33=1").fetchone()[0]
    n22 = conn.execute("SELECT COUNT(*) FROM hands WHERE is_22=1").fetchone()[0]
    conn.close()
    return {
        "total_hands": total, "oldest_n": oldest, "latest_n": latest,
        "count_33": n33, "count_22": n22,
    }

def get_all_hands():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM hands ORDER BY n ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def upsert_prediction(target_n, prediction, basis_n=None, basis_first_suit=None, note=None):
    if not prediction:
        return
    conn = get_conn()
    conn.execute("""
        INSERT INTO predictions
          (target_n, created_at, prediction_suit, strategy, confidence, hit_rate, margin,
           basis_n, basis_first_suit, note)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(target_n) DO UPDATE SET
          prediction_suit=excluded.prediction_suit,
          strategy=excluded.strategy,
          confidence=excluded.confidence,
          hit_rate=excluded.hit_rate,
          margin=excluded.margin,
          basis_n=excluded.basis_n,
          basis_first_suit=excluded.basis_first_suit,
          note=excluded.note,
          created_at=excluded.created_at
    """, (
        target_n, datetime.utcnow().isoformat(),
        prediction["suit"], prediction.get("strategy"),
        prediction.get("confidence", 0), prediction.get("hit_rate", 0),
        prediction.get("margin", 0), basis_n, basis_first_suit, note,
    ))
    conn.commit()
    conn.close()

def validate_predictions():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.id, p.target_n, p.prediction_suit, h.player_first_suit
        FROM predictions p
        JOIN hands h ON h.n = p.target_n
        WHERE p.status = 'PENDING'
    """).fetchall()
    for r in rows:
        status = "VALID" if r["player_first_suit"] == r["prediction_suit"] else "INVALID"
        conn.execute(
            "UPDATE predictions SET status=?, actual_first_suit=?, validated_at=? WHERE id=?",
            (status, r["player_first_suit"], datetime.utcnow().isoformat(), r["id"]),
        )
    conn.commit()
    conn.close()
    return len(rows)

def get_prediction_history(limit=200):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM predictions ORDER BY target_n DESC LIMIT ?", (int(limit),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def record_patterns(hands):
    if len(hands) < 3:
        return
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    for i in range(2, len(hands)):
        vals = [hands[j].get("player_first_suit") for j in (i - 2, i - 1, i)]
        if not all(vals):
            continue
        pattern = "P:" + ">".join(vals)
        ctx = hands[i].get("format") or ""
        conn.execute("""
            INSERT INTO pattern_observations(pattern, occurrences, last_seen_n, updated_at, context)
            VALUES (?,?,?,?,?)
            ON CONFLICT(pattern) DO UPDATE SET
              occurrences = occurrences + 1,
              last_seen_n = excluded.last_seen_n,
              updated_at = excluded.updated_at,
              context = excluded.context
        """, (pattern, 1, hands[i]["n"], now, ctx))
        # Schéma après 3-3
        if hands[i - 1].get("is_33"):
            p33 = "AFTER33:" + (hands[i].get("player_first_suit") or "?")
            conn.execute("""
                INSERT INTO pattern_observations(pattern, occurrences, last_seen_n, updated_at, context)
                VALUES (?,?,?,?,?)
                ON CONFLICT(pattern) DO UPDATE SET
                  occurrences = occurrences + 1,
                  last_seen_n = excluded.last_seen_n,
                  updated_at = excluded.updated_at
            """, (p33, 1, hands[i]["n"], now, "post-33"))
    conn.commit()
    conn.close()

def get_patterns(limit=25):
    conn = get_conn()
    rows = conn.execute(
        "SELECT pattern, occurrences, last_seen_n, context FROM pattern_observations "
        "ORDER BY occurrences DESC, last_seen_n DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# -------------------------------------------------
# Collector
# -------------------------------------------------
def fetch_page(before=None):
    url = CHANNEL_WEB
    if before:
        url += f"?before={before}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    messages = []
    ids = []
    for w in soup.select(".tgme_widget_message"):
        post = w.get("data-post", "")
        mid = None
        if post and "/" in post:
            try:
                mid = int(post.split("/")[-1])
                ids.append(mid)
            except ValueError:
                pass
        te = w.select_one(".tgme_widget_message_text")
        if te:
            text = te.get_text(separator=" ", strip=True)
            if text and "#N" in text[:50]:
                messages.append({"id": mid, "text": text})
    return messages, (min(ids) if ids else None)

def collect(pages=10, delay=0.4):
    all_parsed = []
    seen = set()
    before = None
    for i in range(pages):
        try:
            raw, min_id = fetch_page(before)
        except Exception as e:
            print(f"Erreur page {i+1}: {e}")
            break
        for msg in raw:
            p = parse_message(msg["text"], msg["id"])
            if p and p["n"] not in seen:
                seen.add(p["n"])
                all_parsed.append(p)
        before = min_id
        print(f" Page {i+1}/{pages} → +{len(raw)} msgs, total uniques={len(all_parsed)}")
        if i < pages - 1:
            time.sleep(delay)
    all_parsed.sort(key=lambda x: x["n"])
    return all_parsed

# -------------------------------------------------
# Analyzer & Strategy Engine
# -------------------------------------------------
def analyze(hands):
    if not hands:
        return {"error": "Aucune donnée"}

    p_first = Counter()
    b_first = Counter()
    for h in hands:
        if h.get("player_first_suit"):
            p_first[h["player_first_suit"]] += 1
        if h.get("banker_first_suit"):
            b_first[h["banker_first_suit"]] += 1

    def pct(c):
        t = sum(c.values()) or 1
        return {s: round(100 * c[s] / t, 2) for s in SUITS}

    def transitions(side):
        key = "player_first_suit" if side == "player" else "banker_first_suit"
        counts = defaultdict(lambda: defaultdict(int))
        for i in range(len(hands) - 1):
            s1 = hands[i].get(key)
            s2 = hands[i + 1].get(key)
            if s1 and s2:
                counts[s1][s2] += 1
        matrix = {}
        for s1 in SUITS:
            total = sum(counts[s1].values()) or 1
            matrix[s1] = {
                s2: round(100 * counts[s1][s2] / total, 2) for s2 in SUITS
            }
        return matrix, {k: dict(v) for k, v in counts.items()}

    tp, tp_c = transitions("player")
    tb, tb_c = transitions("banker")

    strategies = []
    for side, matrix, counts in [
        ("player", tp, tp_c), ("banker", tb, tb_c)
    ]:
        for fs in SUITS:
            total = sum(counts.get(fs, {}).values())
            if total < 20:
                continue
            for ts in SUITS:
                rate = matrix[fs][ts]
                if rate >= 30:
                    strategies.append({
                        "name": f"Trans_{side[0].upper()}_{fs}_to_{ts}",
                        "description": (
                            f"Si 1ère carte {side} précédente = {EMOJI[fs]}, "
                            f"alors suivante ≈ {rate}% {EMOJI[ts]}"
                        ),
                        "side": side,
                        "from": fs,
                        "to": ts,
                        "hit_rate": rate,
                        "sample": total,
                        "confidence": round(
                            min(rate / 50, 1) * min(total, 150) / 150, 3
                        ),
                    })

    strategies.sort(key=lambda x: (x["confidence"], x["hit_rate"], x["sample"]), reverse=True)
    return {
        "n_hands": len(hands),
        "n_range": {"min": hands[0]["n"], "max": hands[-1]["n"]},
        "player_first": pct(p_first),
        "banker_first": pct(b_first),
        "transitions_player": tp,
        "transitions_banker": tb,
        "strategies": strategies[:20],
        "strategies_count": len(strategies),
    }

def pick_best_prediction(hands, report):
    """Choisit la meilleure stratégie adaptée au dernier jeu et prédit le suivant."""
    if not hands or not report.get("strategies"):
        return None, None
    latest = hands[-1]
    # Priorité absolue : transitions PLAYER (enseigne joueur)
    applicable = [
        x for x in report["strategies"]
        if x["side"] == "player" and x["from"] == latest.get("player_first_suit")
    ]
    # Fallback : banker si rien de solide côté player
    if not applicable or max(a["confidence"] for a in applicable) < 0.25:
        b_app = [
            x for x in report["strategies"]
            if x["side"] == "banker" and x["from"] == latest.get("banker_first_suit")
        ]
        if b_app and (not applicable or max(b["confidence"] for b in b_app) > max(a["confidence"] for a in applicable)):
            applicable = b_app

    if not applicable:
        # Fallback global : suit le plus fréquent côté joueur
        pf = report.get("player_first") or {}
        if pf:
            best_s = max(pf, key=pf.get)
            return {
                "suit": best_s,
                "symbol": EMOJI[best_s],
                "hit_rate": pf[best_s],
                "confidence": 0.15,
                "sample": report.get("n_hands", 0),
                "margin": round(pf[best_s] - 25, 2),
                "strategy": "FREQ_PLAYER",
            }, latest
        return None, latest

    best = max(applicable, key=lambda x: (x["confidence"], x["hit_rate"], x["sample"]))
    note = None
    if latest.get("is_33"):
        note = "ATTENTION: main précédente 3-3 (possible signal de changement d'algo)"
    elif latest.get("player_drew_3"):
        note = "Joueur a tiré 3 cartes sur la main précédente"

    pred = {
        "suit": best["to"],
        "symbol": EMOJI[best["to"]],
        "hit_rate": best["hit_rate"],
        "confidence": best["confidence"],
        "sample": best["sample"],
        "margin": round(best["hit_rate"] - 25, 2),
        "strategy": best["name"],
        "note": note,
    }
    return pred, latest

# -------------------------------------------------
# HTTP Handler
# -------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        if path == "/" or path == "/index.html":
            stats = get_stats()
            html = DASHBOARD
            html = html.replace("{total_hands}", str(stats["total_hands"]))
            html = html.replace(
                "{oldest_n}",
                str(stats["oldest_n"] if stats["oldest_n"] is not None else "—"),
            )
            html = html.replace(
                "{latest_n}",
                str(stats["latest_n"] if stats["latest_n"] is not None else "—"),
            )
            self.send_html(html)

        elif path == "/api/stats/overview":
            self.send_json(get_stats())

        elif path == "/api/analysis/full":
            hands = get_all_hands()
            self.send_json(analyze(hands))

        elif path == "/api/analysis/strategies":
            hands = get_all_hands()
            report = analyze(hands)
            self.send_json({
                "count": report.get("strategies_count", 0),
                "strategies": report.get("strategies", []),
            })

        elif path == "/api/live":
            hands = get_all_hands()
            report = analyze(hands)
            prediction, latest = pick_best_prediction(hands, report)
            if prediction and latest:
                upsert_prediction(
                    latest["n"] + 1,
                    prediction,
                    latest["n"],
                    latest.get("player_first_suit"),
                    prediction.get("note"),
                )
            validated = validate_predictions()
            record_patterns(hands)
            # Stats prédictions
            hist = get_prediction_history(300)
            n_valid = sum(1 for x in hist if x["status"] == "VALID")
            n_invalid = sum(1 for x in hist if x["status"] == "INVALID")
            n_pending = sum(1 for x in hist if x["status"] == "PENDING")
            self.send_json({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "latest": latest,
                "prediction": prediction,
                "prediction_target_n": (latest["n"] + 1) if latest else None,
                "prediction_history": hist[:100],
                "patterns": get_patterns(20),
                "validated_now": validated,
                "pred_stats": {
                    "total": len(hist), "valid": n_valid,
                    "invalid": n_invalid, "pending": n_pending,
                },
                "timing": {"game_minutes": 60, "mode": "consecutive_next_game"},
            })

        elif path == "/api/predictions":
            validate_predictions()
            self.send_json(get_prediction_history(int(qs.get("limit", [200])[0])))

        elif path == "/api/patterns":
            self.send_json(get_patterns(int(qs.get("limit", [30])[0])))

        elif path == "/api/hands":
            limit = int(qs.get("limit", [50])[0])
            offset = int(qs.get("offset", [0])[0])
            conn = get_conn()
            rows = conn.execute(
                "SELECT n, player_score, banker_score, player_suits, banker_suits, "
                "player_first_suit, banker_first_suit, format, is_33, is_22, "
                "player_drew_3, t_tag, is_r "
                "FROM hands ORDER BY n DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        if path == "/api/collect":
            pages = int(qs.get("pages", [10])[0])
            print(f"=== Collecte de {pages} pages ===")
            try:
                parsed = collect(pages=pages)
                new = upsert_hands(parsed)
                hands_now = get_all_hands()
                record_patterns(hands_now)
                validate_predictions()
                # Recalcul immédiat de la prédiction suivante
                report = analyze(hands_now)
                pred, latest = pick_best_prediction(hands_now, report)
                if pred and latest:
                    upsert_prediction(
                        latest["n"] + 1, pred, latest["n"],
                        latest.get("player_first_suit"), pred.get("note"),
                    )
                conn = get_conn()
                conn.execute(
                    "INSERT INTO collection_logs "
                    "(ts, hands_found, hands_new, status) VALUES (?,?,?,?)",
                    (datetime.utcnow().isoformat(), len(parsed), new, "success"),
                )
                conn.commit()
                conn.close()
                self.send_json({
                    "status": "ok",
                    "hands_found": len(parsed),
                    "hands_new": new,
                    "message": f"{new} nouvelles mains enregistrées — stratégie recalibrée",
                    "next_prediction": pred,
                })
            except Exception as e:
                self.send_json({"status": "error", "error": str(e)}, 500)
        else:
            self.send_json({"error": "Not found"}, 404)

DASHBOARD = r"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xcode Suit Card — Live</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;700;900&family=Rajdhani:wght@400;600;700&family=Courier+Prime:wght@400;700&display=swap');
:root{
  --black:#0c0608;--deep:#140a0e;--card:#1a0e12;--bordeaux:#6b1a2a;--crimson:#8b2240;
  --gold:#d4a84b;--gold-dim:#a07830;--ivory:#f0e6d0;--muted:#a89878;
  --ok:#5ecf9a;--bad:#e07080;--border:#3a2028;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(ellipse at 20% 0%,#2a1018 0%,#0c0608 55%);color:var(--ivory);font-family:'Rajdhani',system-ui,sans-serif;min-height:100vh}
.shell{max-width:1100px;margin:auto;padding:22px 16px 50px}
header{text-align:center;padding:28px 12px 18px;border-bottom:1px solid var(--border);margin-bottom:20px}
header::after{content:'';display:block;width:140px;height:2px;background:linear-gradient(90deg,transparent,var(--gold),transparent);margin:12px auto 0}
.suits{font-size:1.2rem;color:var(--gold-dim);letter-spacing:.3em}
h1{font-family:'Cinzel',serif;font-size:clamp(1.4rem,4vw,2rem);font-weight:900;color:var(--gold);letter-spacing:.12em;margin:8px 0 4px}
.sub{font-size:.75rem;letter-spacing:.2em;color:var(--muted);text-transform:uppercase}
.author{font-size:.68rem;color:var(--gold-dim);margin-top:4px;letter-spacing:.15em}

.panel{background:var(--card);border:1px solid var(--border);border-radius:6px;margin-bottom:18px;position:relative;overflow:hidden}
.panel.gold-top{border-top:2px solid var(--gold)}
.panel .ph{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.panel .pb{padding:16px}
.ey{font-family:'Cinzel',serif;font-size:.62rem;letter-spacing:.2em;color:var(--gold);text-transform:uppercase}
.title{font-weight:700;font-size:.95rem;margin-top:2px}
.pill{border-radius:999px;padding:5px 10px;font-size:.68rem;font-weight:700;border:1px solid var(--gold-dim);color:var(--gold);background:#1a1008}
.live-pill{color:#8fd6b3;border-color:#2a5a40;background:#0c1810}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--ok);margin-right:6px;animation:pulse 1.4s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

.hero{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}
@media(max-width:800px){.hero{grid-template-columns:1fr}}
.livegrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.box{background:#12080c;border:1px solid var(--border);border-radius:5px;padding:14px}
.num{font-family:'Cinzel',serif;font-size:1.7rem;font-weight:900;color:var(--gold);margin:6px 0 10px}
.cards{display:flex;gap:6px;flex-wrap:wrap}
.card{width:48px;height:62px;border-radius:8px;background:#f5f0e6;color:#1a1010;display:flex;flex-direction:column;align-items:center;justify-content:center;font-weight:900;font-size:.85rem}
.card small{font-size:1rem}.card.red{color:#b82030}
.pred-row{display:flex;align-items:center;gap:14px;margin-bottom:12px}
.suit-big{font-size:4rem;line-height:1}
.pname{font-family:'Cinzel',serif;font-size:1.25rem;font-weight:900;color:var(--gold)}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.metric{background:#12080c;border-radius:5px;padding:10px;border:1px solid var(--border)}
.metric span{display:block;font-size:.6rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.metric b{font-size:1rem;color:var(--ivory)}
.note{margin-top:10px;padding:8px 12px;border-radius:4px;background:#2a1410;border:1px solid #6a3a20;color:#e0a870;font-size:.78rem;display:none}

.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:18px}
@media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}}
.stat{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:14px;text-align:center}
.stat .ey{display:block;margin-bottom:4px}
.stat .val{font-family:'Cinzel',serif;font-size:1.35rem;font-weight:900;color:var(--gold)}
.stat .val.ok{color:var(--ok)}.stat .val.bad{color:var(--bad)}
.stat .small{font-size:.65rem;color:var(--muted);margin-top:2px}

/* LIVE controls */
.btn-demarrer{
  width:100%;padding:16px;border-radius:6px;cursor:pointer;
  background:linear-gradient(180deg,#8b2240,#5a1228);border:2px solid var(--gold);color:var(--gold);
  font-family:'Cinzel',serif;font-size:1rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  box-shadow:0 0 22px rgba(212,168,75,.22);transition:.12s;
}
.btn-demarrer:hover{box-shadow:0 0 30px rgba(212,168,75,.4);transform:translateY(-1px)}
.btn-demarrer.off{background:linear-gradient(180deg,#2a1212,#1a0a0c);border-color:#6a3038;color:#c09090;box-shadow:none}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
button.act{
  border:1px solid var(--gold-dim);background:#1a1010;color:var(--gold);padding:9px 12px;border-radius:4px;
  font-family:'Cinzel',serif;font-size:.72rem;letter-spacing:.1em;cursor:pointer;text-transform:uppercase;
}
button.act:hover{background:#2a1810;border-color:var(--gold)}
button.act.pri{background:linear-gradient(180deg,#6b1a2a,#4a1020);border-color:var(--gold)}
.live-status{margin-top:10px;padding:10px 12px;background:#12080c;border:1px solid var(--border);border-radius:4px;font-size:.84rem;color:var(--ivory);min-height:1.4em}
.demarrer-desc{font-size:.72rem;color:var(--muted);text-align:center;margin-top:8px;line-height:1.5}

/* Live table */
.live-table-wrap{margin-top:14px;border:1px solid var(--border);border-radius:5px;overflow:hidden;background:#12080c}
.live-table-head{display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:linear-gradient(180deg,#2a1218,#1a0c10);border-bottom:1px solid var(--border)}
.live-table-title{font-family:'Cinzel',serif;font-size:.7rem;letter-spacing:.14em;color:var(--gold)}
.live-table-badge{font-size:.62rem;padding:3px 8px;border-radius:999px;border:1px solid var(--gold-dim);color:var(--gold)}
.live-table-scroll{max-height:300px;overflow:auto}
.live-table{width:100%;border-collapse:collapse;font-size:.78rem;font-family:'Courier Prime',monospace}
.live-table th{position:sticky;top:0;background:#1a0e12;color:var(--gold-dim);text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);font-family:'Rajdhani',sans-serif;font-size:.62rem;letter-spacing:.08em;text-transform:uppercase}
.live-table td{padding:8px 10px;border-bottom:1px solid #2a181c}
.live-table tr:hover td{background:#1e1014}
.live-table tr.now td{background:#2a1810;border-left:3px solid var(--gold)}
.lt-num{color:var(--gold);font-weight:700}
.lt-ok{color:var(--ok);font-weight:700}.lt-ko{color:var(--bad);font-weight:700}.lt-pend{color:var(--gold-dim)}

.grid2{display:grid;grid-template-columns:1.15fr .85fr;gap:14px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.tablewrap{max-height:420px;overflow:auto}
.table{width:100%;border-collapse:collapse;font-size:.75rem}
.table th,.table td{padding:8px 10px;border-bottom:1px solid #2a181c;text-align:left;white-space:nowrap}
.table th{position:sticky;top:0;background:#1a0e12;color:var(--muted);font-size:.6rem;letter-spacing:.06em}
.status{padding:3px 7px;border-radius:999px;font-weight:800;font-size:.68rem}
.valid{background:#1a2a18;color:var(--ok)}.invalid{background:#2a1218;color:var(--bad)}.pending{background:#2a2010;color:var(--gold)}
.patterns{max-height:200px;overflow:auto}
.pattern{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #2a181c;font-family:'Courier Prime',monospace;font-size:.78rem}
.count{color:var(--gold);font-weight:700}
.out{margin-top:10px;padding:10px;background:#12080c;border:1px solid var(--border);border-radius:4px;font:11px 'Courier Prime',monospace;color:var(--muted);white-space:pre-wrap;min-height:48px}
.foot{text-align:center;color:#5a4048;font-size:.65rem;padding:18px 8px;letter-spacing:.08em}
</style></head><body>
<div class="shell">
<header>
  <div class="suits">♠️ ♥️ ♣️ ♦️</div>
  <h1>XCODE SUIT CARD</h1>
  <div class="sub">Prédiction enseigne JOUEUR · transitions · mémoire serveur</div>
  <div class="author">CANAL @statistika_baccara · STOCKAGE SERVEUR</div>
</header>

<!-- LIVE -->
<section class="panel gold-top">
  <div class="ph">
    <div><div class="ey">Connexion Telegram</div><div class="title">Flux live · @statistika_baccara</div></div>
    <span class="pill live-pill" id="live-badge"><span class="dot"></span>OFF</span>
  </div>
  <div class="pb">
    <button class="btn-demarrer off" id="btn-live" onclick="toggleLive()">🔴 DÉMARRER LIVE</button>
    <div class="demarrer-desc">
      Scan auto toutes les <b style="color:var(--gold)">8 s</b> · collecte légère + validation + nouvelle prédiction.<br>
      Focus : <b style="color:var(--gold)">1ʳᵉ carte JOUEUR</b> · formats 2-2 / 3-3 signalés.
    </div>
    <div class="live-status" id="live-status">LIVE arrêté — cliquez DÉMARRER LIVE pour synchroniser.</div>
    <div class="btn-row">
      <button class="act pri" onclick="collect(8)">Collecter 8</button>
      <button class="act" onclick="collect(15)">Collecter 15</button>
      <button class="act" onclick="collect(30)">Collecter 30</button>
      <button class="act" onclick="full()">Analyse</button>
      <button class="act" onclick="refreshAll()">Actualiser</button>
    </div>

    <div class="live-table-wrap">
      <div class="live-table-head">
        <span class="live-table-title">◆ SUIVI LIVE DES JEUX ◆</span>
        <span class="live-table-badge" id="lt-count">0</span>
      </div>
      <div class="live-table-scroll">
        <table class="live-table">
          <thead><tr><th>Jeu</th><th>P1</th><th>P2</th><th>Format</th><th>Préd.</th><th>Statut</th></tr></thead>
          <tbody id="lt-body"><tr><td colspan="6" style="text-align:center;color:var(--muted);padding:16px">En attente…</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>
</section>

<!-- HERO: dernier jeu + prédiction -->
<section class="hero">
  <div class="panel gold-top">
    <div class="ph">
      <div><div class="ey">Dernier jeu</div><div class="title">Flux reçu</div></div>
      <span class="pill" id="clock">—</span>
    </div>
    <div class="pb">
      <div class="livegrid">
        <div class="box">
          <div class="ey">PLAYER</div>
          <div class="num" id="gn">—</div>
          <div class="cards" id="pc">—</div>
          <div style="margin-top:10px" class="ey">Format</div><b id="fmt">—</b>
        </div>
        <div class="box">
          <div class="ey">BANKER</div>
          <div class="cards" id="bc" style="margin-top:8px">—</div>
          <div style="margin-top:12px" class="ey">Cadence</div><b>≈ 60 min / jeu</b>
        </div>
      </div>
      <div class="note" id="alert33">⚠ Main 3-3 — possible signal de changement d'algo.</div>
    </div>
  </div>

  <div class="panel gold-top">
    <div class="ph">
      <div><div class="ey">Décision auto</div><div class="title">Prochain jeu · enseigne JOUEUR</div></div>
      <span class="pill" id="strat">AUTO</span>
    </div>
    <div class="pb">
      <div class="pred-row">
        <div class="suit-big" id="ps">—</div>
        <div>
          <div class="ey">Enseigne prédite</div>
          <div class="pname" id="pn">En attente</div>
          <span class="pill" id="target">Cible —</span>
        </div>
      </div>
      <div class="metrics">
        <div class="metric"><span>Taux histo</span><b id="rate">—</b></div>
        <div class="metric"><span>Marge vs 25%</span><b id="margin">—</b></div>
        <div class="metric"><span>Échantillon</span><b id="sample">—</b></div>
        <div class="metric"><span>Confiance</span><b id="conf">—</b></div>
      </div>
      <div class="note" id="pnote"></div>
    </div>
  </div>
</section>

<section class="stats">
  <div class="stat"><span class="ey">Jeux</span><div class="val" id="total">—</div><div class="small">serveur</div></div>
  <div class="stat"><span class="ey">Prédictions</span><div class="val" id="preds">—</div><div class="small">historique</div></div>
  <div class="stat"><span class="ey">Validées</span><div class="val ok" id="valid">—</div><div class="small">vert</div></div>
  <div class="stat"><span class="ey">Invalidées</span><div class="val bad" id="invalid">—</div><div class="small">rouge</div></div>
  <div class="stat"><span class="ey">Schémas</span><div class="val" id="pcount">—</div><div class="small">récurrents</div></div>
</section>

<div class="grid2">
  <section class="panel gold-top">
    <div class="ph">
      <div><div class="ey">Mémoire serveur</div><div class="title">Historique des prédictions</div></div>
      <button class="act" onclick="copyPred()">⧉ Copier</button>
    </div>
    <div class="tablewrap">
      <table class="table">
        <thead><tr><th>Jeu</th><th>Préd.</th><th>Stratégie</th><th>Taux</th><th>Marge</th><th>Statut</th><th>Réel</th></tr></thead>
        <tbody id="hist"><tr><td colspan="7">Chargement…</td></tr></tbody>
      </table>
    </div>
  </section>

  <section class="panel gold-top">
    <div class="ph">
      <div><div class="ey">Exploration</div><div class="title">Schémas récurrents</div></div>
      <span class="pill">AUTO</span>
    </div>
    <div class="pb">
      <div class="patterns" id="patterns">Analyse…</div>
      <div class="out" id="out">Prédictions stockées côté serveur. Validation auto dès que le jeu cible arrive.</div>
    </div>
  </section>
</div>

<div class="foot">Xcode Suit Card · transitions historiques enseigne JOUEUR · pas de localStorage · taux non garantis</div>
</div>

<script>
const sm={H:'♥',D:'♦',S:'♠',C:'♣'};
const sn={H:'Cœur',D:'Carreau',S:'Pique',C:'Trèfle'};
const red=s=>s==='H'||s==='D';
const tx=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v??'—'};
const card=(lab,s)=>`<div class="card ${red(s)?'red':''}"><strong>${lab}</strong><small>${sm[s]||s}</small></div>`;
async function j(u,o){return(await fetch(u,o)).json()}

let liveOn=false, liveTimer=null, liveBusy=false;

function setLiveUI(on){
  const btn=document.getElementById('btn-live');
  const badge=document.getElementById('live-badge');
  if(on){
    btn.textContent='🟢 LIVE AUTO ACTIF';
    btn.classList.remove('off');
    badge.innerHTML='<span class="dot"></span>LIVE';
  }else{
    btn.textContent='🔴 DÉMARRER LIVE';
    btn.classList.add('off');
    badge.innerHTML='OFF';
  }
}

function toggleLive(){
  if(liveOn){
    liveOn=false;
    if(liveTimer){clearInterval(liveTimer);liveTimer=null}
    setLiveUI(false);
    tx('live-status','LIVE arrêté.');
    return;
  }
  liveOn=true;
  setLiveUI(true);
  tx('live-status','🟢 LIVE démarré — première collecte…');
  runLiveTick();
  liveTimer=setInterval(runLiveTick,8000);
}

async function runLiveTick(){
  if(liveBusy) return;
  liveBusy=true;
  try{
    tx('live-status','⟳ Collecte + recalibrage…');
    const d=await j('/api/collect?pages=4',{method:'POST'});
    await refreshAll();
    const msg=d.status==='ok'
      ? `✅ +${d.hands_new||0} nouvelles · ${d.hands_found||0} trouvées · stratégie à jour`
      : ('✕ '+(d.error||'erreur'));
    tx('live-status',msg);
  }catch(e){
    tx('live-status','✕ '+e.message);
  }finally{
    liveBusy=false;
  }
}

function fillLiveTable(hands, preds){
  const tbody=document.getElementById('lt-body');
  const cnt=document.getElementById('lt-count');
  if(!tbody) return;
  const predMap={};
  (preds||[]).forEach(p=>{predMap[p.target_n]=p});
  const rows=(hands||[]).slice().sort((a,b)=>b.n-a.n).slice(0,25);
  if(!rows.length){
    tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:16px">Aucune main — collectez ou démarrez LIVE.</td></tr>';
    if(cnt) cnt.textContent='0';
    return;
  }
  const latest=rows[0]&&rows[0].n;
  tbody.innerHTML=rows.map(h=>{
    const pSuits=(h.player_suits||'').split(',').filter(Boolean);
    const bSuits=(h.banker_suits||'').split(',').filter(Boolean);
    const fmt=h.format||((pSuits.length||'?')+'-'+(bSuits.length||'?'));
    const pred=predMap[h.n];
    let st='—', stCl='lt-pend', predSym='—';
    if(pred){
      predSym=sm[pred.prediction_suit]||pred.prediction_suit;
      if(pred.status==='VALID'){st='✅';stCl='lt-ok'}
      else if(pred.status==='INVALID'){st='❌';stCl='lt-ko'}
      else{st='⏳';stCl='lt-pend'}
    }
    const p1=pSuits.map(s=>sm[s]||s).join(' ')||'—';
    const p2=bSuits.map(s=>sm[s]||s).join(' ')||'—';
    return `<tr class="${h.n===latest?'now':''}">
      <td class="lt-num">#N${h.n}</td>
      <td>${p1}</td><td>${p2}</td>
      <td>${fmt}</td>
      <td>${predSym}</td>
      <td class="${stCl}">${st}</td>
    </tr>`;
  }).join('');
  if(cnt) cnt.textContent=rows.length+' jeux';
}

async function refreshAll(){
  try{
    const [l,h,p,st,hands]=await Promise.all([
      j('/api/live'),
      j('/api/predictions?limit=200'),
      j('/api/patterns?limit=20'),
      j('/api/stats/overview'),
      j('/api/hands?limit=30')
    ]);
    const x=l.latest;
    tx('clock',new Date().toLocaleTimeString());
    if(x){
      tx('gn','#N'+x.n);
      document.getElementById('pc').innerHTML=(x.player_suits||'').split(',').filter(Boolean).map((s,i)=>card('P'+(i+1),s)).join('')||'—';
      document.getElementById('bc').innerHTML=(x.banker_suits||'').split(',').filter(Boolean).map((s,i)=>card('B'+(i+1),s)).join('')||'—';
      tx('fmt',x.format||((x.player_card_count||'?')+'-'+(x.banker_card_count||'?')));
      const al=document.getElementById('alert33');
      if(al) al.style.display=x.is_33?'block':'none';
    }
    if(l.prediction){
      const q=l.prediction;
      tx('ps',q.symbol);
      tx('pn',(q.symbol||'')+' — '+(sn[q.suit]||q.suit||''));
      tx('target','Cible #N'+l.prediction_target_n);
      tx('strat',q.strategy||'AUTO');
      tx('rate',q.hit_rate+'%');
      tx('margin',(q.margin>=0?'+':'')+q.margin+' pts');
      tx('sample',q.sample);
      tx('conf',Math.round((q.confidence||0)*100)+'%');
      const pn=document.getElementById('pnote');
      if(q.note){pn.style.display='block';pn.textContent=q.note}else{pn.style.display='none'}
    }
    const ps=l.pred_stats||{};
    tx('total',st.total_hands);
    tx('preds',ps.total||h.length);
    tx('valid',ps.valid||h.filter(x=>x.status==='VALID').length);
    tx('invalid',ps.invalid||h.filter(x=>x.status==='INVALID').length);
    tx('pcount',p.length);
    document.getElementById('hist').innerHTML=h.map(x=>`<tr>
      <td>#${x.target_n}</td>
      <td><b>${sm[x.prediction_suit]||x.prediction_suit}</b></td>
      <td>${x.strategy||'AUTO'}</td>
      <td>${x.hit_rate}%</td>
      <td>${x.margin>=0?'+':''}${x.margin}</td>
      <td><span class="status ${x.status==='VALID'?'valid':x.status==='INVALID'?'invalid':'pending'}">${x.status}</span></td>
      <td>${sm[x.actual_first_suit]||'—'}</td>
    </tr>`).join('')||'<tr><td colspan="7">Aucune prédiction.</td></tr>';
    document.getElementById('patterns').innerHTML=p.map(x=>`<div class="pattern"><code>${x.pattern}</code><span class="count">×${x.occurrences}</span></div>`).join('')||'Aucun schéma.';
    fillLiveTable(hands, h);
  }catch(e){tx('out','Erreur : '+e)}
}

async function collect(n){
  tx('out','Collecte + validation + recalibrage…');
  try{
    const d=await j('/api/collect?pages='+n,{method:'POST'});
    document.getElementById('out').textContent=JSON.stringify(d,null,2);
    await refreshAll();
  }catch(e){tx('out','Erreur : '+e)}
}
async function full(){
  const d=await j('/api/analysis/full');
  document.getElementById('out').textContent=JSON.stringify(d,null,2);
  await refreshAll();
}
async function copyPred(){
  try{
    const h=await j('/api/predictions?limit=300');
    const t=h.map(x=>`#N${x.target_n} | ${sm[x.prediction_suit]||x.prediction_suit} | ${x.strategy||'AUTO'} | ${x.hit_rate}% | marge ${x.margin>=0?'+':''}${x.margin} | ${x.status} | reel ${sm[x.actual_first_suit]||'-'}`).join('
');
    await navigator.clipboard.writeText(t||'Aucune prediction');
    const b=document.querySelector('button.act');
    if(b){const o=b.textContent;b.textContent='Copie OK';setTimeout(()=>b.textContent=o,1200)}
  }catch(e){alert('Copie indisponible')}
} | ${sm[x.prediction_suit]||x.prediction_suit} | ${x.strategy||'AUTO'} | ${x.hit_rate}% | marge ${x.margin>=0?'+':''}${x.margin} | ${x.status} | réel ${sm[x.actual_first_suit]||'—'}`).join('\n');
    await navigator.clipboard.writeText(t||'Aucune prédiction');
    const b=document.querySelector('button.act');
    if(b){const o=b.textContent;b.textContent='Copié ✓';setTimeout(()=>b.textContent=o,1200)}
  }catch(e){alert('Copie indisponible')}
}

refreshAll();
setInterval(refreshAll,4000);
</script></body></html>
"""

# -------------------------------------------------
# Background auto-collect (optionnel)
# -------------------------------------------------
def auto_collect_loop():
    if AUTO_COLLECT_INTERVAL <= 0:
        return
    while True:
        time.sleep(AUTO_COLLECT_INTERVAL)
        try:
            print("[AUTO] Collecte légère…")
            parsed = collect(pages=4, delay=0.35)
            new = upsert_hands(parsed)
            if new:
                hands = get_all_hands()
                record_patterns(hands)
                validate_predictions()
                report = analyze(hands)
                pred, latest = pick_best_prediction(hands, report)
                if pred and latest:
                    upsert_prediction(
                        latest["n"] + 1, pred, latest["n"],
                        latest.get("player_first_suit"), pred.get("note"),
                    )
                print(f"[AUTO] +{new} nouvelles mains — prédiction mise à jour")
        except Exception as e:
            print(f"[AUTO] Erreur: {e}")

# -------------------------------------------------
# Main
# -------------------------------------------------
if __name__ == "__main__":
    init_db()
    print(f"DB initialisée : {DB_PATH}")
    print(f"Démarrage du serveur sur http://0.0.0.0:{PORT}")
    print("Ouvre http://localhost:8000 dans ton navigateur")
    if AUTO_COLLECT_INTERVAL > 0:
        t = threading.Thread(target=auto_collect_loop, daemon=True)
        t.start()
        print(f"Auto-collect activé toutes les {AUTO_COLLECT_INTERVAL}s")
    try:
        server = HTTPServer(("0.0.0.0", PORT), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
        server.server_close()
