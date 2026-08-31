"""
API FastAPI - Xcode SUIT CARD STRATÉGIE CREATOR
Site indépendant de collecte + analyse + stratégies auto
"""
from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional
from datetime import datetime
import asyncio

from database import init_db, get_session, upsert_hands, get_hand_count, get_latest_n, get_oldest_n, get_all_hands_ordered, log_collection, AsyncSessionLocal
from collector import collect_recent
from analyzer import full_analysis, generate_transition_strategies
from models import Hand, Strategy, CollectionLog

app = FastAPI(
    title="Xcode SUIT CARD STRATÉGIE CREATOR",
    description="Système auto-apprenant de collecte et d'analyse des enseignes Baccarat",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await init_db()


# ---------- COLLECTE ----------

@app.post("/api/collect")
async def trigger_collect(pages: int = 10, background_tasks: BackgroundTasks = None):
    """
    Lance une collecte des pages récentes.
    """
    async def _run():
        try:
            hands = await collect_recent(pages=pages)
            new = await upsert_hands(hands)
            await log_collection(hands_found=len(hands), hands_new=new, status="success")
            return {"found": len(hands), "new": new}
        except Exception as e:
            await log_collection(0, 0, status="error", error=str(e))
            raise

    result = await _run()
    return {
        "status": "ok",
        "hands_found": result["found"],
        "hands_new": result["new"],
        "message": f"{result['new']} nouvelles mains enregistrées"
    }


@app.get("/api/stats/overview")
async def overview():
    """Vue d'ensemble de la base"""
    count = await get_hand_count()
    latest = await get_latest_n()
    oldest = await get_oldest_n()
    return {
        "total_hands": count,
        "oldest_n": oldest,
        "latest_n": latest,
        "updated_at": datetime.utcnow().isoformat(),
    }


# ---------- ANALYSE ----------

@app.get("/api/analysis/full")
async def analysis_full():
    """Analyse complète (fréquences + transitions + stratégies)"""
    hands = await get_all_hands_ordered()
    if not hands:
        raise HTTPException(404, "Aucune donnée. Lancez d'abord /api/collect")
    report = full_analysis(hands)
    return report


@app.get("/api/analysis/strategies")
async def get_strategies(min_rate: float = 32.0, min_sample: int = 30):
    """Liste des stratégies générées automatiquement"""
    hands = await get_all_hands_ordered()
    if not hands:
        raise HTTPException(404, "Aucune donnée")
    strats = generate_transition_strategies(hands, min_sample=min_sample, min_rate=min_rate)
    return {
        "count": len(strats),
        "strategies": strats,
        "generated_at": datetime.utcnow().isoformat(),
    }


@app.get("/api/hands")
async def list_hands(limit: int = 50, offset: int = 0):
    """Liste paginée des mains"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Hand).order_by(desc(Hand.n)).offset(offset).limit(limit)
        )
        hands = result.scalars().all()
        return [
            {
                "n": h.n,
                "player_score": h.player_score,
                "banker_score": h.banker_score,
                "player_suits": h.player_suits,
                "banker_suits": h.banker_suits,
                "player_first": h.player_first_suit,
                "banker_first": h.banker_first_suit,
                "t_tag": h.t_tag,
                "is_r": h.is_r,
            }
            for h in hands
        ]


@app.get("/api/logs")
async def collection_logs(limit: int = 20):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CollectionLog).order_by(desc(CollectionLog.id)).limit(limit)
        )
        logs = result.scalars().all()
        return [
            {
                "id": l.id,
                "started_at": l.started_at.isoformat() if l.started_at else None,
                "finished_at": l.finished_at.isoformat() if l.finished_at else None,
                "hands_found": l.hands_found,
                "hands_new": l.hands_new,
                "status": l.status,
            }
            for l in logs
        ]


# ---------- DASHBOARD HTML SIMPLE ----------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    count = await get_hand_count()
    latest = await get_latest_n()
    oldest = await get_oldest_n()

    html = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Xcode SUIT CARD STRATÉGIE CREATOR</title>
  <style>
    :root {{
      --bg: #0f1419;
      --card: #1a2332;
      --accent: #3b82f6;
      --green: #22c55e;
      --text: #e2e8f0;
      --muted: #94a3b8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 2rem;
    }}
    h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; }}
    .subtitle {{ color: var(--muted); margin-bottom: 2rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
    .card {{
      background: var(--card);
      border-radius: 12px;
      padding: 1.25rem;
      border: 1px solid #2d3748;
    }}
    .card h3 {{ font-size: 0.85rem; color: var(--muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .card .value {{ font-size: 2rem; font-weight: 700; color: var(--green); }}
    .actions {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }}
    button, .btn {{
      background: var(--accent);
      color: white;
      border: none;
      padding: 0.75rem 1.5rem;
      border-radius: 8px;
      font-size: 1rem;
      cursor: pointer;
      text-decoration: none;
      display: inline-block;
    }}
    button:hover, .btn:hover {{ opacity: 0.9; }}
    button.secondary {{ background: #475569; }}
    #result {{ 
      background: var(--card); 
      border-radius: 12px; 
      padding: 1.5rem; 
      min-height: 120px;
      white-space: pre-wrap;
      font-family: ui-monospace, monospace;
      font-size: 0.9rem;
      border: 1px solid #2d3748;
    }}
    .footer {{ margin-top: 3rem; color: var(--muted); font-size: 0.85rem; }}
  </style>
</head>
<body>
  <h1>♠️ Xcode SUIT CARD STRATÉGIE CREATOR</h1>
  <p class="subtitle">Système auto-apprenant — Collecte + Analyse + Stratégies Baccarat</p>

  <div class="grid">
    <div class="card">
      <h3>Mains en base</h3>
      <div class="value" id="total">{count}</div>
    </div>
    <div class="card">
      <h3>Plus ancien #N</h3>
      <div class="value" id="oldest">{oldest or '—'}</div>
    </div>
    <div class="card">
      <h3>Plus récent #N</h3>
      <div class="value" id="latest">{latest or '—'}</div>
    </div>
  </div>

  <div class="actions">
    <button onclick="collect(10)">Collecter 10 pages</button>
    <button onclick="collect(30)" class="secondary">Collecter 30 pages (historique)</button>
    <button onclick="runAnalysis()" class="secondary">Lancer l'analyse</button>
    <a class="btn secondary" href="/api/analysis/strategies" target="_blank">Voir stratégies (JSON)</a>
    <a class="btn secondary" href="/docs" target="_blank">API Docs</a>
  </div>

  <div id="result">Prêt. Cliquez sur « Collecter » pour démarrer.</div>

  <div class="footer">
    Canal source : t.me/statistika_baccara · Site indépendant auto-renforçant
  </div>

  <script>
    async function collect(pages) {{
      const el = document.getElementById('result');
      el.textContent = 'Collecte en cours (' + pages + ' pages)...';
      try {{
        const r = await fetch('/api/collect?pages=' + pages, {{ method: 'POST' }});
        const data = await r.json();
        el.textContent = JSON.stringify(data, null, 2);
        // refresh stats
        const ov = await (await fetch('/api/stats/overview')).json();
        document.getElementById('total').textContent = ov.total_hands;
        document.getElementById('oldest').textContent = ov.oldest_n || '—';
        document.getElementById('latest').textContent = ov.latest_n || '—';
      }} catch (e) {{
        el.textContent = 'Erreur: ' + e;
      }}
    }}

    async function runAnalysis() {{
      const el = document.getElementById('result');
      el.textContent = 'Analyse en cours...';
      try {{
        const r = await fetch('/api/analysis/full');
        const data = await r.json();
        // Affichage résumé
        let txt = '=== ANALYSE COMPLÈTE ===\\n';
        txt += 'Mains analysées : ' + data.n_hands + '\\n';
        txt += 'Range #N : ' + data.n_range.min + ' → ' + data.n_range.max + '\\n\\n';
        txt += 'Stratégies détectées : ' + data.strategies_count + '\\n\\n';
        txt += 'Top 5 stratégies :\\n';
        (data.strategies || []).slice(0,5).forEach((s,i) => {{
          txt += (i+1) + '. ' + s.name + ' | hit_rate=' + s.hit_rate + '% | conf=' + s.confidence + '\\n';
          txt += '   ' + s.description + '\\n';
        }});
        el.textContent = txt;
      }} catch (e) {{
        el.textContent = 'Erreur: ' + e;
      }}
    }}
  </script>
</body>
</html>
"""
    return HTMLResponse(html)
