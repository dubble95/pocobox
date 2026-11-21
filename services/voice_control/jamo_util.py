"""Korean Jamo phonetic decomposition, weighted Levenshtein distance, and fuzzy matching for PocoBox."""

from __future__ import annotations

import re
from typing import Any, Iterable

# Unicode Hangul boundaries
_SBASE = 0xAC00
_LBASE = 0x1100
_VBASE = 0x1161
_TBASE = 0x11A7
_LCOUNT = 19
_VCOUNT = 21
_TCOUNT = 28
_NCOUNT = _VCOUNT * _TCOUNT  # 588
_SCOUNT = _LCOUNT * _NCOUNT  # 11172

# Compatibility Jamo characters
_CHOSUNG = (
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
)
_JUNGSUNG = (
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
    "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ",
)
_JONGSUNG = (
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
    "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
)

# Phonetic similarity groups (soft substitution cost = 0.35 instead of 1.0)
_PHONETIC_CONSONANT_GROUPS: list[set[str]] = [
    {"ㄱ", "ㅋ", "ㄲ"},
    {"ㄷ", "ㅌ", "ㄸ"},
    {"ㅂ", "ㅍ", "ㅃ"},
    {"ㅈ", "ㅊ", "ㅉ"},
    {"ㅅ", "ㅆ", "ㅎ"},
    {"ㄴ", "ㄹ", "ㅁ", "ㅇ"},  # sonorants/nasals frequently confused in trailing/leading positions
]

_PHONETIC_VOWEL_GROUPS: list[set[str]] = [
    {"ㅐ", "ㅔ"},
    {"ㅒ", "ㅖ"},
    {"ㅗ", "ㅜ"},
    {"ㅓ", "ㅗ"},
    {"ㅏ", "ㅑ"},
    {"ㅓ", "ㅕ"},
    {"ㅜ", "ㅠ"},
    {"ㅡ", "ㅣ"},
    {"ㅘ", "ㅙ", "ㅚ", "ㅝ", "ㅞ", "ㅟ", "ㅢ"},  # dipthongs
]


def decompose_hangul(text: str) -> str:
    """Decompose Korean syllable characters into individual jamo sequences.

    Non-Korean characters (English, numbers, spaces, punctuation) are preserved and lowercased.
    Example: '아이유' -> 'ㅇㅏㅇㅣㅇㅠ'
             '차이코프스키' -> 'ㅊㅏㅇㅣㅋㅗㅍㅡㅅㅡㅋㅣ'
    """
    if not text:
        return ""

    decomposed: list[str] = []
    for ch in text.lower():
        code = ord(ch)
        if _SBASE <= code < _SBASE + _SCOUNT:
            s_index = code - _SBASE
            l_index = s_index // _NCOUNT
            v_index = (s_index % _NCOUNT) // _TCOUNT
            t_index = s_index % _TCOUNT

            decomposed.append(_CHOSUNG[l_index])
            decomposed.append(_JUNGSUNG[v_index])
            if t_index > 0:
                decomposed.append(_JONGSUNG[t_index])
        else:
            decomposed.append(ch)

    return "".join(decomposed)


def _char_substitution_cost(c1: str, c2: str) -> float:
    """Calculate substitution cost between two characters (0.0 to 1.0)."""
    if c1 == c2:
        return 0.0

    # Check consonant phonetic clusters
    for group in _PHONETIC_CONSONANT_GROUPS:
        if c1 in group and c2 in group:
            return 0.35

    # Check vowel phonetic clusters (e.g. ㅐ vs ㅔ)
    for group in _PHONETIC_VOWEL_GROUPS:
        if c1 in group and c2 in group:
            return 0.35

    return 1.0


def jamo_levenshtein_distance(s1: str, s2: str, *, phonetic_weighting: bool = True) -> float:
    """Compute weighted Levenshtein distance between two Jamo-decomposed strings."""
    j1 = decompose_hangul(s1)
    j2 = decompose_hangul(s2)

    len1 = len(j1)
    len2 = len(j2)

    if len1 == 0:
        return float(len2)
    if len2 == 0:
        return float(len1)

    # Dynamic programming 1D buffer
    dp = [float(i) for i in range(len2 + 1)]

    for i in range(1, len1 + 1):
        prev = dp[0]
        dp[0] = float(i)
        c1 = j1[i - 1]

        for j in range(1, len2 + 1):
            temp = dp[j]
            c2 = j2[j - 1]

            cost = _char_substitution_cost(c1, c2) if phonetic_weighting else (0.0 if c1 == c2 else 1.0)
            dp[j] = min(
                dp[j] + 1.0,        # deletion
                dp[j - 1] + 1.0,    # insertion
                prev + cost,        # substitution
            )
            prev = temp

    return dp[len2]


def jamo_phonetic_similarity(s1: str, s2: str) -> float:
    """Calculate phonetic similarity ratio [0.0, 1.0] between two strings using Jamo decomposition."""
    if not s1 or not s2:
        return 0.0 if not s1 and not s2 else 0.0

    s1_clean = "".join(s1.split()).strip()
    s2_clean = "".join(s2.split()).strip()
    if s1_clean == s2_clean:
        return 1.0

    j1 = decompose_hangul(s1_clean)
    j2 = decompose_hangul(s2_clean)
    max_len = max(len(j1), len(j2))
    if max_len == 0:
        return 1.0

    dist = jamo_levenshtein_distance(s1_clean, s2_clean, phonetic_weighting=True)
    sim = max(0.0, 1.0 - (dist / float(max_len)))
    return round(sim, 4)


def find_best_jamo_match(
    query: str,
    candidates: Iterable[str],
    *,
    threshold: float = 0.68,
) -> tuple[str | None, float]:
    """Find the candidate with the highest Jamo phonetic similarity above threshold.

    Returns (best_candidate, similarity_score) or (None, 0.0) if no match meets threshold.
    """
    clean_q = "".join(query.split()).strip()
    if not clean_q:
        return None, 0.0

    best_cand: str | None = None
    best_score: float = 0.0

    for cand in candidates:
        score = jamo_phonetic_similarity(clean_q, cand)
        if score > best_score:
            best_score = score
            best_cand = cand

    if best_cand is not None and best_score >= threshold:
        return best_cand, best_score

    return None, best_score


def match_alias_with_jamo(
    query: str,
    alias_dict: dict[str, Any],
    *,
    threshold: float = 0.68,
) -> tuple[str | None, Any, float]:
    """Fuzzy match a query string against alias dictionary keys using Jamo phonetic similarity.

    Returns (matched_key, value, score) or (None, None, 0.0) if no match above threshold.
    """
    clean_q = "".join(query.split()).strip()
    if not clean_q:
        return None, None, 0.0

    # Exact match first (<1 microsecond)
    if clean_q in alias_dict:
        return clean_q, alias_dict[clean_q], 1.0

    best_key, score = find_best_jamo_match(clean_q, alias_dict.keys(), threshold=threshold)
    if best_key is not None:
        return best_key, alias_dict[best_key], score

    return None, None, score
