"""
Modèles de données pour le système Suit Card Strategy
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, 
    Float, Text, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.ext.asyncio import AsyncAttrs

Base = declarative_base()


class Hand(Base):
    """Une main de baccarat extraite du canal"""
    __tablename__ = "hands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    n = Column(Integer, unique=True, nullable=False, index=True)  # #Nxxxx
    player_score = Column(Integer, nullable=False)
    banker_score = Column(Integer, nullable=False)
    
    # Cartes brutes (JSON serialisé)
    player_cards_raw = Column(Text)   # ex: "A♣️,2♥️,10♦️"
    banker_cards_raw = Column(Text)
    
    # Enseignes (liste de codes H/D/S/C)
    player_suits = Column(String(20))  # ex: "H,S,C"
    banker_suits = Column(String(20))
    
    # Première carte (la plus importante pour les patterns)
    player_first_suit = Column(String(1), index=True)
    banker_first_suit = Column(String(1), index=True)
    player_first_color = Column(String(1))  # R ou B
    banker_first_color = Column(String(1))
    player_first_val = Column(Integer)      # 0-9
    banker_first_val = Column(Integer)
    
    # Couleurs dominantes
    player_red_count = Column(Integer, default=0)
    player_black_count = Column(Integer, default=0)
    banker_red_count = Column(Integer, default=0)
    banker_black_count = Column(Integer, default=0)
    
    # Tags
    t_tag = Column(String(10), index=True)  # #T12
    is_r = Column(Boolean, default=False)   # #R présent
    
    # Métadonnées
    message_id = Column(Integer)            # ID Telegram
    collected_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String(20), default="web")  # web | telethon
    
    # Nombre de cartes
    player_card_count = Column(Integer)
    banker_card_count = Column(Integer)

    __table_args__ = (
        Index("idx_n", "n"),
        Index("idx_t_tag", "t_tag"),
        Index("idx_player_first", "player_first_suit"),
        Index("idx_banker_first", "banker_first_suit"),
    )


class Strategy(Base):
    """Une stratégie générée / scoreée automatiquement"""
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    description = Column(Text)
    
    # Type : transition, frequency, hourly, pattern...
    strategy_type = Column(String(40), index=True)
    
    # Conditions (JSON)
    conditions = Column(Text)   # {"prev_suit": "S", "side": "banker", ...}
    
    # Action recommandée
    recommendation = Column(Text)  # "Banker ♣️", "Player ♥️", etc.
    
    # Métriques
    sample_size = Column(Integer, default=0)
    hit_count = Column(Integer, default=0)
    hit_rate = Column(Float, default=0.0)
    confidence = Column(Float, default=0.0)  # score interne
    
    # Versioning & statut
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Période d'analyse
    data_from_n = Column(Integer)
    data_to_n = Column(Integer)


class CollectionLog(Base):
    """Journal des collectes"""
    __tablename__ = "collection_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    hands_found = Column(Integer, default=0)
    hands_new = Column(Integer, default=0)
    status = Column(String(20), default="running")  # running | success | error
    error_message = Column(Text)
    source = Column(String(20), default="web")
