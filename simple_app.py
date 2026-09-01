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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "baccarat.db")
CHANNEL_WEB = "https://t.me/s/statistika_baccara"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
PORT = int(os.environ.get("PORT", 8000))
AUTO_COLLECT_INTERVAL = 90
MIN_SAMPLE, MIN_RATE, DEACTIVATE_RATE, MIN_VALIDATIONS = 20, 30.0, 22.0, 8
SUITS = ["H", "D", "S", "C"]
EMOJI = {"H": "♥", "D": "♦", "S": "♠", "C": "♣"}
SUIT_NAME = {"H": "Coeur", "D": "Carreau", "S": "Pique", "C": "Trefle"}
SUIT_MAP = {"♥":"H","♦":"D","♠":"S","♣":"C","♥️":"H","♦️":"D","♠️":"S","♣️":"C"}
COLOR_MAP = {"H":"R","D":"R","S":"B","C":"B"}
RANK_VALUE = {"A":1,"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":0,"T":0,"J":0,"Q":0,"K":0}
HAND_RE = re.compile(r"#N(\d+)\s*\.\s*(\d+)\s*\(([^)]+)\)\s*-\s*(\d+)\s*\(([^)]+)\)\s*(#T\d+)?\s*(#R)?", re.I)
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
    m=HAND_RE.search(text.strip())
    if not m: return None
    n,ps,pc,bs,bc,t_tag,r_tag=m.groups()
    p,b=parse_cards(pc),parse_cards(bc)
    if not p and not b: return None
    return {"n":int(n),"player_score":int(ps),"banker_score":int(bs),"player_cards":p,"banker_cards":b,
            "t_tag":t_tag,"is_r":bool(r_tag),"message_id":msg_id,"raw":text.strip(),
            "format":f"{len(p)}-{len(b)}","is_33":len(p)==3 and len(b)==3,"is_22":len(p)==2 and len(b)==2,"player_drew_3":len(p)==3}

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c

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
        for col,typ in [("format","TEXT"),("is_33","INTEGER DEFAULT 0"),("is_22","INTEGER DEFAULT 0"),("player_drew_3","INTEGER DEFAULT 0")]:
            if col not in cols: c.execute(f"ALTER TABLE hands ADD COLUMN {col} {typ}")
        pcols={r[1] for r in c.execute("PRAGMA table_info(predictions)")}
        if "strategy_id" not in pcols: c.execute("ALTER TABLE predictions ADD COLUMN strategy_id INTEGER")
        if "note" not in pcols: c.execute("ALTER TABLE predictions ADD COLUMN note TEXT")
    except Exception: pass
    c.commit(); c.close()

