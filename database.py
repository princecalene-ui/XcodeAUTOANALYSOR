"""
Gestion de la base de données SQLite (async)
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, func
from models import Base, Hand, Strategy, CollectionLog
from parser import ParsedHand
from typing import List, Optional
from datetime import datetime
import os

DB_PATH = os.getenv("DB_PATH", "data/baccarat.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def upsert_hands(hands: List[ParsedHand], source: str = "web") -> int:
    """
    Insère les nouvelles mains. Retourne le nombre de nouvelles insertions.
    """
    if not hands:
        return 0

    new_count = 0
    async with AsyncSessionLocal() as session:
        # Récupérer les N déjà présents
        existing = await session.execute(select(Hand.n))
        existing_ns = {row[0] for row in existing.fetchall()}

        for h in hands:
            if h.n in existing_ns:
                continue
            data = h.to_dict()
            data["source"] = source
            data["collected_at"] = datetime.utcnow()
            hand = Hand(**data)
            session.add(hand)
            new_count += 1

        await session.commit()
    return new_count


async def get_hand_count() -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.count(Hand.id)))
        return result.scalar() or 0


async def get_latest_n() -> Optional[int]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.max(Hand.n)))
        return result.scalar()


async def get_oldest_n() -> Optional[int]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.min(Hand.n)))
        return result.scalar()


async def get_all_hands_ordered() -> List[Hand]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Hand).order_by(Hand.n.asc()))
        return list(result.scalars().all())


async def log_collection(hands_found: int, hands_new: int, status: str = "success", error: str = None, source: str = "web"):
    async with AsyncSessionLocal() as session:
        log = CollectionLog(
            finished_at=datetime.utcnow(),
            hands_found=hands_found,
            hands_new=hands_new,
            status=status,
            error_message=error,
            source=source,
        )
        session.add(log)
        await session.commit()
