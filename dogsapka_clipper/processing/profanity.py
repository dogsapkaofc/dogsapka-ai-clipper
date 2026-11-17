"""Masking swear words in burned-in subtitles: the first and last letter stay, the middle becomes stars.

    FUCK -> F**K      fucking -> f*****g      kurva -> k***a      bazdmeg! -> b*****g!

Matching ignores case and accents. Words are matched as whole words: either an exact form or a stem
that is only ever used as a swear word (so "class", "Scunthorpe", "szarvas" or "fasza…"-free words such as
"faszén" are not touched - risky stems are listed as exact forms only). The transcript itself is never
changed; masking is applied when subtitles are written.
"""
from __future__ import annotations

import re
import unicodedata

# whole words (after accent folding, lower case)
EXACT = {
    # English
    "fuck", "fucks", "fucked", "fucker", "fuckers", "fucking", "fuckin", "fck", "fk", "fuk", "motherfucker",
    "motherfuckers", "motherfucking", "shit", "shits", "shitty", "shitting", "bullshit", "horseshit", "bitch",
    "bitches", "bitching", "cunt", "cunts", "asshole", "assholes", "dickhead", "dickheads", "bastard",
    "bastards", "wanker", "wankers", "twat", "twats", "prick", "pricks", "slut", "sluts", "whore", "whores",
    "pussy", "cock", "cocks", "dick", "dicks", "damn", "goddamn", "goddammit", "jackass", "dumbass",
    "wtf", "stfu", "nigga", "nigger", "faggot", "fag", "retard", "retarded",
    # Hungarian
    "kurva", "kurvak", "kurvara", "kurvaja", "kurvanyad", "kurvaanyad", "bazdmeg", "bazmeg", "baszdmeg",
    "baszki", "bazki", "baszod", "baszom", "baszd", "baszott", "baszni", "baszas", "bassza",
    "geci", "gecik", "gecis", "geciség", "fasz", "faszom", "faszt", "faszba", "faszfej", "faszkalap",
    "faszság", "faszsag", "picsa", "picsaba", "picsat", "picsaja", "szar", "szart", "szarul", "szarság",
    "szarsag", "szaros", "szarhazi", "szarházi", "buzi", "buzik", "kocsog", "köcsög", "kocsogok", "bmeg",
    "bzmeg", "lofasz", "lófasz", "seggfej", "seggfejek", "ribanc", "ribancok", "csicska", "fos", "fosos",
}
# stems that are only used as swear words: any word starting with them is masked
STEMS = ("fuck", "motherf", "shit", "bitch", "cunt", "asshole", "kurv", "bazdm", "baszdm", "gecis",
         "faszfej", "faszkal", "picsáb", "picsab", "seggfej", "ribanc", "köcsög", "kocsog")

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


_EXACT_F = {_fold(w) for w in EXACT}
_STEMS_F = tuple(_fold(s) for s in STEMS)


def is_profane(word: str) -> bool:
    w = _fold(word)
    if len(w) < 2:
        return False
    return w in _EXACT_F or w.startswith(_STEMS_F)


def _mask_word(word: str) -> str:
    if len(word) <= 2:
        return word[0] + "*" * (len(word) - 1)
    return word[0] + "*" * (len(word) - 2) + word[-1]


def mask_text(text: str) -> str:
    """Mask every swear word in a string, keeping punctuation and spacing."""
    return _WORD.sub(lambda m: _mask_word(m.group(0)) if is_profane(m.group(0)) else m.group(0), text or "")


def mask_words(words: list[dict]) -> list[dict]:
    """Word-timed transcript words (``{"word": …}``) with swear words masked (copies, input untouched)."""
    return [{**w, "word": mask_text(w.get("word", ""))} for w in words]
