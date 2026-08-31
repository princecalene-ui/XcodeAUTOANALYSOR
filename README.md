# Xcode SUIT CARD STRATÉGIE CREATOR

Site indépendant auto-apprenant pour la collecte et l'analyse des **enseignes Baccarat**  
Source : canal Telegram [@statistika_baccara](https://t.me/statistika_baccara)

---

## Objectif

1. **Collecter** en continu l’historique + le temps réel des mains
2. **Structurer** les données (enseignes, couleurs, valeurs, heures `#T`, tags `#R`)
3. **Analyser** automatiquement fréquences, transitions, patterns
4. **Générer et scorer** des stratégies qui s’améliorent avec le volume de données
5. Servir de base future à un **module de prédiction**

---

## Stack

- **Backend** : FastAPI + SQLAlchemy (SQLite)
- **Collecte** : Scraping web public `t.me/s/statistika_baccara` (pas besoin d’API key au début)
- **Analyse** : Python (matrices de transition, fréquences, scoring)
- **Frontend** : Dashboard HTML intégré (MVP) → évolutif vers React/Next.js

---

## Installation rapide

```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Lancer le site

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Ouvrir : http://localhost:8000

### Collecte en ligne de commande

```bash
python scripts/run_collect.py --pages 30
```

---

## API principale

| Endpoint                    | Méthode | Description                          |
|----------------------------|---------|--------------------------------------|
| `/`                        | GET     | Dashboard web                        |
| `/api/collect?pages=10`    | POST    | Collecte N pages                     |
| `/api/stats/overview`      | GET     | Nombre de mains, range #N            |
| `/api/analysis/full`       | GET     | Analyse complète + top stratégies    |
| `/api/analysis/strategies` | GET     | Liste des stratégies filtrées        |
| `/api/hands`               | GET     | Liste paginée des mains              |
| `/docs`                    | GET     | Documentation Swagger interactive    |

---

## Roadmap

- [x] Parser robuste des messages
- [x] Collecteur web multi-pages
- [x] Base SQLite + modèles
- [x] Analyse fréquences + transitions
- [x] Génération automatique de stratégies
- [x] Dashboard MVP
- [ ] Scheduler (collecte toutes les X minutes)
- [ ] Support Telethon (historique complet + temps réel via API)
- [ ] Module de prédiction (ML)
- [ ] Frontend React + graphiques
- [ ] Authentification + multi-utilisateurs
- [ ] Export CSV / JSON des stratégies

---

## Structure du projet

```
baccarat-suit-strategy/
├── backend/
│   ├── main.py          # FastAPI
│   ├── models.py        # SQLAlchemy
│   ├── parser.py        # Extraction cartes/enseignes
│   ├── collector.py     # Scraping Telegram web
│   ├── database.py      # Session + upsert
│   ├── analyzer.py      # Stats + stratégies
│   └── requirements.txt
├── scripts/
│   └── run_collect.py
├── data/                # baccarat.db (généré)
├── docs/
│   └── ARCHITECTURE.md
└── README.md
```

---

## Notes importantes

- Le scraping web public est limité (environ 20 messages par page). Pour un historique massif, passer à **Telethon** (nécessite `api_id` + `api_hash` de my.telegram.org).
- Baccarat reste un jeu de hasard. Les stratégies sont des **patterns statistiques**, pas une garantie de gain.
- Le système s’auto-renforce : plus on collecte, plus les matrices de transition et les scores de confiance deviennent stables.