def upsert_hands(parsed_list):
    c=get_conn(); existing={r[0] for r in c.execute("SELECT n FROM hands")}; new=0
    for h in parsed_list:
        if h["n"] in existing: continue
        p,b=h["player_cards"],h["banker_cards"]
        c.execute("""INSERT INTO hands (n,player_score,banker_score,player_suits,banker_suits,
            player_first_suit,banker_first_suit,player_first_color,banker_first_color,
            player_first_val,banker_first_val,player_red,player_black,banker_red,banker_black,
            t_tag,is_r,player_card_count,banker_card_count,format,is_33,is_22,player_drew_3,
            message_id,collected_at,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (h["n"],h["player_score"],h["banker_score"],
             ",".join(x["suit"] for x in p),",".join(x["suit"] for x in b),
             p[0]["suit"] if p else None,b[0]["suit"] if b else None,
             p[0]["color"] if p else None,b[0]["color"] if b else None,
             p[0]["value"] if p else None,b[0]["value"] if b else None,
             sum(1 for x in p if x["color"]=="R"),sum(1 for x in p if x["color"]=="B"),
             sum(1 for x in b if x["color"]=="R"),sum(1 for x in b if x["color"]=="B"),
             h["t_tag"],1 if h["is_r"] else 0,len(p),len(b),
             h.get("format"),1 if h.get("is_33") else 0,1 if h.get("is_22") else 0,1 if h.get("player_drew_3") else 0,
             h.get("message_id"),datetime.utcnow().isoformat(),"web"))
        new+=1
    c.commit(); c.close(); return new

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

def pick_prediction(hands, strategies):
    if not hands: return None, None, "Aucune main"
    latest=hands[-1]
    if not strategies:
        pf=Counter(h["player_first_suit"] for h in hands if h.get("player_first_suit"))
        if not pf: return None, latest, "Pas de data"
        best_s=pf.most_common(1)[0][0]; total=sum(pf.values()); rate=round(100*pf[best_s]/total,2)
        pred={"suit":best_s,"symbol":EMOJI[best_s],"hit_rate":rate,"confidence":0.12,"sample":total,
              "margin":round(rate-25,2),"strategy":"FREQ_PLAYER","strategy_id":None,"note":"Fallback frequence"}
        return pred, latest, pred["note"]
    applicable=[s for s in strategies if s["side"]=="player" and s["from_suit"]==latest.get("player_first_suit")]
    if not applicable:
        applicable=[s for s in strategies if s["side"]=="banker" and s["from_suit"]==latest.get("banker_first_suit")]
    if not applicable:
        pf=Counter(h["player_first_suit"] for h in hands if h.get("player_first_suit"))
        if pf:
            best_s=pf.most_common(1)[0][0]; total=sum(pf.values()); rate=round(100*pf[best_s]/total,2)
            pred={"suit":best_s,"symbol":EMOJI[best_s],"hit_rate":rate,"confidence":0.1,"sample":total,
                  "margin":round(rate-25,2),"strategy":"FREQ_PLAYER","strategy_id":None,
                  "note":f"Aucune transition pour {latest.get('player_first_suit')}"}
            return pred, latest, pred["note"]
        return None, latest, "Rien d'applicable"
    def score(s):
        rate=s["real_rate"] if s["real_total"]>=MIN_VALIDATIONS else s["hist_rate"]
        return (rate, s["confidence"], s["sample_size"], s["real_total"])
    best=max(applicable, key=score)
    rate=best["real_rate"] if best["real_total"]>=MIN_VALIDATIONS else best["hist_rate"]
    note=None
    if latest.get("is_33"): note="⚠ 3-3 precedent: possible changement algo — vigilance"
    elif latest.get("player_drew_3"): note="Joueur 3 cartes precedemment"
    diag=(f"Score REEL {best['real_rate']}% ({best['real_hits']}/{best['real_total']})"
          if best["real_total"]>=MIN_VALIDATIONS else f"Score HISTO {best['hist_rate']}% (n={best['sample_size']})")
    note=f"{note} · {diag}" if note else diag
    pred={"suit":best["to_suit"],"symbol":EMOJI[best["to_suit"]],"hit_rate":rate,"confidence":best["confidence"],
          "sample":best["sample_size"],"margin":round(rate-25,2),"strategy":best["name"],"strategy_id":best["id"],"note":note}
    return pred, latest, note

def upsert_prediction(target_n, prediction, basis_n=None, basis_first_suit=None):
    if not prediction: return
    c=get_conn()
    c.execute("""INSERT INTO predictions (target_n,created_at,prediction_suit,strategy,strategy_id,confidence,hit_rate,margin,basis_n,basis_first_suit,note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(target_n) DO UPDATE SET
        prediction_suit=excluded.prediction_suit,strategy=excluded.strategy,strategy_id=excluded.strategy_id,
        confidence=excluded.confidence,hit_rate=excluded.hit_rate,margin=excluded.margin,
        basis_n=excluded.basis_n,basis_first_suit=excluded.basis_first_suit,note=excluded.note,created_at=excluded.created_at""",
        (target_n,datetime.utcnow().isoformat(),prediction["suit"],prediction.get("strategy"),prediction.get("strategy_id"),
         prediction.get("confidence",0),prediction.get("hit_rate",0),prediction.get("margin",0),basis_n,basis_first_suit,prediction.get("note")))
    c.commit(); c.close()

def validate_predictions(recheck_all=False):
    """
    Gagnant si l'enseigne predite apparait PARMI les cartes JOUEUR (2 ou 3),
    pas seulement en premiere position.
    """
    c=get_conn()
    if recheck_all:
        rows=c.execute("""SELECT p.id,p.prediction_suit,h.player_first_suit,h.player_suits
            FROM predictions p JOIN hands h ON h.n=p.target_n
            WHERE p.status IN ('PENDING','VALID','INVALID')""").fetchall()
    else:
        rows=c.execute("""SELECT p.id,p.prediction_suit,h.player_first_suit,h.player_suits
            FROM predictions p JOIN hands h ON h.n=p.target_n
            WHERE p.status='PENDING'""").fetchall()
    n=0
    for r in rows:
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
    validated=validate_predictions(recheck_all=recheck)
    score_strategies_from_validations()
    deactivated=prune_bad_strategies()
    created=rebuild_strategies(hands)
    record_patterns(hands)
    strategies=get_active_strategies()
    pred,latest,diag=pick_prediction(hands,strategies)
    if pred and latest:
        upsert_prediction(latest["n"]+1,pred,latest["n"],latest.get("player_first_suit"))
    return {"validated":validated,"strategies_created":created,"deactivated":deactivated,
            "active_count":len(strategies),"prediction":pred,"latest_n":latest["n"] if latest else None,"diagnosis":diag}

def fetch_page(before=None):
    url=CHANNEL_WEB+(f"?before={before}" if before else "")
    r=requests.get(url,headers=HEADERS,timeout=20); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser"); messages,ids=[],[]
    for w in soup.select(".tgme_widget_message"):
        post=w.get("data-post",""); mid=None
        if post and "/" in post:
            try: mid=int(post.split("/")[-1]); ids.append(mid)
            except ValueError: pass
        te=w.select_one(".tgme_widget_message_text")
        if te:
            text=te.get_text(separator=" ",strip=True)
            if text and "#N" in text[:50]: messages.append({"id":mid,"text":text})
    return messages,(min(ids) if ids else None)

def collect(pages=10, delay=0.4):
    all_parsed,seen,before=[],set(),None
    for i in range(pages):
        try: raw,min_id=fetch_page(before)
        except Exception as e:
            print(f"Erreur page {i+1}: {e}"); break
        for msg in raw:
            p=parse_message(msg["text"],msg["id"])
            if p and p["n"] not in seen: seen.add(p["n"]); all_parsed.append(p)
        before=min_id
        print(f" Page {i+1}/{pages} +{len(raw)} msgs uniques={len(all_parsed)}")
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
    def do_GET(self):
        path=urlparse(self.path).path; qs=parse_qs(urlparse(self.path).query)
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
            self.send_json({"timestamp":datetime.now().isoformat(timespec="seconds"),"latest":latest,
                "prediction":cycle.get("prediction"),"prediction_target_n":(latest["n"]+1) if latest else None,
                "prediction_history":hist[:100],"patterns":get_patterns(15),"strategies_active":get_active_strategies(10),
                "learning":{"validated_now":cycle.get("validated",0),"deactivated":cycle.get("deactivated",[]),
                    "active_count":cycle.get("active_count",0),"diagnosis":cycle.get("diagnosis")},
                "pred_stats":{"total":len(hist),"valid":n_valid,"invalid":n_invalid,"pending":n_pending}})
        elif path=="/api/predictions":
            validate_predictions(); self.send_json(get_prediction_history(int(qs.get("limit",[200])[0])))
        elif path=="/api/patterns": self.send_json(get_patterns(int(qs.get("limit",[30])[0])))
        elif path=="/api/hands": self.send_json(get_recent_hands(int(qs.get("limit",[40])[0])))
        else: self.send_json({"error":"Not found"},404)
    def do_POST(self):
        path=urlparse(self.path).path; qs=parse_qs(urlparse(self.path).query)
        if path=="/api/collect":
            pages=int(qs.get("pages",[8])[0]); print(f"=== Collecte {pages} pages ===")
            try:
                parsed=collect(pages=pages); new=upsert_hands(parsed); cycle=run_learning_cycle()
                c=get_conn(); c.execute("INSERT INTO collection_logs (ts,hands_found,hands_new,status) VALUES (?,?,?,?)",
                    (datetime.utcnow().isoformat(),len(parsed),new,"success")); c.commit(); c.close()
                self.send_json({"status":"ok","hands_found":len(parsed),"hands_new":new,
                    "message":f"{new} nouvelles · apprentissage maj","learning":cycle,"next_prediction":cycle.get("prediction")})
            except Exception as e: self.send_json({"status":"error","error":str(e)},500)
        elif path=="/api/learn":
            self.send_json({"status":"ok","learning":run_learning_cycle(recheck=True)})
        else: self.send_json({"error":"Not found"},404)

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
.suit-big{font-size:3.4rem;line-height:1}
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
</style></head><body><div class="shell">
<header><div style="color:var(--gold-dim);letter-spacing:.25em">♠ ♥ ♣ ♦</div>
<h1>XCODE SUIT CARD</h1>
<div class="sub">Auto-apprenant · pred des P1 suffisant · cartes live · bascule strategies</div></header>

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
<div><div class="ey">Enseigne</div><div class="pname" id="pn">En attente</div><span class="pill" id="target">Cible —</span>
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
async function j(u,o){return(await fetch(u,o)).json()}
let liveOn=false,liveTimer=null,liveBusy=false;
function setLiveUI(on){const b=document.getElementById('btn-live'),g=document.getElementById('live-badge');
if(on){b.textContent='LIVE AUTO ACTIF';b.classList.remove('off');g.innerHTML='<span class="dot"></span>LIVE'}
else{b.textContent='DEMARRER LIVE';b.classList.add('off');g.textContent='OFF'}}
function toggleLive(){if(liveOn){liveOn=false;if(liveTimer){clearInterval(liveTimer);liveTimer=null}setLiveUI(false);tx('live-status','LIVE arrete.');return}
liveOn=true;setLiveUI(true);tx('live-status','LIVE…');runLiveTick();liveTimer=setInterval(runLiveTick,4000)}
async function runLiveTick(){if(liveBusy)return;liveBusy=true;try{tx('live-status','Collecte + learn…');
const d=await j('/api/collect?pages=3',{method:'POST'});await refreshAll();
const L=d.learning||{};let m=d.status==='ok'?`+${d.hands_new||0} · ${L.active_count||0} actives`:'Err';
if(L.deactivated&&L.deactivated.length)m+=' · '+L.deactivated.length+' coupees';tx('live-status',m)}
catch(e){tx('live-status','Err '+e.message)}finally{liveBusy=false}}
function fillLiveTable(hands,preds){const tb=document.getElementById('lt-body'),ct=document.getElementById('lt-count');
if(!tb)return;const map={};(preds||[]).forEach(p=>map[p.target_n]=p);
const rows=(hands||[]).slice().sort((a,b)=>b.n-a.n).slice(0,25);
if(!rows.length){tb.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:12px">Vide</td></tr>';if(ct)ct.textContent='0';return}
const latest=rows[0]&&rows[0].n;
tb.innerHTML=rows.map(h=>{const pS=(h.player_suits||'').split(',').filter(Boolean),bS=(h.banker_suits||'').split(',').filter(Boolean);
const fmt=h.format||(pS.length+'-'+bS.length);const pred=map[h.n];let st='—',cl='lt-pend',ps='—';
if(pred){ps=sm[pred.prediction_suit]||pred.prediction_suit;if(pred.status==='VALID'){st='OK';cl='lt-ok'}else if(pred.status==='INVALID'){st='KO';cl='lt-ko'}else{st='…';cl='lt-pend'}}
return `<tr class="${h.n===latest?'now':''}"><td class="lt-num">#N${h.n}</td><td>${pS.map(s=>sm[s]||s).join(' ')||'—'}</td><td>${bS.map(s=>sm[s]||s).join(' ')||'—'}</td><td>${fmt}</td><td>${ps}</td><td class="${cl}">${st}</td></tr>`}).join('');
if(ct)ct.textContent=rows.length+' jeux'}
async function refreshAll(){try{
const [l,h,p,st,hands,stR]=await Promise.all([j('/api/live'),j('/api/predictions?limit=200'),j('/api/patterns?limit=15'),j('/api/stats/overview'),j('/api/hands?limit=30'),j('/api/strategies')]);
const x=l.latest;tx('clock',new Date().toLocaleTimeString());
if(x){tx('gn','#N'+x.n);document.getElementById('pc').innerHTML=(x.player_suits||'').split(',').filter(Boolean).map((s,i)=>card('P'+(i+1),s)).join('')||'—';
document.getElementById('bc').innerHTML=(x.banker_suits||'').split(',').filter(Boolean).map((s,i)=>card('B'+(i+1),s)).join('')||'—';
tx('fmt',x.format||'?');const nP=(x.player_suits||'').split(',').filter(Boolean).length;const fmtEl=document.getElementById('fmt');if(fmtEl&&nP>=2)fmtEl.innerHTML=(x.format||(nP+'-?'))+' <span style="color:var(--ok)">· P1 OK</span>';const al=document.getElementById('alert33');if(al)al.style.display=x.is_33?'block':'none';
if(x.is_33){const ls=document.getElementById('live-status');if(ls&&!ls.textContent.includes('3-3'))ls.textContent=(ls.textContent||'')+' · ⚠ dernier jeu 3-3'}}
if(l.prediction){const q=l.prediction;const p1n=(x&&x.player_suits)?x.player_suits.split(',').filter(Boolean).length:0;if(p1n>=2){const rs=document.getElementById('live-status');if(rs&&!String(rs.textContent).includes('P1 pret'))rs.textContent='P1 pret ('+p1n+' cartes) → pred #N'+(l.prediction_target_n||'?')+' emise';}tx('ps',q.symbol);tx('pn',(q.symbol||'')+' — '+(sn[q.suit]||''));
tx('target','Cible #N'+l.prediction_target_n);tx('strat',q.strategy||'AUTO');
window._lastPredTxt='#N'+l.prediction_target_n+(q.symbol||sm[q.suit]||q.suit||'');
const b1=document.getElementById('btn-copy-one');if(b1)b1.textContent='⧉ COPIER '+window._lastPredTxt;tx('rate',q.hit_rate+'%');
tx('margin',(q.margin>=0?'+':'')+q.margin);tx('sample',q.sample);tx('conf',Math.round((q.confidence||0)*100)+'%');
const pn=document.getElementById('pnote');if(q.note){pn.style.display='block';pn.textContent=q.note}else{pn.style.display='none'}}
const ps=l.pred_stats||{};tx('total',st.total_hands);tx('preds',ps.total||h.length);tx('valid',ps.valid||0);tx('invalid',ps.invalid||0);
tx('sactive',st.strategies_active!=null?st.strategies_active:(stR.active||[]).length);
document.getElementById('hist').innerHTML=h.map(x=>`<tr><td>#${x.target_n}</td><td><b>${sm[x.prediction_suit]||x.prediction_suit}</b></td>
<td>${x.strategy||'—'}</td><td>${x.hit_rate}%</td>
<td><span class="status ${x.status==='VALID'?'valid':x.status==='INVALID'?'invalid':'pending'}">${x.status}</span></td>
<td>${(x.actual_first_suit||'').split(',').filter(Boolean).map(s=>sm[s]||s).join(' ')||'—'}</td></tr>`).join('')||'<tr><td colspan="6">Vide</td></tr>';
const allS=(stR.all||stR.active||[]);
document.getElementById('strats').innerHTML=allS.slice(0,12).map(s=>{const rate=s.real_total>=8?s.real_rate:s.hist_rate;
return `<div class="strat-item"><span class="${s.is_active?'':'off'}">${s.name}</span><span>${s.is_active?'✓ '+rate+'%':'✗ coupe'}</span></div>`}).join('')||'—';
document.getElementById('patterns').innerHTML=p.map(x=>`<div class="pattern"><code>${x.pattern}</code><span class="count">x${x.occurrences}</span></div>`).join('')||'—';
fillLiveTable(hands,h);
if(l.learning&&l.learning.diagnosis)tx('out','Diag: '+l.learning.diagnosis);
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
refreshAll();setInterval(refreshAll,3000);
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
    init_db(); print(f"DB: {DB_PATH}"); print(f"http://0.0.0.0:{PORT}")
    if AUTO_COLLECT_INTERVAL>0:
        threading.Thread(target=auto_collect_loop,daemon=True).start()
        print(f"Auto-collect {AUTO_COLLECT_INTERVAL}s")
    try: HTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
    except KeyboardInterrupt: print("\\nArret.")
