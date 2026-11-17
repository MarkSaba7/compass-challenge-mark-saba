"""Analyse d'un document : texte -> type + faits [type d'entité, identifiant, champ, valeur].

Exemple de fait : ["invoice", "F-2021-1393", "total", "27479.03"]. Aucun appel au modèle ici.
"""
import re

from . import textnorm as tn

PARSER_VERSION = 2  # à incrémenter quand l'analyse change : invalide le cache

PROJECT_RE = re.compile(r"\bBRV-\d{4}-\d{2,3}\b")
INVOICE_RE = re.compile(r"\bF-\d{4}-\d{3,5}\b")
CONTRACT_RE = re.compile(r"\bC-\d{4}-\d{2,3}\b")
NUMERO_RE = re.compile(r"\b(?:n[°o]|no\.)\s*(\d+)", re.I)
SECTION_RE = re.compile(r"^(\d)\.\s+(\S.*)$")

HEADERS = [
    ("invoice", ["facture n", "invoice no"]),
    ("report", ["rapport mensuel d'avancement", "monthly progress report"]),
    ("site_meeting", ["reunion de chantier", "site meeting"]),
    ("tech_note", ["note technique", "technical note"]),
    ("project_sheet", ["fiche de projet", "project charter"]),
    ("kickoff", ["proces-verbal —", "minutes —"]),
    ("memo", ["note de service", "memo —"]),
    ("contract", ["contrat de sous-traitance", "contrat de services d'entretien", "subcontract agreement",
                  "maintenance services agreement"]),
    ("committee", ["comite de direction", "management committee"]),
    ("directory", ["bottin du personnel", "staff directory"]),
    ("newsletter", ["brumeval monthly", "brumeval mensuel"]),
]

L_INVOICE = tn.labels({
    "facture n": "number", "invoice no": "number", "date": "date", "facture a": "client", "bill to": "client", "a l'attention de": "attention",
    "attention": "attention", "projet": "project", "project": "project", "conditions": "terms", "terms": "terms",
    "sous-total": "subtotal", "subtotal": "subtotal", "tps": "gst", "gst": "gst", "tvq": "qst", "qst": "qst",
    "total": "total"})
L_SHEET = tn.labels({
    "numero de projet": "id", "project number": "id", "client": "client", "municipalite": "location",
    "location": "location", "chargee de projet": "manager", "charge de projet": "manager",
    "project manager": "manager", "date de debut": "start_date", "start date": "start_date",
    "date de fin prevue": "end_date", "planned end date": "end_date", "budget approuve": "budget",
    "approved budget": "budget", "sous-traitant": "subcontractor", "subcontractor": "subcontractor",
    "date du rapport": "sheet_date", "report date": "sheet_date", "prepare par": "prepared_by",
    "prepared by": "prepared_by"})
L_MEETING = tn.labels({
    "date": "date", "lieu": "location", "location": "location", "presences": "attendees",
    "attendees": "attendees", "present": "attendees", "periode": "period", "period": "period",
    "prepare par": "prepared_by", "prepared by": "prepared_by"})
L_MEMO = tn.labels({
    "date": "date", "destinataires": "to", "to": "to", "expediteur": "from", "from": "from",
    "de": "from", "a": "to", "objet": "subject", "subject": "subject"})
L_CONTRACT = tn.labels({
    "et": "contractor", "and": "contractor",
    "signe pour brumeval ingenierie inc.": "signatory", "signed for brumeval ingenierie inc.": "signatory"})
L_END_DATE = ["date de fin prevue", "planned end date"]

MONEY_FIELDS = {"subtotal", "gst", "qst", "total", "budget", "price", "annual_fee"}
DATE_FIELDS = {"date", "start_date", "end_date", "sheet_date", "due_date", "effective_date", "approved_date"}


def _starts_like(folded_line: str, prefix: str) -> bool:
    if folded_line.startswith(prefix):
        return True
    return len(folded_line) >= len(prefix) - 1 and tn.similar(folded_line[:len(prefix)], prefix) >= 0.82


def detect_type(lines: list[str]) -> tuple[str, int]:
    """Type du document et index de sa ligne de titre."""
    head = [tn.fold(l) for l in lines[:8]]
    for i, f in enumerate(head[:3]):
        if re.match(r"(de|from)\s?:.*@", f):
            return "email", i
    for i, f in enumerate(head[:5]):
        m = re.match(r"\w{5,9} \d+ — ", f)
        if i > 0 and m and tn.similar(f[:7], "version") >= 0.7:
            return "policy", i - 1
    for fuzzy in (False, True):
        for i, f in enumerate(head):
            for kind, prefixes in HEADERS:
                for p in prefixes:
                    if f.startswith(p) or (fuzzy and len(f) < 120 and _starts_like(f, p)):
                        return kind, i
    return "other", 0


