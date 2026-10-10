"""Contracts for audited, application-owned providers; no network or AGF inputs."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable

SPORTS = ("at", "basket", "futbol", "hisse")
# Upper bounds in seconds. Download time cannot replace the source's as-of time.
REQUIRED_EVIDENCE = {
    "at": {"event": 300, "runners": 300, "odds": 60, "ratings": 86400, "history": 300},
    "basket": {"event": 300, "lineups": 900, "form": 86400, "market": 60},
    "futbol": {"event": 300, "lineups": 900, "form": 86400, "market": 60},
    "hisse": {"prices": 5, "nbbo": 5, "volume": 60, "momentum": 60,
              "news": 86400, "sec": 900, "fx": 300, "account": 5},
}


@dataclass(frozen=True)
class CommandRequest:
    sport: str
    args: tuple[str, ...]
    now: datetime


@dataclass(frozen=True)
class SourceEvidence:
    name: str
    source_id: str
    source_url: str
    as_of: datetime
    verified: bool = False
    delay_seconds: int | None = None


@dataclass(frozen=True)
class StockRiskInputs:
    """Read-only observations. Monetary numbers must be finite Decimal values."""
    price: Decimal
    bid: Decimal
    ask: Decimal
    usd_try: Decimal
    position_tl: Decimal
    open_exposure_tl: Decimal
    daily_loss_tl: Decimal  # Nonnegative realized + unrealized loss, supplied by trusted adapter.
    relative_volume: Decimal
    momentum_percent: Decimal
    positive_news: bool = False
    sec_financing_clear: bool = False
    regular_session_open: bool = False


@dataclass(frozen=True)
class Candidate:
    event_id: str | None = None
    market: str | None = None
    closes_at: datetime | None = None
    selection: str | None = None
    evidence: tuple[SourceEvidence, ...] = ()
    reason_codes: tuple[str, ...] = ()
    # Set only by the reviewed provider after it checks complete model inputs.
    # Telegram messages must NEVER be deserialized into Candidate objects.
    inputs_complete: bool = False
    stock_risk: StockRiskInputs | None = None


@dataclass(frozen=True)
class ProviderRegistration:
    model: str  # Include a model version; recorded immutably with every prediction.
    callback: Callable[[CommandRequest], Candidate]
    source_hosts: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Reply:
    chat_id: int
    text: str
    # Send as plain text through the EXISTING receiver's reply method.
    parse_mode: None = None
