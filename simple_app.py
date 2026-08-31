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

        elif path == "/api/live":
            hands = get_all_hands()
            report = analyze(hands)
            latest = hands[-1] if hands else None
            live = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "latest": latest,
                "prediction": None,
                "strategy": None,
                "margin": None,
            }
            if latest and report.get("strategies"):
                applicable = [
                    x for x in report["strategies"]
                    if x["side"] == "player"
                    and x["from"] == latest.get("player_first_suit")
                ]
                if not applicable:
                    applicable = [
                        x for x in report["strategies"]
                        if x["side"] == "banker"
                        and x["from"] == latest.get("banker_first_suit")
                    ]
                if applicable:
                    applicable.sort(
                        key=lambda x: (x["confidence"], x["hit_rate"], x["sample"]),
                        reverse=True
                    )
                    best = applicable[0]
                    live["prediction"] = {
                        "suit": best["to"],
                        "symbol": EMOJI[best["to"]],
                        "hit_rate": best["hit_rate"],
                        "confidence": best["confidence"],
                        "sample": best["sample"],
                    }
                    live["strategy"] = best["name"]
                    live["margin"] = round(best["hit_rate"] - 25.0, 2)
            self.send_json(live)

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
<title>Xcode Suit Card — Live Strategy</title>
<style>
:root{--bg:#07111f;--panel:#0d1b2d;--line:#1d3552;--text:#f4f7fb;--muted:#8fa6bf;--accent:#4f8cff;--ok:#34d399;--warn:#fbbf24;--shadow:0 18px 50px rgba(0,0,0,.25)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0%,#102b49 0,#07111f 42%,#050b14 100%);color:var(--text);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
.shell{max-width:1280px;margin:auto;padding:26px}.top{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:14px}.logo{width:48px;height:48px;border-radius:14px;background:linear-gradient(135deg,#4f8cff,#7c5cff);display:grid;place-items:center;font-size:25px;box-shadow:0 10px 30px rgba(79,140,255,.28)}
h1{font-size:clamp(1.35rem,3vw,2rem);margin:0;letter-spacing:-.03em}.sub{color:var(--muted);margin:4px 0 0;font-size:.9rem}
.live-pill{display:flex;align-items:center;gap:8px;border:1px solid #1e4b43;background:#0b2724;color:#8ff0d0;padding:8px 12px;border-radius:999px;font-size:.78rem;font-weight:700}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 14px var(--ok);animation:pulse 1.5s infinite}@keyframes pulse{50%{opacity:.35;transform:scale(.75)}}
.hero{display:grid;grid-template-columns:1.45fr .85fr;gap:18px;margin-bottom:18px}.panel{background:linear-gradient(180deg,rgba(16,36,59,.96),rgba(9,23,39,.96));border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);overflow:hidden}
.panel-head{display:flex;align-items:center;justify-content:space-between;padding:18px 20px;border-bottom:1px solid var(--line)}.panel-title{font-weight:800;font-size:.96rem}.eyebrow{color:var(--muted);font-size:.7rem;text-transform:uppercase;letter-spacing:.12em}
.live-main{padding:24px 20px;display:grid;grid-template-columns:1fr 1fr;gap:18px}.game-box,.prediction-box{border:1px solid var(--line);border-radius:16px;padding:18px;background:rgba(4,13,24,.35)}
.label{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px}.game-number{font-size:2rem;font-weight:900}.cards{display:flex;gap:9px;flex-wrap:wrap;margin-top:13px}
.card-chip{min-width:56px;height:70px;border-radius:12px;background:#f8fafc;color:#101827;display:flex;flex-direction:column;align-items:center;justify-content:center;font-weight:900;box-shadow:0 7px 18px rgba(0,0,0,.25)}.card-chip small{font-size:1.1rem}.red{color:#e11d48}.black{color:#111827}
.pred{display:flex;align-items:center;gap:15px}.suit{font-size:4.2rem;line-height:1}.pred-name{font-size:1.5rem;font-weight:900}.metric{margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:10px}
.metric div{background:#091625;border-radius:12px;padding:11px}.metric b{display:block;font-size:1.15rem;margin-top:3px}.metric span{font-size:.68rem;color:var(--muted);text-transform:uppercase}.margin{color:var(--ok)!important}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}.stat{padding:18px 20px}.stat .value{font-size:1.55rem;font-weight:900;margin-top:5px}.stat .small{font-size:.74rem;color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.actions{display:flex;gap:9px;flex-wrap:wrap;padding:16px 20px}
button{border:0;border-radius:10px;padding:10px 14px;color:white;background:var(--accent);font-weight:750;cursor:pointer}button.sec{background:#1a304a;border:1px solid var(--line)}button:hover{filter:brightness(1.12)}
.table-wrap{overflow:auto}.table{width:100%;border-collapse:collapse;font-size:.84rem}.table th,.table td{text-align:left;padding:12px 14px;border-bottom:1px solid #172d45}.table th{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.badge{padding:4px 8px;border-radius:999px;background:#142e49;color:#bcd7f7;font-weight:700;font-size:.7rem}.out{padding:18px 20px;min-height:100px;white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.76rem;color:#b8c9dc}
.foot{color:#68809b;font-size:.73rem;text-align:center;padding:24px 0}@media(max-width:900px){.hero,.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.shell{padding:14px}.live-main{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}.top{align-items:flex-start}.live-pill{font-size:.68rem}}
</style>
</head>
<body>
<div class="shell">
<header class="top"><div class="brand"><div class="logo">♠</div><div><h1>Xcode Suit Card</h1><p class="sub">Console de stratégie — analyse historique + lecture du jeu courant</p></div></div><div class="live-pill"><span class="dot"></span> LIVE • actualisation automatique</div></header>

<section class="hero">
<div class="panel">
<div class="panel-head"><div><div class="eyebrow">Flux actuel</div><div class="panel-title">Jeu le plus récent détecté</div></div><span id="liveTime" class="badge">--:--:--</span></div>
<div class="live-main">
<div class="game-box"><div class="label">Main / manche</div><div id="gameNo" class="game-number">—</div><div class="cards" id="playerCards"><span class="badge">Chargement…</span></div><div class="label" style="margin-top:17px">Player</div></div>
<div class="game-box"><div class="label">Cartes Banker</div><div class="cards" id="bankerCards"><span class="badge">—</span></div><div class="label" style="margin-top:17px">Banker</div></div>
</div></div>

<div class="panel"><div class="panel-head"><div><div class="eyebrow">Stratégie active</div><div class="panel-title">Marge de prédiction</div></div><span id="strategyBadge" class="badge">En attente</span></div>
<div style="padding:24px 20px"><div class="pred"><div id="predSuit" class="suit">—</div><div><div class="label">Enseigne projetée</div><div id="predName" class="pred-name">Aucune donnée</div></div></div>
<div class="metric"><div><span>Taux historique</span><b id="hitRate">—</b></div><div><span>Marge vs 25%</span><b id="margin" class="margin">—</b></div><div><span>Échantillon</span><b id="sample">—</b></div><div><span>Confiance</span><b id="confidence">—</b></div></div></div></div>
</section>

<section class="stats">
<div class="panel stat"><div class="eyebrow">Mains en base</div><div id="total" class="value">{total_hands}</div><div class="small">données collectées</div></div>
<div class="panel stat"><div class="eyebrow">Plus ancien</div><div id="oldest" class="value">{oldest_n}</div><div class="small">numéro de jeu</div></div>
<div class="panel stat"><div class="eyebrow">Plus récent</div><div id="latest" class="value">{latest_n}</div><div class="small">numéro de jeu</div></div>
<div class="panel stat"><div class="eyebrow">Stratégies</div><div id="stratCount" class="value">—</div><div class="small">détectées automatiquement</div></div>
</section>

<div class="grid">
<section class="panel"><div class="panel-head"><div><div class="eyebrow">Contrôle</div><div class="panel-title">Collecte & analyse</div></div></div>
<div class="actions"><button onclick="go(10)">Collecter 10 pages</button><button class="sec" onclick="go(25)">Historique 25 pages</button><button class="sec" onclick="analyse()">Analyse complète</button><button class="sec" onclick="strats()">Stratégies</button></div>
<div id="out" class="out">Système prêt. La vue LIVE se met à jour automatiquement.</div></section>

<section class="panel"><div class="panel-head"><div><div class="eyebrow">Historique immédiat</div><div class="panel-title">Derniers jeux</div></div></div>
<div class="table-wrap"><table class="table"><thead><tr><th>#N</th><th>Player</th><th>Banker</th><th>P1</th><th>B1</th></tr></thead><tbody id="recent"><tr><td colspan="5">Chargement…</td></tr></tbody></table></div></section>
</div>
<div class="foot">Xcode SUIT CARD STRATÉGIE CREATOR · Les taux affichés sont historiques et ne garantissent pas le résultat d'un jeu futur.</div>
</div>

<script>
const suitMap={H:'♥',D:'♦',S:'♠',C:'♣'}, redSuit=s=>s==='H'||s==='D';
function cardHtml(rank,suit){return `<div class="card-chip ${redSuit(suit)?'red':'black'}"><strong>${rank}</strong><small>${suitMap[suit]||suit}</small></div>`}
function setText(id,v){document.getElementById(id).textContent=v??'—'}
async function refreshLive(){
 try{
  const d=await(await fetch('/api/live',{cache:'no-store'})).json(),h=d.latest;
  setText('liveTime',new Date().toLocaleTimeString());
  if(h){
   setText('gameNo','#N'+h.n);setText('latest',h.n);
   document.getElementById('playerCards').innerHTML=(h.player_suits||'').split(',').map((s,i)=>s?cardHtml('P'+(i+1),s):'').join('');
   document.getElementById('bankerCards').innerHTML=(h.banker_suits||'').split(',').map((s,i)=>s?cardHtml('B'+(i+1),s):'').join('');
  }
  if(d.prediction){
   setText('predSuit',d.prediction.symbol);setText('predName',d.prediction.symbol+' — enseigne proposée');
   setText('hitRate',d.prediction.hit_rate+'%');setText('margin',(d.margin>=0?'+':'')+d.margin+' pts');
   setText('sample',d.prediction.sample);setText('confidence',Math.round(d.prediction.confidence*100)+'%');setText('strategyBadge',d.strategy);
  }else{['predSuit','predName','hitRate','margin','sample','confidence'].forEach(id=>setText(id,'—'));setText('strategyBadge','En attente')}
  const rows=await(await fetch('/api/hands?limit=8',{cache:'no-store'})).json();
  document.getElementById('recent').innerHTML=rows.map(r=>`<tr><td><b>#${r.n}</b></td><td>${r.player_suits||'—'}</td><td>${r.banker_suits||'—'}</td><td><span class="badge">${suitMap[r.player_first_suit]||'—'}</span></td><td><span class="badge">${suitMap[r.banker_first_suit]||'—'}</span></td></tr>`).join('');
 }catch(e){console.error(e)}
}
async function refreshStats(){try{const ov=await(await fetch('/api/stats/overview',{cache:'no-store'})).json();setText('total',ov.total_hands);setText('oldest',ov.oldest_n||'—');setText('latest',ov.latest_n||'—');const st=await(await fetch('/api/analysis/strategies',{cache:'no-store'})).json();setText('stratCount',st.count)}catch(e){}}
async function go(pages){const el=document.getElementById('out');el.textContent='Collecte en cours…';try{const d=await(await fetch('/api/collect?pages='+pages,{method:'POST'})).json();el.textContent=JSON.stringify(d,null,2);await refreshStats();await refreshLive()}catch(e){el.textContent='Erreur : '+e}}
async function analyse(){const el=document.getElementById('out');el.textContent='Analyse en cours…';try{const d=await(await fetch('/api/analysis/full')).json();el.textContent=JSON.stringify(d,null,2)}catch(e){el.textContent='Erreur : '+e}}
async function strats(){const el=document.getElementById('out');el.textContent='Chargement des stratégies…';try{const d=await(await fetch('/api/analysis/strategies')).json();el.textContent=JSON.stringify(d,null,2)}catch(e){el.textContent='Erreur : '+e}}
refreshStats();refreshLive();setInterval(refreshLive,3000);setInterval(refreshStats,10000);
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
