"""Normalisation du texte : accents, étiquettes avec coquilles, montants, dates."""
import difflib
import re
import unicodedata


def fold(s: str) -> str:
    """Minuscules sans accents, de même longueur que l'entrée (les positions restent valides)."""
    out = []
    for c in s:
        base = next((x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x)), " ")
        out.append("'" if base in "’`´" else base.lower()[0])
    return "".join(out)


def key(s: str) -> str:
    """Clé de comparaison : mots sans accents ni ponctuation, « 1 » pris pour un « l » dans un mot."""
    f = re.sub(r"(?<=[a-z])1|1(?=[a-z])", "l", fold(str(s)))
    return " ".join(re.findall(r"[a-z0-9]+", f))


def tokens(s: str) -> list[str]:
    """Mots d'un long texte pour la recherche plein texte (version rapide de key)."""
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()
    return re.findall(r"[a-z0-9]{2,}", s)


def has_phrase(haystack_key: str, needle_key: str) -> bool:
    """Présence d'une expression entière (« saint fictif » n'est pas dans « sainte fictif »)."""
    return bool(needle_key) and f" {needle_key} " in f" {haystack_key} "


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def best_match(name: str, candidates: dict[str, str], cutoff: float = 0.86) -> str | None:
    """Retrouve la forme canonique d'un nom malgré une coquille. candidates : clé -> forme canonique."""
    k = key(name)
    if k in candidates:
        return candidates[k]
    close = difflib.get_close_matches(k, candidates, 1, cutoff)
    return candidates[close[0]] if close else None


def split_label(line: str, labels: list[tuple[str, str]]) -> tuple[str, str] | None:
    """« Budget aprouvé 1 494 000 $ » -> ("budget", "1 494 000 $"). labels : (étiquette pliée, champ)."""
    f = fold(line)
    for lab, field in labels:
        n = len(lab)
        if f.startswith(lab) and (len(f) == n or f[n] in " :\t"):
            return field, line[n:].lstrip(" :\t")
    for lab, field in labels:
        n = len(lab)
        if n < 6:
            continue  # trop court pour une comparaison floue fiable
        for d in (0, -1, 1):
            cut = n + d
            if len(f) >= cut and (len(f) == cut or f[cut] in " :\t") and similar(f[:cut], lab) >= (0.8 if n < 10 else 0.86):
                return field, line[cut:].lstrip(" :\t")
    return None


def labels(mapping: dict[str, str]) -> list[tuple[str, str]]:
    """Trie les étiquettes de la plus longue à la plus courte (« date de fin prévue » avant « date »)."""
    return sorted(((fold(k), v) for k, v in mapping.items()), key=lambda x: -len(x[0]))


_SP = "    "
MONEY_RE = re.compile(
    rf"(?:CAD|\$)\s?\d[\d{_SP}.,]*\d|(?:CAD|\$)\s?\d|\d[\d{_SP}.,]*\d\s?(?:\$|CAD)|\d\s?(?:\$|CAD)")


def parse_number(raw: str) -> float | None:
    """« 12 500,00 », « 12,500.00 », « 1 494 000 », « 0,47 » -> float."""
    raw = re.sub(r"[^\d.,]", "", raw).strip(".,")
    if not raw:
        return None
    if "," in raw and "." in raw:
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
        raw = raw.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in raw:
        tail = raw.rpartition(",")[2]
        raw = raw.replace(",", "") if raw.count(",") > 1 or len(tail) == 3 else raw.replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def find_money(text: str) -> list[float]:
    return [v for v in (parse_number(m.group(0)) for m in MONEY_RE.finditer(text)) if v is not None]


def money(text: str) -> str | None:
    """Premier montant du texte, au format « 1234.56 »."""
    found = find_money(text)
    if not found:
        v = parse_number(text) if re.fullmatch(rf"[\d{_SP}.,]+", text.strip()) else None
        found = [v] if v is not None else []
    return f"{found[0]:.2f}" if found else None


MONTHS = {
    "janvier": 1, "january": 1, "fevrier": 2, "february": 2, "mars": 3, "march": 3, "avril": 4, "april": 4,
    "mai": 5, "may": 5, "juin": 6, "june": 6, "juillet": 7, "july": 7, "aout": 8, "august": 8,
    "septembre": 9, "september": 9, "octobre": 10, "october": 10, "novembre": 11, "november": 11,
    "decembre": 12, "december": 12,
}
_W = r"[^\W\d_]{3,10}"
_DATE_RES = [
    ("ymd", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")),
    ("dmy", re.compile(rf"\b(\d{{1,2}})(?:er|st|nd|rd|th)?\s+({_W})\.?,?\s+(\d{{4}})\b")),
    ("mdy", re.compile(rf"\b({_W})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")),
    ("slash", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")),  # jour/mois/année dans ce corpus
]
_PERIOD_RE = re.compile(rf"\b({_W})\s+(\d{{4}})\b")


def month_num(word: str) -> int | None:
    k = key(word)
    if k in MONTHS:
        return MONTHS[k]
    close = difflib.get_close_matches(k, MONTHS, 1, 0.75) if len(k) >= 4 else []
    return MONTHS[close[0]] if close else None


def _iso(y: int, m: int | None, d: int) -> str | None:
    if m and 1 <= m <= 12 and 1 <= d <= 31 and 1990 <= y <= 2100:
        return f"{y:04d}-{m:02d}-{d:02d}"
    return None


def find_dates(text: str) -> list[str]:
    """Toutes les dates du texte, dans l'ordre de lecture, au format ISO."""
    hits = []
    for kind, rx in _DATE_RES:
        for m in rx.finditer(text):
            g = m.groups()
            if kind == "ymd":
                iso = _iso(int(g[0]), int(g[1]), int(g[2]))
            elif kind == "dmy":
                iso = _iso(int(g[2]), month_num(g[1]), int(g[0]))
            elif kind == "mdy":
                iso = _iso(int(g[2]), month_num(g[0]), int(g[1]))
            else:
                y = int(g[2]) + (2000 if len(g[2]) == 2 else 0)
                iso = _iso(y, int(g[1]), int(g[0]))
            if iso:
                hits.append((m.start(), iso))
    return [iso for _, iso in sorted(hits)]


def date(text: str) -> str | None:
    found = find_dates(text)
    return found[0] if found else None


def period(text: str) -> str | None:
    """« décembre 2024 » -> « 2024-12 »."""
    for m in _PERIOD_RE.finditer(text):
        n = month_num(m.group(1))
        if n:
            return f"{int(m.group(2)):04d}-{n:02d}"
    return None


def integer(text: str) -> str | None:
    m = re.search(r"\d+", text)
    return m.group(0) if m else None
