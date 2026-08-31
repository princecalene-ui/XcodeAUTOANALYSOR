"""
Analyseur de patterns et générateur de stratégies
Auto-renforcement : plus de données → stats plus fiables → stratégies filtrées
"""
from collections import Counter, defaultdict
from typing import List, Dict, Any, Tuple
from models import Hand
from dataclasses import dataclass
import json


SUIT_EMOJI = {"H": "♥️", "D": "♦️", "S": "♠️", "C": "♣️"}
SUITS = ["H", "D", "S", "C"]


@dataclass
class TransitionStats:
    matrix: Dict[str, Dict[str, float]]   # from → to → percentage
    counts: Dict[str, Dict[str, int]]
    sample_size: int


def compute_suit_frequencies(hands: List[Hand]) -> Dict[str, Any]:
    player = Counter()
    banker = Counter()
    player_first = Counter()
    banker_first = Counter()

    for h in hands:
        if h.player_suits:
            for s in h.player_suits.split(","):
                player[s] += 1
        if h.banker_suits:
            for s in h.banker_suits.split(","):
                banker[s] += 1
        if h.player_first_suit:
            player_first[h.player_first_suit] += 1
        if h.banker_first_suit:
            banker_first[h.banker_first_suit] += 1

    def pct(counter: Counter) -> Dict[str, float]:
        total = sum(counter.values()) or 1
        return {s: round(100 * counter[s] / total, 2) for s in SUITS}

    return {
        "player_all": pct(player),
        "banker_all": pct(banker),
        "player_first": pct(player_first),
        "banker_first": pct(banker_first),
        "n_hands": len(hands),
    }


def compute_transitions(hands: List[Hand], side: str = "player") -> TransitionStats:
    """
    side = 'player' ou 'banker'
    Calcule la matrice de transition de la 1ère enseigne.
    """
    attr = "player_first_suit" if side == "player" else "banker_first_suit"
    counts = defaultdict(lambda: defaultdict(int))

    ordered = sorted(hands, key=lambda h: h.n)
    for i in range(len(ordered) - 1):
        s1 = getattr(ordered[i], attr)
        s2 = getattr(ordered[i + 1], attr)
        if s1 and s2:
            counts[s1][s2] += 1

    matrix = {}
    total_transitions = 0
    for s1 in SUITS:
        total = sum(counts[s1].values()) or 1
        total_transitions += sum(counts[s1].values())
        matrix[s1] = {s2: round(100 * counts[s1][s2] / total, 2) for s2 in SUITS}

    return TransitionStats(
        matrix=matrix,
        counts={k: dict(v) for k, v in counts.items()},
        sample_size=total_transitions,
    )


def generate_transition_strategies(
    hands: List[Hand],
    min_sample: int = 30,
    min_rate: float = 32.0
) -> List[Dict[str, Any]]:
    """
    Génère des stratégies basées sur les transitions fortes.
    Une stratégie = "Si la 1ère carte précédente était X côté Y, alors privilégier Z"
    """
    strategies = []

    for side in ["player", "banker"]:
        stats = compute_transitions(hands, side=side)
        for from_suit in SUITS:
            total_from = sum(stats.counts.get(from_suit, {}).values())
            if total_from < min_sample:
                continue
            for to_suit in SUITS:
                rate = stats.matrix[from_suit][to_suit]
                if rate >= min_rate:
                    strategies.append({
                        "name": f"Trans_{side[0].upper()}_{from_suit}_to_{to_suit}",
                        "description": (
                            f"Si la 1ère carte {side} précédente était {SUIT_EMOJI[from_suit]}, "
                            f"alors la suivante a {rate}% de chance d'être {SUIT_EMOJI[to_suit]}"
                        ),
                        "strategy_type": "transition",
                        "conditions": {
                            "side": side,
                            "prev_first_suit": from_suit,
                            "target_first_suit": to_suit,
                        },
                        "recommendation": f"{side.capitalize()} 1ère → {SUIT_EMOJI[to_suit]}",
                        "sample_size": total_from,
                        "hit_rate": rate,
                        "confidence": round(min(rate / 50.0, 1.0) * (min(total_from, 200) / 200), 3),
                    })

    # Trier par confidence décroissante
    strategies.sort(key=lambda s: s["confidence"], reverse=True)
    return strategies


def compute_color_stats(hands: List[Hand]) -> Dict[str, Any]:
    p_red = p_black = b_red = b_black = 0
    for h in hands:
        p_red += h.player_red_count or 0
        p_black += h.player_black_count or 0
        b_red += h.banker_red_count or 0
        b_black += h.banker_black_count or 0

    p_total = p_red + p_black or 1
    b_total = b_red + b_black or 1

    return {
        "player": {"red": round(100 * p_red / p_total, 2), "black": round(100 * p_black / p_total, 2)},
        "banker": {"red": round(100 * b_red / b_total, 2), "black": round(100 * b_black / b_total, 2)},
    }


def full_analysis(hands: List[Hand]) -> Dict[str, Any]:
    """Rapport complet d'analyse"""
    if not hands:
        return {"error": "Aucune donnée"}

    freq = compute_suit_frequencies(hands)
    trans_p = compute_transitions(hands, "player")
    trans_b = compute_transitions(hands, "banker")
    colors = compute_color_stats(hands)
    strategies = generate_transition_strategies(hands)

    return {
        "n_hands": len(hands),
        "n_range": {
            "min": min(h.n for h in hands),
            "max": max(h.n for h in hands),
        },
        "frequencies": freq,
        "colors": colors,
        "transitions": {
            "player": trans_p.matrix,
            "banker": trans_b.matrix,
            "player_sample": trans_p.sample_size,
            "banker_sample": trans_b.sample_size,
        },
        "strategies": strategies[:20],  # top 20
        "strategies_count": len(strategies),
    }
