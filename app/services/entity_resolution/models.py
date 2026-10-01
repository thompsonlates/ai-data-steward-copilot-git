from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SignalScore:
    name: str
    score: float
    weight: float
    weighted_score: float
    detail: str
    signal_band: str
    match_level: Optional[str] = None
    domain_trust: Optional[float] = None
    signal_type: Optional[str] = None
