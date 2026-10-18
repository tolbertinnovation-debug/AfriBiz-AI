"""The shared vocabulary between the rule engine, the AI layer and the action executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class LineSpec:
    """One item line as understood from a message. Prices are in major units (e.g. 150 = KSh 150)."""

    name: str
    quantity: float = 1
    price: Decimal | None = None
    # "unit": price is per item ("@ 50", "50 each"); "total": price is for the whole line ("for 150");
    # "bare": a number with no marker, resolved later using the item's known selling price.
    price_kind: str = "bare"
    unit: str | None = None


@dataclass
class Intent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def get(self, key: str, default: Any = None) -> Any:
        return self.args.get(key, default)
