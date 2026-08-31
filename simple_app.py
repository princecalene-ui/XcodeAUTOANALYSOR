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
from http.server import HTTPServer,
BaseHTTPRequestHandler
from urllib.parse import urlparse, parse
_qs
import threading
import requests
from bs4 import BeautifulSoup
# -------------------------------------------------
# Config
# -------------------------------------------------
BASE
_
DIR = os.path.dirname(os.path.abspath(__
file
__))
DB
_
PATH = os.path.join(BASE
_
DIR,
"baccarat.db")
CHANNEL
_
WEB = "https://t.me/s/statistika
baccara"
_
HEADERS = {
"User-Agent": "Mozilla/5.0 (Windows NT 10.0;
Win64; x64) AppleWebKit/537.36"
}
PORT = int(os.environ.get("PORT"
, 8000))
# -------------------------------------------------
# Parser
# -------------------------------------------------
SUIT
_
MAP = {
"♥": "H"
,
"♥": "H"
,
"♦": "D"
,
"♠": "S"
,
"♠": "S"
,
"♣": "C"
,
}
COLOR
_
MAP = {"H": "R"
,
"D": "R"
,
RANK
_
VALUE = {
"A": 1,
"2": 2,
"3": 3,
"4": 4,
"6": 6,
"7": 7,
"8": 8,
"9": 9,
"10": 0,
"T": 0,
"J": 0,
"Q": 0,
"♦": "D"
,
"♣": "C"
,
"S": "B"
,
"5": 5,
"K": 0,
"C": "B"}
}
HAND
_
RE = re.compile(
r"#N(\d+)\s*\.\s*(\d+)\s*\(([^)]+)\)\s*-\s*
(\d+)\s*\(([^)]+)\)\s*(#T\d+)?\s*(#R)?"
,
re.I,
)
CARD
_
RE = re.compile(r"(A|10|[2-9JQKT])\s*
([♥♦♦♠♣♣♥♠])"
, re.I)
def parse
_
cards(s):
cards = []
for rank, suit
char in CARD
_
_
RE.findall(s):
rank = rank.upper()
if rank == "T":
rank = "10"
suit = SUIT
_
MAP.get(suit
_
char)
if not suit:
continue
cards.append({
"rank": rank,
"suit": suit,
"color": COLOR
"value": RANK
_
MAP[suit],
_
VALUE.get(rank, 0),
})
return cards
def parse
_
message(text, msg_
id=None):
m = HAND
_
RE.search(text.strip())
if not m:
return None
n, ps, pc, bs, bc, t
_
tag, r
_
tag = m.groups()
p_
cards = parse
_
b
_
cards = parse
_
cards(pc)
cards(bc)
if not p_
cards and not b
_
return None
return {
"n": int(n),
"player
"banker
_
score": int(ps),
_
score": int(bs),
"player
_
cards": p_
cards,
"banker
cards": b
_
_
cards,
"t
_
tag": t
_
tag,
"is
_
r": bool(r
_
tag),
"message
_
id": msg_
id,
"raw": text.strip(),
cards:
}
# -------------------------------------------------
# Database
# -------------------------------------------------
def get
_
conn():
os.makedirs(os.path.dirname(DB
_
exist
_
ok=True)
conn = sqlite3.connect(DB
_
PATH)
conn.row
_
factory = sqlite3.Row
PATH),
return conn
def init
_
db():
conn = get
_
conn()
conn.executescript("""
CREATE TABLE IF NOT EXISTS hands (
id INTEGER PRIMARY KEY AUTOINCREMENT,
n INTEGER UNIQUE NOT NULL,
player
_
banker
_
_
score INTEGER,
score INTEGER,
suits TEXT,
player
banker
player
banker
_
suits TEXT,
first
_
_
suit TEXT,
first
_
_
suit TEXT,
player
banker
first
_
_
first
_
_
color TEXT,
color TEXT,
player
banker
first
_
_
first
_
_
val INTEGER,
val INTEGER,
player
player
banker
_
red INTEGER DEFAULT 0,
_
black INTEGER DEFAULT 0,
_
red INTEGER DEFAULT 0,
banker
_
black INTEGER DEFAULT 0,
t
tag TEXT,
_
is
_
r INTEGER DEFAULT 0,
player
banker
_
_
card
_
count INTEGER,
card
_
count INTEGER,
id INTEGER,
message
_
collected
_
at TEXT,
source TEXT DEFAULT 'web'
);
CREATE INDEX IF NOT EXISTS idx
_
n ON hands(n);
CREATE INDEX IF NOT EXISTS idx
_pfs ON
hands(player
first
_
_
suit);
CREATE INDEX IF NOT EXISTS idx
_
bfs ON
hands(banker
first
_
_
suit);
CREATE TABLE IF NOT EXISTS collection
_
logs (
id INTEGER PRIMARY KEY AUTOINCREMENT,
ts TEXT,
hands
_
hands
_
found INTEGER,
new INTEGER,
status TEXT,
error TEXT
);
""")
conn.commit()
conn.close()
def upsert
_
hands(parsed
_
list):
conn = get
_
conn()
existing = {r[0] for r in conn.execute("SELECT n
FROM hands").fetchall()}
new = 0
for h in parsed
list:
_
if h["n"] in existing:
continue
p = h["player
_
cards"]
banker
b = h["banker
_
cards"]
conn.execute("""
first
_
_
INSERT INTO hands (
n, player
_
score, banker
_
score,
player
suits, banker
_
_
suits,
player
first
_
_
_
_
player
first
_
_
suit, banker
color,
first
color,
suit,
player
player
first
_
_
val, banker
first
_
_
val,
_
red, player
_
black, banker
_
red,
banker
_
black,
t
_
tag, is
_
r, player
card
_
_
count,
banker
card
_
_
count,
message
id, collected
_
_
at, source
) VALUES
(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
h["banker
"""
, (
h["n"], h["player
_
score"],
_
score"],
"
"
,
.join(c["suit"] for c in p),
"
"
,
.join(c["suit"] for c in b),
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
h["t
_
tag"], 1 if h["is
_
r"] else 0,
len(p), len(b),
h.get("message
_
id"),
datetime.utcnow().isoformat(),
"web"
,
))
new += 1
conn.commit()
conn.close()
return new
def get
_
stats():
conn = get
_
conn()
total = conn.execute("SELECT COUNT(*) FROM
hands").fetchone()[0]
oldest = conn.execute("SELECT MIN(n) FROM
hands").fetchone()[0]
latest = conn.execute("SELECT MAX(n) FROM
hands").fetchone()[0]
conn.close()
return {"total
_
hands": total,
"oldest
_
"latest
_
n": latest}
n": oldest,
def get
all
_
_
hands():
conn = get
_
conn()
rows = conn.execute("SELECT * FROM hands ORDER BY
n ASC").fetchall()
conn.close()
return [dict(r) for r in rows]
# -------------------------------------------------
# Collector
# -------------------------------------------------
def fetch
_page(before=None):
url = CHANNEL
WEB
_
if before:
url += f"?before={before}"
r = requests.get(url, headers=HEADERS, timeout=20)
r.raise
for
_
_
status()
soup = BeautifulSoup(r.text,
"html.parser")
messages = []
ids = []
for w in soup.select("
.tgme
_
widget
_
message"):
post = w.get("data-post"
,
"")
mid = None
if post and "/" in post:
try:
mid = int(post.split("/")[-1])
ids.append(mid)
except ValueError:
pass
te = w.select
_
one("
if te:
.tgme
_
widget
_
message
_
text")
text = te.get
_
text(separator=" "
,
strip=True)
if text and "#N" in text[:40]:
messages.append({"id": mid,
"text":
text})
return messages, (min(ids) if ids else None)
def collect(pages=10, delay=0.45):
all
_parsed = []
seen = set()
before = None
for i in range(pages):
try:
raw, min
id = fetch
_
_page(before)
except Exception as e:
print(f"Erreur page {i+1}: {e}")
break
for msg in raw:
p = parse
_
message(msg["text"], msg["id"])
if p and p["n"] not in seen:
seen.add(p["n"])
all
_parsed.append(p)
before = min
id
_
print(f" Page {i+1}/{pages} → +{len(raw)}
msgs, total uniques={len(all
_parsed)}")
if i < pages - 1:
time.sleep(delay)
all
_parsed.sort(key=lambda x: x["n"])
return all
_parsed
# -------------------------------------------------
# Analyzer
# -------------------------------------------------
SUITS = ["H"
"D"
,
,
"S"
,
"C"]
EMOJI = {"H": "♥"
,
"D": "♦"
,
"S": "♠"
,
"C": "♣"}
def analyze(hands):
if not hands:
return {"error": "Aucune donnée"}
# Fréquences
p_
first = Counter()
b
_
first = Counter()
for h in hands:
if h["player
first
_
_
suit"]:
p_
first[h["player
first
_
if h["banker
first
_
_
suit"]:
_
b
_
first[h["banker
first
_
_
suit"]] += 1
suit"]] += 1
def pct(c):
t = sum(c.values()) or 1
return {s: round(100 * c[s] / t, 2) for s in
SUITS}
# Transitions
def transitions(side):
key = "player
first
_
_
suit" if side == "player"
else "banker
first
suit"
_
_
counts = defaultdict(lambda: defaultdict(int))
for i in range(len(hands) - 1):
s1 = hands[i].get(key)
s2 = hands[i + 1].get(key)
if s1 and s2:
counts[s1][s2] += 1
matrix = {}
for s1 in SUITS:
total = sum(counts[s1].values()) or 1
matrix[s1] = {s2: round(100 * counts[s1]
[s2] / total, 2) for s2 in SUITS}
return matrix, {k: dict(v) for k, v in
counts.items()}
tp, tp_
c = transitions("player")
tb, tb
_
c = transitions("banker")
# Stratégies
strategies = []
for side, matrix, counts in [("player"
, tp, tp_
c),
("banker"
, tb, tb
_
c)]:
for fs in SUITS:
total = sum(counts.get(fs, {}).values())
if total < 25:
continue
for ts in SUITS:
rate = matrix[fs][ts]
if rate >= 32:
strategies.append({
"name":
f"Trans
_{side[0].upper()}_{fs}_
to
_{ts}"
,
"description": (
f"Si 1ère carte {side}
précédente = {EMOJI[fs]},
"
f"alors suivante ≈ {rate}%
{EMOJI[ts]}"
),
"side": side,
"from": fs,
"to": ts,
"hit
_
rate": rate,
"sample": total,
"confidence": round(min(rate /
50, 1) * min(total, 150) / 150, 3),
})
strategies.sort(key=lambda x: x["confidence"],
reverse=True)
return {
"n
_
hands": len(hands),
"n
_
range": {"min": hands[0]["n"],
hands[-1]["n"]},
"player
"banker
_
first": pct(p_
first),
_
first": pct(b
_
first),
"transitions
_player": tp,
"transitions
_
banker": tb,
"strategies": strategies[:15],
"strategies
_
count": len(strategies),
"max":
}
# -------------------------------------------------
# HTTP Handler
# -------------------------------------------------
class Handler(BaseHTTPRequestHandler):
def log_
message(self, fmt,
*args):
print(f"
[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")
def send
_json(self, data, code=200):
body = json.dumps(data, ensure
_
ascii=False,
indent=2).encode("utf-8")
self.send
_
response(code)
self.send
_
header("Content-Type"
,
"application/json; charset=utf-8")
self.send
_
header("Access-Control-Allow-
Origin"
,
"*")
self.send
_
header("Content-Length"
self.end
_
headers()
self.wfile.write(body)
, len(body))
def send
_
html(self, html):
body = html.encode("utf-8")
self.send
_
response(200)
self.send
_
header("Content-Type"
,
charset=utf-8")
self.send
_
header("Content-Length"
self.end
_
headers()
self.wfile.write(body)
"text/html;
, len(body))
def do
_
GET(self):
path = urlparse(self.path).path
qs = parse
_qs(urlparse(self.path).query)
if path == "/" or path == "/index.html":
stats = get
_
stats()
html = DASHBOARD
html = html.replace("{total
_
hands}"
,
str(stats["total
_
hands"]))
html = html.replace("{oldest
_
n}"
,
str(stats["oldest
_
n"] if stats["oldest
_
n"] is not None
else "
—
"))
html = html.replace("{latest
_
n}"
,
str(stats["latest
_
n"] if stats["latest
_
n"] is not None
else "
—
"))
self.send
_
html(html)
elif path == "/api/stats/overview":
self.send
_json(get
_
stats())
elif path == "/api/analysis/full":
hands = get
all
_
_
hands()
self.send
_json(analyze(hands))
elif path == "/api/analysis/strategies":
hands = get
all
_
_
hands()
report = analyze(hands)
self.send
_json({
"count":
report.get("strategies
_
count"
, 0),
"strategies": report.get("strategies"
,
[]),
player
})
elif path == "/api/hands":
limit = int(qs.get("limit"
offset = int(qs.get("offset"
, [50])[0])
, [0])[0])
conn = get
_
conn()
rows = conn.execute(
"SELECT n, player
_
score, banker
_
_
suits, banker
_
suits,
"
"player
first
suit, banker
first
_
_
_
_
score,
suit,
t
_
tag, is
_
r "
"FROM hands ORDER BY n DESC LIMIT ?
OFFSET ?"
,
(limit, offset),
).fetchall()
conn.close()
self.send
_json([dict(r) for r in rows])
else:
self.send
_json({"error": "Not found"},
404)
def do
_
POST(self):
path = urlparse(self.path).path
qs = parse
_qs(urlparse(self.path).query)
if path == "/api/collect":
pages = int(qs.get("pages"
try:
, [10])[0])
print(f"=== Collecte de {pages} pages
===")
parsed = collect(pages=pages)
new = upsert
_
hands(parsed)
conn = get
_
conn()
conn.execute(
"INSERT INTO collection
_
logs (ts,
hands
_
found, hands
_
new, status) VALUES (?,?,?,?)"
,
(datetime.utcnow().isoformat(),
len(parsed), new,
"success"),
)
conn.commit()
conn.close()
self.send
_json({
"status": "ok"
,
"hands
_
found": len(parsed),
"hands
_
new": new,
"message": f"{new} nouvelles mains
enregistrées"
,
})
except Exception as e:
self.send
_json({"status": "error"
"error": str(e)}, 500)
else:
,
self.send
_json({"error": "Not found"},
404)
DASHBOARD = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,
initial-scale=1">
<title>Xcode SUIT CARD STRATÉGIE CREATOR</title>
<style>
:root {{ --bg:#0f1419; --card:#1a2332; --
accent:#3b82f6; --green:#22c55e; --text:#e2e8f0; --
muted:#94a3b8; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:system-ui,sans-serif;
background:var(--bg); color:var(--text); padding:2rem;
min-height:100vh; }}
h1 {{ font-size:1.7rem; margin-bottom:.4rem; }}
.sub {{ color:var(--muted); margin-bottom:2rem; }}
.grid {{ display:grid; grid-template-
columns:repeat(auto-fit,minmax(200px,1fr)); gap:1rem;
margin-bottom:2rem; }}
.card {{ background:var(--card); border-radius:12px;
padding:1.2rem; border:1px solid #2d3748; }}
.card h3 {{ font-size:.8rem; color:var(--muted); text-
transform:uppercase; letter-spacing:.05em; margin-
bottom:.4rem; }}
.card .v {{ font-size:1.9rem; font-weight:700;
color:var(--green); }}
.actions {{ display:flex; gap:.8rem; flex-wrap:wrap;
margin-bottom:1.5rem; }}
button {{ background:var(--accent); color:#fff;
border:none; padding:.7rem 1.3rem; border-radius:8px;
font-size:.95rem; cursor:pointer; }}
button.sec {{ background:#475569; }}
button:hover {{ opacity:.9; }}
#out {{ background:var(--card); border-radius:12px;
padding:1.3rem; min-height:140px; font-family:ui-
monospace,monospace; font-size:.88rem; white-
space:pre-wrap; border:1px solid #2d3748; }}
.foot {{ margin-top:2.5rem; color:var(--muted); font-
size:.82rem; }}
</style>
</head>
<body>
<h1>♠ Xcode SUIT CARD STRATÉGIE CREATOR</h1>
<p class="sub">Site indépendant auto-apprenant —
Collecte + Analyse + Stratégies Baccarat</p>
<div class="grid">
<div class="card"><h3>Mains en base</h3><div
class="v" id="total">{total
_
hands}</div></div>
<div class="card"><h3>Plus ancien #N</h3><div
class="v" id="oldest">{oldest
_
n or "
—
"}</div></div>
<div class="card"><h3>Plus récent #N</h3><div
class="v" id="latest">{latest
n or "
—
_
"}</div></div>
</div>
<div class="actions">
<button onclick="go(10)">Collecter 10 pages</button>
<button class="sec" onclick="go(25)">Collecter 25
pages (historique)</button>
<button class="sec" onclick="analyse()">Lancer
l'analyse</button>
<button class="sec" onclick="strats()">Voir
stratégies</button>
</div>
<div id="out">Prêt. Cliquez sur « Collecter » pour
démarrer la collecte depuis le canal Telegram.</div>
<div class="foot">Source : t.me/statistika
_
Système auto-renforçant · Port {port}</div>
baccara ·
<script>
async function go(pages) {{
const el = document.getElementById('out');
el.textContent = 'Collecte en cours (' + pages + '
pages)... Cela peut prendre 15-40 secondes.
';
try {{
const r = await fetch('/api/collect?pages=' +
pages, {{method:'POST'}});
const d = await r.json();
el.textContent = JSON.stringify(d, null, 2);
const ov = await (await
fetch('/api/stats/overview')).json();
document.getElementById('total').textContent =
ov.total
_
hands;
document.getElementById('oldest').textContent =
ov.oldest
_
n || '
—
document.getElementById('latest').textContent =
ov.latest
_
n || '
—
';
';
}} catch(e) {{ el.textContent = 'Erreur: ' + e; }}
}}
async function analyse() {{
const el = document.getElementById('out');
el.textContent = 'Analyse en cours...
';
try {{
const d = await (await
fetch('/api/analysis/full')).json();
let t = '=== ANALYSE COMPLÈTE ===\\n';
t += 'Mains : ' + d.n
_
hands + ' | Range #N : ' +
d.n
_
range.min + ' → ' + d.n
_
range.max + '\\n\\n';
t += '1ère carte Player : ' +
JSON.stringify(d.player
_
first) + '\\n';
t += '1ère carte Banker : ' +
JSON.stringify(d.banker
_
first) + '\\n\\n';
t += 'Stratégies détectées : ' +
d.strategies
_
count + '\\n\\n';
t += 'TOP STRATÉGIES :\\n';
(d.strategies||[]).slice(0,8).forEach((s,i) => {{
t += (i+1) + '
. [' + s.hit
_
rate + '% | n=' +
s.sample + ' | conf=' + s.confidence + ']\\n';
t += ' ' + s.description + '\\n';
}});
el.textContent = t;
}} catch(e) {{ el.textContent = 'Erreur: ' + e; }}
}}
async function strats() {{
const el = document.getElementById('out');
el.textContent = 'Chargement stratégies...
';
try {{
const d = await (await
fetch('/api/analysis/strategies')).json();
el.textContent = JSON.stringify(d, null, 2);
}} catch(e) {{ el.textContent = 'Erreur: ' + e; }}
}}
</script>
</body>
</html>
"""
.replace("{port}"
, str(PORT))
# -------------------------------------------------
# Main
# -------------------------------------------------
if
name
== "
main
":
__
__
__
__
init
_
db()
print(f"DB initialisée : {DB
_
PATH}")
print(f"Démarrage du serveur sur http://0.0.0.0:
{PORT}")
print("Ouvre http://localhost:8000 dans ton
navigateur")
try:
server = HTTPServer(("0.0.0.0"
, PORT), Handler)
server.serve
_
forever()
except KeyboardInterrupt:
print("\nArrêt.
")
server.server
_
close()