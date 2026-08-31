#!/usr/bin/env python3
"""
Xcode SUIT CARD STRATÉGIE CREATOR
Version simple (stdlib + requests + bs4) — fonctionne
sans FastAPI
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

# -------------------------------------------------
# Parser
# -------------------------------------------------
SUIT_MAP = {
    "♥": "H",
    "♦": "D",
    "♠": "S",
    "♣": "C",
}
COLOR_MAP = {"H": "R", "D": "R", "S": "B", "C": "B"}
RANK_VALUE = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 0, "T": 0, "J": 0, "Q": 0, "K": 0,
}

HAND_RE = re.compile(
    r"#N(\d+)\s*\.\s*(\d+)\s*\(([^)]+)\)\s*-\s*"
    r"(\d+)\s*\(([^)]+)\)\s*(#T\d+)?\s*(#R)?",
    re.I,
)
CARD_RE = re.compile(r"(A|10|[2-9JQKT])\s*([♥♦♠♣])", re.I)

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
    }

# -------------------------------------------------
# Database
# -------------------------------------------------
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
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
        message_id INTEGER,
        collected_at TEXT,
        source TEXT DEFAULT 'web'
    );
    CREATE INDEX IF NOT EXISTS idx_n ON hands(n);
    CREATE INDEX IF NOT EXISTS idx_pfs ON hands(player_first_suit);
    CREATE INDEX IF NOT EXISTS idx_bfs ON hands(banker_first_suit);
    CREATE TABLE IF NOT EXISTS collection_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        hands_found INTEGER,
        hands_new INTEGER,
        status TEXT,
        error TEXT
    );
    """)
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
                message_id, collected_at, source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            len(p), len(b), h.get("message_id"),
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
    conn.close()
    return {"total_hands": total, "oldest_n": oldest, "latest_n": latest}

def get_all_hands():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM hands ORDER BY n ASC").fetchall()
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
            if text and "#N" in text[:40]:
                messages.append({"id": mid, "text": text})
    return messages, (min(ids) if ids else None)

def collect(pages=10, delay=0.45):
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
# Analyzer
# -------------------------------------------------
SUITS = ["H", "D", "S", "C"]
EMOJI = {"H": "♥", "D": "♦", "S": "♠", "C": "♣"}

def analyze(hands):
    if not hands:
        return {"error": "Aucune donnée"}

    p_first = Counter()
    b_first = Counter()
    for h in hands:
        if h["player_first_suit"]:
            p_first[h["player_first_suit"]] += 1
        if h["banker_first_suit"]:
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
            if total < 25:
                continue
            for ts in SUITS:
                rate = matrix[fs][ts]
                if rate >= 32:
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

    strategies.sort(key=lambda x: x["confidence"], reverse=True)
    return {
        "n_hands": len(hands),
        "n_range": {"min": hands[0]["n"], "max": hands[-1]["n"]},
        "player_first": pct(p_first),
        "banker_first": pct(b_first),
        "transitions_player": tp,
        "transitions_banker": tb,
        "strategies": strategies[:15],
        "strategies_count": len(strategies),
    }

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
                str(stats["oldest_n"] if stats["oldest_n"] is not None else "—")
            )
            html = html.replace(
                "{latest_n}",
                str(stats["latest_n"] if stats["latest_n"] is not None else "—")
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

        elif path == "/api/hands":
            limit = int(qs.get("limit", [50])[0])
            offset = int(qs.get("offset", [0])[0])
            conn = get_conn()
            rows = conn.execute(
                "SELECT n, player_score, banker_score, player_suits, banker_suits, "
                "player_first_suit, banker_first_suit, t_tag, is_r "
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
                    "message": f"{new} nouvelles mains enregistrées",
                })
            except Exception as e:
                self.send_json({"status": "error", "error": str(e)}, 500)
        else:
            self.send_json({"error": "Not found"}, 404)

