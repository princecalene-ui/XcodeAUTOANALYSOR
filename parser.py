"""
Parser robuste des messages du canal Statistika Bakkara
Format typique :
#N1242. 4(2♥️2♠️) - 8(7♠️A♦️) #T12 #R
#N484 . 0(A ♣️ A ♠️ 8 ♠️ ) - 0(7 ♥️ Q ♣️ ) #T0
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


SUIT_MAP = {
    "♥️": "H", "♥": "H",
    "♦️": "D", "♦": "D",
    "♠️": "S", "♠": "S",
    "♣️": "C", "♣": "C",
}

COLOR_MAP = {"H": "R", "D": "R", "S": "B", "C": "B"}

RANK_VALUE = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 0, "T": 0, "J": 0, "Q": 0, "K": 0,
}


@dataclass
class Card:
    rank: str
    suit: str          # H D S C
    color: str         # R B
    value: int         # 0-9 baccarat


@dataclass
class ParsedHand:
    n: int
    player_score: int
    banker_score: int
    player_cards: List[Card] = field(default_factory=list)
    banker_cards: List[Card] = field(default_factory=list)
    t_tag: Optional[str] = None
    is_r: bool = False
    raw: str = ""
    message_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        def cards_to_raw(cards: List[Card]) -> str:
            return ",".join(f"{c.rank}{c.suit}" for c in cards)

        def suits_str(cards: List[Card]) -> str:
            return ",".join(c.suit for c in cards)

        p_first = self.player_cards[0] if self.player_cards else None
        b_first = self.banker_cards[0] if self.banker_cards else None

        return {
            "n": self.n,
            "player_score": self.player_score,
            "banker_score": self.banker_score,
            "player_cards_raw": cards_to_raw(self.player_cards),
            "banker_cards_raw": cards_to_raw(self.banker_cards),
            "player_suits": suits_str(self.player_cards),
            "banker_suits": suits_str(self.banker_cards),
            "player_first_suit": p_first.suit if p_first else None,
            "banker_first_suit": b_first.suit if b_first else None,
            "player_first_color": p_first.color if p_first else None,
            "banker_first_color": b_first.color if b_first else None,
            "player_first_val": p_first.value if p_first else None,
            "banker_first_val": b_first.value if b_first else None,
            "player_red_count": sum(1 for c in self.player_cards if c.color == "R"),
            "player_black_count": sum(1 for c in self.player_cards if c.color == "B"),
            "banker_red_count": sum(1 for c in self.banker_cards if c.color == "R"),
            "banker_black_count": sum(1 for c in self.banker_cards if c.color == "B"),
            "t_tag": self.t_tag,
            "is_r": self.is_r,
            "player_card_count": len(self.player_cards),
            "banker_card_count": len(self.banker_cards),
            "message_id": self.message_id,
        }


# Regex principal (flexible sur les espaces)
HAND_RE = re.compile(
    r"#N(\d+)\s*\.\s*"
    r"(\d+)\s*\(([^)]+)\)\s*"
    r"-\s*"
    r"(\d+)\s*\(([^)]+)\)\s*"
    r"(#T\d+)?\s*"
    r"(#R)?",
    re.IGNORECASE
)

# Extraction des cartes : rank + suit
CARD_RE = re.compile(
    r"(A|10|[2-9JQKT])\s*([♥️♦♦️♠️♣♣️♥♠])",
    re.IGNORECASE
)


def parse_cards(card_str: str) -> List[Card]:
    cards = []
    for rank, suit_char in CARD_RE.findall(card_str):
        rank = rank.upper()
        if rank == "T":
            rank = "10"
        suit = SUIT_MAP.get(suit_char)
        if not suit:
            continue
        value = RANK_VALUE.get(rank, 0)
        cards.append(Card(
            rank=rank,
            suit=suit,
            color=COLOR_MAP[suit],
            value=value
        ))
    return cards


def parse_message(text: str, message_id: Optional[int] = None) -> Optional[ParsedHand]:
    """Parse un message brut du canal. Retourne None si non reconnu."""
    text = text.strip()
    m = HAND_RE.search(text)
    if not m:
        return None

    n, p_score, p_cards_str, b_score, b_cards_str, t_tag, r_tag = m.groups()

    player_cards = parse_cards(p_cards_str)
    banker_cards = parse_cards(b_cards_str)

    if not player_cards and not banker_cards:
        return None

    return ParsedHand(
        n=int(n),
        player_score=int(p_score),
        banker_score=int(b_score),
        player_cards=player_cards,
        banker_cards=banker_cards,
        t_tag=t_tag,
        is_r=bool(r_tag),
        raw=text,
        message_id=message_id,
    )


def parse_many(texts: List[str]) -> List[ParsedHand]:
    results = []
    for t in texts:
        h = parse_message(t)
        if h:
            results.append(h)
    return results
