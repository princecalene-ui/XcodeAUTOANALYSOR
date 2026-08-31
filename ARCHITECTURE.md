# Xcode SUIT CARD STRATÉGIE CREATOR
## Architecture du système auto-apprenant

### Objectif
Site indépendant qui :
1. Collecte en continu l'historique + le temps réel du canal Telegram `@statistika_baccara`
2. Structure et stocke toutes les mains (enseignes, couleurs, valeurs, heures, tags)
3. Analyse automatiquement les patterns (transitions, fréquences, horaires)
4. Génère et met à jour des stratégies
5. Sert de base future au module de prédiction

### Stack recommandée (MVP → Production)

| Couche              | Technologie                          | Rôle                                      |
|---------------------|--------------------------------------|-------------------------------------------|
| Collecte            | Python (requests + BeautifulSoup) + Telethon (option) | Scraping web + API Telegram               |
| Parsing             | Python regex + dataclasses           | Extraction cartes / enseignes / tags      |
| Stockage            | SQLite (MVP) → PostgreSQL            | Historique + features                     |
| Backend API         | FastAPI                              | Endpoints data + stats + stratégies       |
| Analyse / ML        | Pandas + scikit-learn                | Fréquences, transitions, modèles          |
| Frontend            | React / Next.js ou Streamlit (MVP)   | Dashboard + visualisation                 |
| Scheduler           | APScheduler / Celery / cron          | Collecte continue                         |
| Déploiement         | Docker + VPS (ou Railway / Render)   | Site indépendant 24/7                     |

### Flux de données
```
Telegram Channel
       │
       ▼
[Scraper temps réel + historique]
       │
       ▼
[Parser → Structure Hand]
       │
       ▼
[SQLite / PostgreSQL]
       │
       ├──► [Analyseur de patterns]
       │         │
       │         ▼
       │    [Stratégies auto-générées]
       │
       └──► [API FastAPI]
                 │
                 ▼
            [Dashboard Web]
```

### Modules principaux
1. `collector/`     → scraping + ingestion
2. `parser/`        → extraction enseignes / couleurs / valeurs
3. `storage/`       → modèles DB + repositories
4. `analyzer/`      → statistiques + transitions + stratégies
5. `api/`           → FastAPI
6. `frontend/`      → interface
7. `scheduler/`     → boucle continue

### Stratégie d'auto-renforcement
- Plus de données → statistiques plus stables
- Recalcul périodique des matrices de transition
- Scoring des règles (winrate simulé sur historique)
- Conservation uniquement des règles au-dessus d'un seuil
- Versioning des stratégies