DASHBOARD = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Xcode SUIT CARD STRATÉGIE CREATOR</title>
<style>
:root { --bg:#0f1419; --card:#1a2332; --accent:#3b82f6; --green:#22c55e; --text:#e2e8f0; --muted:#94a3b8; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:system-ui,sans-serif; background:var(--bg); color:var(--text); padding:2rem; min-height:100vh; }
h1 { font-size:1.7rem; margin-bottom:.4rem; }
.sub { color:var(--muted); margin-bottom:2rem; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:1rem; margin-bottom:2rem; }
.card { background:var(--card); border-radius:12px; padding:1.2rem; border:1px solid #2d3748; }
.card h3 { font-size:.8rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; margin-bottom:.4rem; }
.card .v { font-size:1.9rem; font-weight:700; color:var(--green); }
.actions { display:flex; gap:.8rem; flex-wrap:wrap; margin-bottom:1.5rem; }
button { background:var(--accent); color:#fff; border:none; padding:.7rem 1.3rem; border-radius:8px; font-size:.95rem; cursor:pointer; }
button.sec { background:#475569; }
button:hover { opacity:.9; }
#out { background:var(--card); border-radius:12px; padding:1.3rem; min-height:140px; font-family:ui-monospace,monospace; font-size:.88rem; white-space:pre-wrap; border:1px solid #2d3748; }
.foot { margin-top:2.5rem; color:var(--muted); font-size:.82rem; }
</style>
</head>
<body>
<h1>♠ Xcode SUIT CARD STRATÉGIE CREATOR</h1>
<p class="sub">Site indépendant auto-apprenant — Collecte + Analyse + Stratégies Baccarat</p>
<div class="grid">
<div class="card"><h3>Mains en base</h3><div class="v" id="total">{total_hands}</div></div>
<div class="card"><h3>Plus ancien #N</h3><div class="v" id="oldest">{oldest_n}</div></div>
<div class="card"><h3>Plus récent #N</h3><div class="v" id="latest">{latest_n}</div></div>
</div>
<div class="actions">
<button onclick="go(10)">Collecter 10 pages</button>
<button class="sec" onclick="go(25)">Collecter 25 pages (historique)</button>
<button class="sec" onclick="analyse()">Lancer l'analyse</button>
<button class="sec" onclick="strats()">Voir stratégies</button>
</div>
<div id="out">Prêt. Cliquez sur « Collecter » pour démarrer la collecte depuis le canal Telegram.</div>
<div class="foot">Source : t.me/statistika_baccara · Système auto-renforçant · Port {port}</div>
<script>
async function go(pages) {{
const el = document.getElementById('out');
el.textContent = 'Collecte en cours (' + pages + ' pages)... Cela peut prendre 15-40 secondes.';
try {{
const r = await fetch('/api/collect?pages=' + pages, {{method:'POST'}});
const d = await r.json();
el.textContent = JSON.stringify(d, null, 2);
const ov = await (await fetch('/api/stats/overview')).json();
document.getElementById('total').textContent = ov.total_hands;
document.getElementById('oldest').textContent = ov.oldest_n || '—';
document.getElementById('latest').textContent = ov.latest_n || '—';
}} catch(e) {{ el.textContent = 'Erreur: ' + e; }}
}}
async function analyse() {{
const el = document.getElementById('out');
el.textContent = 'Analyse en cours...';
try {{
const d = await (await fetch('/api/analysis/full')).json();
let t = '=== ANALYSE COMPLÈTE ===\\n';
t += 'Mains : ' + d.n_hands + ' | Range #N : ' + d.n_range.min + ' → ' + d.n_range.max + '\\n\\n';
t += '1ère carte Player : ' + JSON.stringify(d.player_first) + '\\n';
t += '1ère carte Banker : ' + JSON.stringify(d.banker_first) + '\\n\\n';
t += 'Stratégies détectées : ' + d.strategies_count + '\\n\\n';
t += 'TOP STRATÉGIES:\\n';
(d.strategies||[]).slice(0,8).forEach((s,i) => {{
t += (i+1) + '. [' + s.hit_rate + '% | n=' + s.sample + ' | conf=' + s.confidence + ']\\n';
t += ' ' + s.description + '\\n';
}});
el.textContent = t;
}} catch(e) {{ el.textContent = 'Erreur: ' + e; }}
}}
async function strats() {{
const el = document.getElementById('out');
el.textContent = 'Chargement stratégies...';
try {{
const d = await (await fetch('/api/analysis/strategies')).json();
el.textContent = JSON.stringify(d, null, 2);
}} catch(e) {{ el.textContent = 'Erreur: ' + e; }}
}}
</script>
</body>
</html>
""".replace("{port}", str(PORT))

# Main
if __name__ == "__main__":
    init_db()
    print(f"DB initialisée : {DB_PATH}")
    print(f"Démarrage du serveur sur http://0.0.0.0:{PORT}")
    print("Ouvre http://localhost:8000 dans ton navigateur")
    try:
        server = HTTPServer(("0.0.0.0", PORT), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
        server.server_close()