def _fields(lines: list[str], labs) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        hit = tn.split_label(line.strip(), labs)
        if hit and hit[0] not in out and hit[1]:
            out[hit[0]] = hit[1]
    return out


def _sections(lines: list[str]) -> dict[str, list[str]]:
    """Sections numérotées « 3. Prix » -> {"3": [lignes de contenu]}. Le numéro résiste aux coquilles."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in lines:
        m = SECTION_RE.match(line.strip())
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
        elif cur:
            out[cur].append(line.strip())
    return out


def _after_dash(line: str) -> str:
    return line.split("—", 1)[1].strip() if "—" in line else ""


def _project_name(text: str) -> str | None:
    """« Projet : Bassin de rétention de X (BRV-2023-06) », parfois sur deux lignes."""
    m = re.search(r":\s*([^:()]{6,160}?)\s*\(BRV-\d{4}-\d{2,3}\)", text[:700])
    return " ".join(m.group(1).split()) if m else None


def _progress(text: str) -> str | None:
    m = re.search(r"(\d{1,3})\s?%", text)
    return m.group(1) if m else None


def _norm(field: str, value: str) -> str | None:
    value = value.strip()
    if field in MONEY_FIELDS:
        return tn.money(value.rsplit(":", 1)[-1])
    if field in DATE_FIELDS:
        return tn.date(value)
    return " ".join(value.split()) or None


def _invoice(doc_id, lines, text, facts):
    m = INVOICE_RE.search(text)
    if not m:
        return
    fid = m.group(0)
    f = _fields(lines, L_INVOICE)
    vals = {k: _norm(k, v) for k, v in f.items() if k not in ("terms", "number", "project")}
    if not vals.get("date"):
        vals["date"] = tn.date(text[:400])
    p = PROJECT_RE.search(text)
    vals["project"] = p.group(0) if p else None
    if "terms" in f:
        vals["terms_days"] = tn.integer(f["terms"])
        vals["due_date"] = tn.date(f["terms"])
    # filet de sécurité si une étiquette de montant est trop abîmée pour être reconnue
    parts = [vals.get(k) for k in ("subtotal", "gst", "qst")]
    if not vals.get("total") and all(parts):
        vals["total"] = f"{sum(map(float, parts)):.2f}"
    if not vals.get("subtotal") and vals.get("total"):
        vals["subtotal"] = f"{float(vals['total']) / 1.14975:.2f}"
    for k, v in vals.items():
        if v:
            facts.append(["invoice", fid, k, v])


def _project_sheet(doc_id, lines, text, facts, title_i):
    f = _fields(lines[title_i + 1:], L_SHEET)  # le titre « Fiche de projet » ressemble trop à une étiquette
    p = PROJECT_RE.search(f.get("id", "")) or PROJECT_RE.search(text)
    if not p:
        return
    brv = p.group(0)
    name = _after_dash(lines[title_i])
    for nxt in lines[title_i + 1:title_i + 3]:  # titre replié sur la ligne suivante
        if tn.split_label(nxt.strip(), L_SHEET):
            break
        name += " " + nxt.strip()
    facts.append(["project", brv, "name", " ".join(name.split())])
    for k, v in f.items():
        v = _norm(k, v)
        if v and k != "id":
            facts.append(["project", brv, k, v])
    prog = _progress(text)
    if prog:
        facts.append(["project", brv, "progress", prog])


def _meeting(doc_id, kind, lines, text, facts, title_i):
    p = PROJECT_RE.search(text[:700])
    if not p:
        return
    brv = p.group(0)
    f = _fields(lines[:title_i + 8], L_MEETING)
    name = _project_name(text)
    if name:
        facts.append(["project", brv, "alias", name])
    if kind == "report":
        n = NUMERO_RE.search(lines[title_i])
        eid = f"{brv}/rapport-{n.group(1) if n else doc_id}"
        vals = {"number": n.group(1) if n else None, "period": tn.period(f.get("period", "")),
                "progress": _progress(text)}
        etype = "report"
    elif kind == "tech_note":
        eid, etype = doc_id, "note"
        vals = {"kind": _after_dash(lines[title_i]), "date": tn.date(f.get("date", ""))}
    else:
        n = NUMERO_RE.search(lines[title_i])
        etype = "meeting"
        vals = {"date": tn.date(f.get("date", "")) or tn.date(text[:500]), "location": f.get("location"),
                "attendees": f.get("attendees")}
        if kind == "kickoff":
            eid = f"{brv}/{tn.key(_after_dash(lines[title_i])).replace(' ', '-') or 'pv'}"
            vals["kind"] = _after_dash(lines[title_i])
            m = re.search(r"[Tt]otal\w*,?\s+(?:of\s+)?(\d+)\s", text)
            vals["bids_received"] = m.group(1) if m else None
        else:
            eid = f"{brv}/chantier-{n.group(1) if n else doc_id}"
            vals["kind"], vals["number"] = "site meeting", n.group(1) if n else None
    facts.append([etype, eid, "project", brv])
    for k, v in vals.items():
        if v:
            facts.append([etype, eid, k, " ".join(str(v).split())])
    # une date de fin annoncée en réunion est un fait sur le projet (peut contredire la fiche)
    for sentence in re.split(r"(?<=[.;])\s+|\n", text):
        fs = tn.fold(sentence)
        if any(lab in fs or tn.similar(fs[:len(lab) + 3], "la " + lab) > 0.85 for lab in L_END_DATE):
            d = tn.date(sentence)
            if d:
                facts.append(["project", brv, "end_date", d])


def _contract(doc_id, lines, text, facts, title_i):
    m = CONTRACT_RE.search(text)
    if not m:
        return
    cid = m.group(0)
    head = tn.fold(lines[title_i])
    kind = "maintenance" if ("entretien" in head or "maintenance" in head or tn.similar(head[:20], "contrat de services d") > 0.8
                             or tn.similar(head[:20], "maintenance services") > 0.8) else "subcontract"
    f = _fields(lines, L_CONTRACT)
    sec = {k: " ".join(v) for k, v in _sections(lines).items()}
    vals = {"kind": kind, "signatory": f.get("signatory")}
    if "contractor" in f:
        vals["contractor"] = re.sub(r"\s*\((?:le|the) .*$", "", f["contractor"]).strip()
    scope = sec.get("1", "")
    vals["scope"] = scope.split(".")[0].strip() if scope else None
    p = PROJECT_RE.search(scope) or PROJECT_RE.search(text)
    vals["project"] = p.group(0) if p else None
    term = sec.get("2", "").split(". ")[0]
    vals["start_date"] = tn.date(term)
    dur = re.findall(r"(\d+)\s+[^\W\d]+\.?\s*$", term.strip())
    vals["duration_months"] = dur[-1] if dur else None
    vals["annual_fee" if kind == "maintenance" else "price"] = tn.money(sec.get("3", "").split(" taxes")[0])
    vals["payment_days"] = tn.integer(sec.get("4", "").split(". ")[0])
    vals["termination_notice_days"] = tn.integer(sec.get("5", "").split(". ")[0])
    for k, v in vals.items():
        if v:
            facts.append(["contract", cid, k, v])


def _policy(doc_id, lines, text, facts, title_i):
    name = lines[title_i].strip()
    vline = lines[title_i + 1]
    version = tn.integer(vline) or "1"
    vals = {"name": name, "version": version, "effective_date": tn.date(_after_dash(vline))}
    for line in lines[title_i + 2:title_i + 5]:
        fl = tn.fold(line)
        if "version" in fl and ("remplace" in fl or "replace" in fl or re.search(r"version \d", fl)) and tn.date(line):
            m = re.search(r"version (\d+)", fl)
            vals["replaces_version"] = m.group(1) if m else None
        elif tn.date(line) and "approved_date" not in vals:
            vals["approved_date"] = tn.date(line)
    body = _sections(lines).get("3", [])
    if body:
        first = body[0]
        for extra in body[1:3]:  # la disposition peut se replier sur deux lignes
            if first.rstrip().endswith("."):
                break
            first += " " + extra
        vals["provision"] = first.split(". ")[0].strip()
    for k, v in vals.items():
        if v:
            facts.append(["policy", doc_id, k, v])


def _directory(doc_id, lines, text, facts):
    facts.append(["directory", doc_id, "date", tn.date(text[:300]) or ""])
    for line in lines:
        m = re.match(r"^(.+?)\s+(\d{3})\s+(\S+@\S+)\s*$", line.strip())
        if not m:
            continue
        tokens = m.group(1).split()
        n = m.group(3).split("@")[0].count(".") + 1  # prénom.nom -> 2 mots dans le nom
        name, title = " ".join(tokens[:n]), " ".join(tokens[n:])
        for k, v in (("title", title), ("extension", m.group(2)), ("email", m.group(3))):
            facts.append(["employee", name, k, v])


def _memo(doc_id, lines, text, facts, title_i):
    f = _fields(lines[title_i + 1:title_i + 5], L_MEMO)
    title = _after_dash(lines[title_i])
    body = [l.strip() for l in lines[title_i + 1:] if l.strip() and not tn.split_label(l.strip(), L_MEMO)]
    announce = body[0] if body else ""
    vals = {"title": title, "from": f.get("from"), "date": tn.date(f.get("date", "")), "announcement": announce}
    for k, v in vals.items():
        if v:
            facts.append(["memo", doc_id, k, v])
    m = re.search(r"(?:bienvenue [àa]|welcome)\s+([^,]{4,60}),", announce, re.I)
    dates = tn.find_dates(announce)
    if m and dates:
        facts.append(["employee", m.group(1).strip(), "start_date", dates[-1]])


def _committee(doc_id, lines, text, facts, title_i):
    f = _fields(lines[title_i + 1:title_i + 5], L_MEETING)
    facts.append(["committee", doc_id, "date", tn.date(f.get("date", "")) or tn.date(text[:300]) or ""])
    if f.get("attendees"):
        facts.append(["committee", doc_id, "attendees", f["attendees"]])
    for sentence in re.split(r"(?<=[.;])\s+|\n", text):
        fs = tn.fold(sentence)
        if re.search(r"propo\w+", fs) and tn.find_money(sentence):
            facts.append(["committee", doc_id, "proposal", " ".join(sentence.split())])


def _email(doc_id, lines, text, facts):
    f = _fields(lines[:6], L_MEMO)
    for k in ("from", "to", "subject"):
        if f.get(k):
            facts.append(["email", doc_id, k, f[k]])
    facts.append(["email", doc_id, "date", tn.date(f.get("date", "")) or ""])
    body = text.replace("\n", " ")
    for sentence in re.split(r"(?<=[.;])\s+", body):
        p = PROJECT_RE.search(sentence)
        if p and "budget" in tn.fold(sentence):
            name = re.search(r"(?:pour le projet|for the)\s+(.+?)\s*(?:project\s*)?\(BRV", sentence)
            if name:
                facts.append(["project", p.group(0), "alias", name.group(1).strip()])
            m = tn.find_money(sentence.split(p.group(0), 1)[1])
            if m:
                facts.append(["project", p.group(0), "budget", f"{m[0]:.2f}"])


def parse_document(doc_id: str, text: str) -> dict:
    lines = [l for l in text.split("\n") if l.strip()]
    kind, title_i = detect_type(lines) if lines else ("other", 0)
    facts: list[list[str]] = []
    try:
        if kind == "invoice":
            _invoice(doc_id, lines, text, facts)
        elif kind == "project_sheet":
            _project_sheet(doc_id, lines, text, facts, title_i)
        elif kind in ("kickoff", "site_meeting", "report", "tech_note"):
            _meeting(doc_id, kind, lines, text, facts, title_i)
        elif kind == "contract":
            _contract(doc_id, lines, text, facts, title_i)
        elif kind == "policy":
            _policy(doc_id, lines, text, facts, title_i)
        elif kind == "directory":
            _directory(doc_id, lines, text, facts)
        elif kind == "memo":
            _memo(doc_id, lines, text, facts, title_i)
        elif kind == "committee":
            _committee(doc_id, lines, text, facts, title_i)
        elif kind == "email":
            _email(doc_id, lines, text, facts)
    except Exception as e:  # un document atypique reste cherchable en plein texte
        facts.append(["doc", doc_id, "parse_error", repr(e)])
    fr = len(re.findall(r"\b(le|la|les|des|du|est|une|pour)\b", text[:1500].lower()))
    en = len(re.findall(r"\b(the|of|is|and|for|with|was)\b", text[:1500].lower()))
    return {
        "id": doc_id, "type": kind, "lang": "fr" if fr >= en else "en",
        "title": lines[title_i].strip() if lines else "",
        "date": next((f[3] for f in facts if f[2] in ("date", "sheet_date", "effective_date") and f[3]), None)
                or tn.date(text[:500]),
        "ids": sorted(set(PROJECT_RE.findall(text) + INVOICE_RE.findall(text) + CONTRACT_RE.findall(text))),
        "facts": [f for f in facts if f[3]],
        "text": text,
    }
