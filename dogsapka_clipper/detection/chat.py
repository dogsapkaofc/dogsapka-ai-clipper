"""Detection methods A (chat activity spikes) and B (chat reactions)."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, deque

from dogsapka_clipper.detection.base import EWStats, SignalSample, ramp

# keyword -> weight. Matched on normalized (upper-case, accent-folded variants included) text.
REACTION_KEYWORDS: dict[str, float] = {
    # English
    "LOL": 0.8, "LMAO": 1.0, "LMFAO": 1.0, "ROFL": 1.0, "OMG": 0.9, "NO WAY": 1.0, "WTF": 0.9,
    "CLIP": 1.0, "CLIP IT": 1.2, "CLIPPED": 1.0, "POG": 0.9, "POGGERS": 1.0, "INSANE": 1.0, "EZ": 0.5,
    "HAHA": 0.8, "HAHAHA": 1.0, "WHAT": 0.5, "GG": 0.6, "HOLY": 0.8, "SHEESH": 0.8, "CRAZY": 0.8,
    "NOOO": 0.8, "LETS GO": 0.9, "LET'S GO": 0.9, "W": 0.4, "RIP": 0.6, "OMEGALUL": 1.0,
    # Hungarian
    "NE MÁR": 1.0, "NE MAR": 1.0, "MI VOLT EZ": 1.1, "MI VOLT": 0.8, "ÚRISTEN": 1.0, "URISTEN": 1.0,
    "TE JÓ ÉG": 1.0, "TE JO EG": 1.0, "BAZDMEG": 0.8, "AZTA": 0.9, "HÚHA": 0.8, "HUHA": 0.8,
    "NEM HISZEM EL": 1.0, "ŐRÜLET": 1.0, "ORULET": 1.0, "BETEG": 0.7, "KLIPPELD": 1.2, "KLIP": 1.0,
    "JÁJ": 0.6, "JAJ": 0.6, "HAJRÁ": 0.6, "HAJRA": 0.6,
}
REACTION_EMOTES = {"KEKW": 1.0, "LUL": 0.9, "LULW": 0.9, "OMEGALUL": 1.0, "POGCHAMP": 1.0, "POGGERS": 1.0,
                   "POGU": 1.0, "MONKAS": 0.7, "MONKAW": 0.8, "PEPELAUGH": 0.9, "ICANT": 1.0, "KAPPA": 0.3,
                   "WUT": 0.6, "D:": 0.7, "OOOO": 0.8, "AWARE": 0.5, "SADGE": 0.4, "EZ": 0.5}


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _compile() -> list[tuple[re.Pattern, str, float]]:
    out = []
    for kw, w in REACTION_KEYWORDS.items():
        pat = re.escape(kw).replace(r"\ ", r"\s+")
        out.append((re.compile(rf"(?<![A-ZÁÉÍÓÖŐÚÜŰ0-9]){pat}(?![A-ZÁÉÍÓÖŐÚÜŰ0-9])"), kw, w))
    return out


_PATTERNS = _compile()
_LAUGH = re.compile(r"\b(?:A?H[AEH]){3,}\b|\b(?:XD+)\b|\b(?:JAJA){1,}\b|\b(?:KEK)+W?\b")
_STRETCH = re.compile(r"([A-Z!?])\1{3,}")


def reaction_strength(text: str, emotes: list[str] | None = None) -> tuple[float, str]:
    """Return (0..~1.5 strength, best matching token) for a single chat message."""
    up = text.upper()
    best, token = 0.0, ""
    for pat, kw, w in _PATTERNS:
        if pat.search(up):
            if w > best:
                best, token = w, kw
    if best < 0.9 and _LAUGH.search(_fold(up)):
        best, token = 0.9, "HAHAHA"
    for e in (emotes or []) + up.split():
        w = REACTION_EMOTES.get(e.upper().strip(".,!?"))
        if w and w > best:
            best, token = w, e.upper()
    if best and (_STRETCH.search(up) or up.count("!") >= 3):
        best *= 1.2
    if not best and len(text) >= 4 and text.isupper() and sum(c.isalpha() for c in text) >= 4:
        best, token = 0.3, "CAPS"
    return best, token


class ChatActivityDetector:
    """Detects message-rate spikes relative to an adaptive per-streamer baseline.

    Using a z-score with a Poisson floor on the standard deviation means a small channel going
    from 0.3 to 3 msg/s and a large one going from 8 to 65 msg/s are both recognised, while a
    large channel's normal fluctuation is not.
    """

    def __init__(self, window_s: float = 5.0, sensitivity: float = 0.5) -> None:
        self.window_s = window_s
        self.sensitivity = sensitivity
        self.rate_stats = EWStats(span=300)     # per-second message rate (5 min)
        self.uniq_stats = EWStats(span=300)
        self._msgs: deque[tuple[float, str]] = deque()
        self.last_rate = 0.0

    def add(self, ts: float, user_hash: str) -> None:
        self._msgs.append((ts, user_hash))

    def _trim(self, now: float) -> None:
        # Evaluation time advances monotonically. Batched/late chat can arrive out of
        # timestamp order, so trimming only the head would strand expired messages.
        # Keep future messages: YouTube is evaluated behind the ingestion clock.
        cutoff = now - self.window_s
        self._msgs = deque(item for item in self._msgs if item[0] >= cutoff)

    def evaluate(self, now: float) -> SignalSample:
        """Call about once per second with monotonically advancing evaluation times."""
        self._trim(now)
        recent_users = [u for t, u in self._msgs if t <= now]
        rate = len(recent_users) / self.window_s
        uniq = len(set(recent_users)) / self.window_s
        self.last_rate = rate
        mean = self.rate_stats.mean
        warm = self.rate_stats.n >= 30
        # Poisson-floored standard deviation of a windowed rate estimate
        std = max(self.rate_stats.std, math.sqrt(max(mean, 0.05) / self.window_s), 0.15)
        z = (rate - mean) / std if warm else 0.0
        ratio = rate / max(mean, 0.1)
        u_ratio = uniq / max(self.uniq_stats.mean, 0.1)
        # sensitivity shifts the z-score that maps to "certain"
        z_lo = 2.0 - self.sensitivity            # 1.5 .. 2.0 at default
        z_hi = 7.0 - 3.0 * self.sensitivity
        value = 0.65 * ramp(z, z_lo, z_hi) + 0.2 * ramp(ratio, 1.5, 6.0) + 0.15 * ramp(u_ratio, 1.3, 4.0)
        if rate < 0.4:  # a couple of stray messages are never a spike
            value *= rate / 0.4
        # update baseline; clip extreme values so spikes don't inflate it
        cap = mean + 3 * std if warm else rate
        self.rate_stats.update(min(rate, cap))
        self.uniq_stats.update(min(uniq, self.uniq_stats.mean * 3 + 1 if warm else uniq))
        reason = f"Chat spike {rate:.1f}/s (x{ratio:.1f} baseline)" if value >= 0.5 else ""
        return SignalSample(value if warm else 0.0, reason, {"rate": rate, "baseline": mean, "z": z, "unique": uniq})

    def state(self) -> dict:
        return {"rate": self.rate_stats.state(), "uniq": self.uniq_stats.state()}

    def load_state(self, st: dict | None) -> None:
        if st:
            self.rate_stats.load(st.get("rate"))
            self.uniq_stats.load(st.get("uniq"))
            # treat restored baseline as partially warm so detection works soon after start
            self.rate_stats.n = min(self.rate_stats.n, 20)
            self.uniq_stats.n = min(self.uniq_stats.n, 20)


class ChatReactionDetector:
    """Measures the density of excitement expressions (EN + HU) and reaction emotes."""

    def __init__(self, window_s: float = 6.0, sensitivity: float = 0.5) -> None:
        self.window_s = window_s
        self.sensitivity = sensitivity
        self._items: deque[tuple[float, float, str]] = deque()   # (ts, strength, token)
        self._count: deque[float] = deque()
        self.frac_stats = EWStats(span=300)

    def add(self, ts: float, text: str, emotes: list[str] | None = None) -> None:
        self._count.append(ts)
        strength, token = reaction_strength(text, emotes)
        if strength > 0:
            self._items.append((ts, strength, token))

    def evaluate(self, now: float) -> SignalSample:
        lo = now - self.window_s
        while self._items and self._items[0][0] < lo:
            self._items.popleft()
        while self._count and self._count[0] < lo:
            self._count.popleft()
        # upper bound: when evaluated in the past (batched YouTube chat) newer messages must not count
        total = sum(1 for t in self._count if t <= now)
        items = [it for it in self._items if it[0] <= now]
        weighted = sum(s for _, s, _ in items)
        frac = weighted / total if total else 0.0
        rate = weighted / self.window_s
        base = self.frac_stats.mean
        warm = self.frac_stats.n >= 30
        # need both a high share of reacting messages and a non-trivial absolute amount
        value = ramp(frac - (base if warm else 0.1), 0.1, 0.55 - 0.2 * self.sensitivity) * ramp(rate, 0.15, 1.2)
        self.frac_stats.update(min(frac, base + 0.2) if warm else frac)
        tokens = Counter(t for _, _, t in items if t != "CAPS")
        top = [t for t, _ in tokens.most_common(3)]
        reason = f"Chat reactions: {', '.join(top)}" if value >= 0.5 and top else ""
        return SignalSample(value, reason, {"fraction": frac, "top": top, "rate": rate})

    def state(self) -> dict:
        return {"frac": self.frac_stats.state()}

    def load_state(self, st: dict | None) -> None:
        if st:
            self.frac_stats.load(st.get("frac"))
            self.frac_stats.n = min(self.frac_stats.n, 20)
