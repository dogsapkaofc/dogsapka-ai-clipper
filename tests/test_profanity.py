"""Swear words in subtitles: first and last letter kept, middle starred."""
import pytest

from dogsapka_clipper.processing.profanity import is_profane, mask_text, mask_words
from dogsapka_clipper.processing.subtitles import build_captions, write_ass, write_srt


@pytest.mark.parametrize("raw,masked", [
    ("FUCK", "F**K"),
    ("fucking hell!", "f*****g hell!"),
    ("Shit, no way", "S**t, no way"),
    ("motherfucker", "m**********r"),
    ("Kurva jó volt", "K***a jó volt"),
    ("BAZDMEG!", "B*****G!"),
    ("ez szar", "ez s**r"),
    ("a picsába", "a p*****a"),
    ("köcsög", "k****g"),
    ("WTF", "W*F"),
])
def test_masks_first_and_last_letter_kept(raw, masked):
    assert mask_text(raw) == masked


@pytest.mark.parametrize("clean", [
    "class assessment Scunthorpe", "szarvas szarka", "basszus", "a baszk nyelv", "faszén", "hello world",
    "Gecse", "picsányi"])
def test_innocent_words_untouched(clean):
    assert mask_text(clean) == clean


def test_accent_and_case_insensitive():
    assert is_profane("KÖCSÖG") and is_profane("kocsog") and is_profane("SzArHáZi")


def test_mask_words_keeps_timing_and_input():
    words = [{"start": 1.0, "end": 1.4, "word": "fuck", "prob": 0.9}, {"start": 1.5, "end": 1.9, "word": "yes"}]
    out = mask_words(words)
    assert out[0] == {"start": 1.0, "end": 1.4, "word": "f**k", "prob": 0.9}
    assert words[0]["word"] == "fuck"                       # transcript itself is not changed


def test_written_subtitle_files_contain_masked_words(tmp_path):
    words = mask_words([{"start": 0.5 + i * 0.4, "end": 0.85 + i * 0.4, "word": w, "prob": 0.9}
                        for i, w in enumerate(["what", "the", "fuck", "kurva", "jó"])])
    ass = write_ass(words, tmp_path / "s.ass", 1080, 1920, style="viral").read_text(encoding="utf-8")
    srt = write_srt(build_captions(words, max_words=7, max_chars=42), tmp_path / "s.srt").read_text(encoding="utf-8")
    for txt in (ass, srt):
        assert "fuck" not in txt.lower() and "kurva" not in txt.lower()
    assert "F**K" in ass.upper() and "f**k" in srt
