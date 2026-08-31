#!/usr/bin/env python3
"""
Script CLI pour collecter des données sans lancer le serveur web.
Usage:
  python scripts/run_collect.py --pages 20
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import asyncio
import argparse
from collector import collect_recent
from database import init_db, upsert_hands, get_hand_count, log_collection


async def main(pages: int):
    print(f"→ Initialisation DB...")
    await init_db()
    before = await get_hand_count()
    print(f"→ Mains déjà en base : {before}")

    print(f"→ Collecte de {pages} pages...")
    hands = await collect_recent(pages=pages, delay=0.4)
    print(f"→ {len(hands)} mains parsées")

    new = await upsert_hands(hands)
    await log_collection(hands_found=len(hands), hands_new=new)
    after = await get_hand_count()

    print(f"→ Nouvelles insertions : {new}")
    print(f"→ Total en base maintenant : {after}")
    if hands:
        print(f"→ Range #N : {hands[0].n} → {hands[-1].n}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=15)
    args = parser.parse_args()
    asyncio.run(main(args.pages))
