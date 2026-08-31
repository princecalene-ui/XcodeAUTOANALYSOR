"""
Collecteur de données depuis le canal Telegram (version web publique)
https://t.me/s/statistika_baccara
"""
import asyncio
import httpx
from bs4 import BeautifulSoup
from typing import List, Tuple, Optional
from datetime import datetime
import logging

from parser import parse_message, ParsedHand

logger = logging.getLogger(__name__)

CHANNEL_WEB = "https://t.me/s/statistika_baccara"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


async def fetch_page(client: httpx.AsyncClient, before: Optional[int] = None) -> Tuple[List[dict], Optional[int]]:
    """
    Récupère une page de messages.
    Retourne (liste de {id, text}, min_message_id)
    """
    url = CHANNEL_WEB
    if before:
        url += f"?before={before}"

    try:
        resp = await client.get(url, headers=HEADERS, timeout=20.0)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Erreur fetch page: {e}")
        return [], None

    soup = BeautifulSoup(resp.text, "lxml")
    messages = []
    ids = []

    for widget in soup.select(".tgme_widget_message"):
        post = widget.get("data-post", "")
        msg_id = None
        if post and "/" in post:
            try:
                msg_id = int(post.split("/")[-1])
                ids.append(msg_id)
            except ValueError:
                pass

        text_el = widget.select_one(".tgme_widget_message_text")
        if text_el:
            text = text_el.get_text(separator=" ", strip=True)
            if text and ("#N" in text[:30] or text.startswith("#N")):
                messages.append({"id": msg_id, "text": text})

    min_id = min(ids) if ids else None
    return messages, min_id


async def collect_recent(pages: int = 5, delay: float = 0.5) -> List[ParsedHand]:
    """
    Collecte les N pages les plus récentes.
    """
    hands: List[ParsedHand] = []
    seen_n = set()
    before = None

    async with httpx.AsyncClient() as client:
        for page in range(pages):
            raw_msgs, min_id = await fetch_page(client, before)
            if not raw_msgs:
                break

            for msg in raw_msgs:
                parsed = parse_message(msg["text"], message_id=msg["id"])
                if parsed and parsed.n not in seen_n:
                    seen_n.add(parsed.n)
                    hands.append(parsed)

            before = min_id
            if page < pages - 1:
                await asyncio.sleep(delay)

            logger.info(f"Page {page+1}/{pages} → {len(raw_msgs)} msgs bruts, total uniques: {len(hands)}")

    # Trier par N croissant
    hands.sort(key=lambda h: h.n)
    return hands


async def collect_until_n(target_min_n: int, max_pages: int = 100, delay: float = 0.4) -> List[ParsedHand]:
    """
    Collecte en remontant jusqu'à atteindre un certain #N (ou max_pages).
    Utile pour remplir l'historique.
    """
    hands: List[ParsedHand] = []
    seen_n = set()
    before = None

    async with httpx.AsyncClient() as client:
        for page in range(max_pages):
            raw_msgs, min_id = await fetch_page(client, before)
            if not raw_msgs:
                break

            page_min_n = float("inf")
            for msg in raw_msgs:
                parsed = parse_message(msg["text"], message_id=msg["id"])
                if parsed:
                    page_min_n = min(page_min_n, parsed.n)
                    if parsed.n not in seen_n:
                        seen_n.add(parsed.n)
                        hands.append(parsed)

            before = min_id
            logger.info(f"Page {page+1} → min N={page_min_n}, total={len(hands)}")

            if page_min_n <= target_min_n:
                break
            if page < max_pages - 1:
                await asyncio.sleep(delay)

    hands.sort(key=lambda h: h.n)
    return hands


def collect_recent_sync(pages: int = 5) -> List[ParsedHand]:
    """Version synchrone pour scripts simples"""
    return asyncio.run(collect_recent(pages=pages))
