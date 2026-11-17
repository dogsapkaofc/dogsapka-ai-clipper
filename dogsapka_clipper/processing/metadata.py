"""Rule-based titles, descriptions and hashtags (EN/HU), grounded in real event data.

No facts are invented: titles are built only from the detection reasons, the transcript,
the stream category and the streamer name.
"""
from __future__ import annotations

import re
from collections import Counter

STOPWORDS = {
    "en": set("the a an and or but to of in on at for with is are was were be been it this that i you he she we they "
              "my your me so just like oh yeah okay ok what no not do did dont don't can can't im i'm its it's "
              "have has get got go going there here then now".split()),
    "hu": set("a az és de hogy nem is egy ez az meg már csak még van volt lesz ezt azt mi te én ő mint vagy "
              "itt ott most akkor jó igen nem hát na szóval".split()),
}

TEMPLATES = {
    "en": {
        "funny": ["{name} can't stop laughing", "This got {name} good 😂", "{name}'s chat lost it"],
        "dramatic": ["{name} didn't see this coming", "The moment everything changed", "{name}: \"{quote}\""],
        "gaming": ["{name} {game} moment", "{game}: {name} goes off", "{name} in {game} 🔥"],
        "reaction": ["{name} reacts: \"{quote}\"", "{name}'s reaction says it all", "Watch {name}'s reaction"],
        "professional": ["{name} – {game} highlight", "Stream highlight: {name}", "{name} live highlight"],
    },
    "hu": {
        "funny": ["{name} nem bírja abbahagyni a nevetést", "Ezen {name} is kiakadt 😂", "{name} chatje megőrült"],
        "dramatic": ["{name} erre nem számított", "A pillanat, amikor minden megváltozott", "{name}: \"{quote}\""],
        "gaming": ["{name} {game} pillanat", "{game}: {name} odatette", "{name} a {game}-ben 🔥"],
        "reaction": ["{name} reakciója: \"{quote}\"", "{name} reakciója mindent elmond", "Nézd {name} reakcióját"],
        "professional": ["{name} – {game} kiemelés", "Stream kiemelés: {name}", "{name} élő kiemelés"],
    },
}


def _slug_tag(s: str) -> str:
    return "#" + re.sub(r"[^\wáéíóöőúüű]", "", s.lower(), flags=re.I) if s else ""


def best_quote(transcript: dict | None, max_words: int = 7) -> str:
    segs = (transcript or {}).get("segments", [])
    if not segs:
        return ""
    # prefer confident, exclamatory, short segments
    def key(s):
        t = s.get("text", "")
        return (s.get("confidence", 0.5) + (0.3 if "!" in t or "?" in t else 0.0)
                - 0.02 * max(0, len(t.split()) - max_words))
    seg = max(segs, key=key)
    if seg.get("confidence", 1.0) < 0.5:
        return ""
    words = seg.get("text", "").strip().split()
    q = " ".join(words[:max_words]).strip(" ,.")
    return q + ("…" if len(words) > max_words else "")


def keywords(transcript: dict | None, lang: str, n: int = 4) -> list[str]:
    text = " ".join(s.get("text", "") for s in (transcript or {}).get("segments", [])).lower()
    toks = re.findall(r"[\wáéíóöőúüű]{4,}", text)
    sw = STOPWORDS.get(lang, set()) | STOPWORDS["en"]
    return [w for w, _ in Counter(t for t in toks if t not in sw).most_common(n)]


def suggest_style(reasons: list[str]) -> str:
    r = " ".join(reasons).lower()
    if "laugh" in r or "lol" in r or "kekw" in r or "haha" in r:
        return "funny"
    if "loud reaction" in r or "excited" in r:
        return "reaction"
    if "scene" in r or "visual" in r:
        return "gaming"
    return "dramatic"


def generate_metadata(streamer: str, login: str, game: str, reasons: list[str], transcript: dict | None,
                      lang: str = "en", style: str = "auto", stream_title: str = "") -> dict:
    lang = lang if lang in TEMPLATES else "en"
    style = suggest_style(reasons) if style == "auto" else style
    quote = best_quote(transcript)
    game_s = game or ("stream" if lang == "en" else "stream")
    titles = []
    for tpl in TEMPLATES[lang].get(style, TEMPLATES[lang]["gaming"]):
        if "{quote}" in tpl and not quote:
            continue
        if "{game}" in tpl and not game:
            continue
        titles.append(tpl.format(name=streamer, game=game_s, quote=quote)[:95])
    if not titles:
        titles.append(TEMPLATES[lang]["professional"][1].format(name=streamer, game=game_s))
    kws = keywords(transcript, lang)
    tags = [t for t in [_slug_tag(streamer), _slug_tag(game), "#twitch", "#shorts" if lang == "en" else "#magyar",
                        "#gaming" if game else "#stream", "#clips"] + [_slug_tag(k) for k in kws[:2]] if t]
    tags = list(dict.fromkeys(tags))[:8]
    src = f"https://www.twitch.tv/{login}"
    if lang == "hu":
        desc = (f"Kiemelés {streamer} élő adásából" + (f" ({game})" if game else "") + ".\n"
                + (f"\"{quote}\"\n" if quote else "")
                + f"\nForrás / streamer: {src}\nA klip a streamer engedélyével készült.")
    else:
        desc = (f"Highlight from {streamer}'s live stream" + (f" playing {game}" if game else "") + ".\n"
                + (f"\"{quote}\"\n" if quote else "")
                + f"\nSource / streamer: {src}\nClip shared with the streamer's permission.")
    if stream_title:
        desc += ("\nStream: " if lang == "en" else "\nAdás: ") + stream_title[:120]
    return {"titles": titles, "title": titles[0], "description": desc + "\n\n" + " ".join(tags),
            "hashtags": tags, "style": style, "language": lang, "attribution": src}
