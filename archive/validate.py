"""Validation : toute réponse (du modèle ou des règles) passe par ici avant d'être écrite."""
import json
import re

from . import textnorm as tn

STATUSES = ("answered", "not_found", "conflict")
DOC_RE = re.compile(r"doc_\d{3,5}")
NOT_FOUND = {"status": "not_found", "answer": "", "citations": []}


def extract_json(text):
    """Récupère l'objet JSON d'une réponse, même entouré de texte ou de ```json."""
    if not isinstance(text, str) or "{" not in text or "}" not in text:
        return None
    chunk = text[text.find("{"):text.rfind("}") + 1]
    for candidate in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):  # 2e essai sans virgules finales
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def same_value(a, b):
    """Deux valeurs sont-elles égales, peu importe le format (date, montant ou texte) ?"""
    if tn.date(a) or tn.date(b):
        return tn.date(a) == tn.date(b)
    na, nb = tn.parse_number(a), tn.parse_number(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= 0.005 * max(abs(na), abs(nb))
    return tn.key(a) != "" and tn.key(a) == tn.key(b)


def appears_in(answer, text):
    """La valeur répondue figure-t-elle dans ce texte ?"""
    d = tn.date(answer)
    if d:
        return d in tn.find_dates(text)
    n = tn.parse_number(answer)
    if n is not None and not re.search(r"[A-Za-z]{3}", answer):
        return any(abs(v - n) < 0.006 for v in tn.find_money(text) + [float(x) for x in re.findall(r"\b\d+\b", text)])
    return tn.key(answer) in tn.key(text)


def reconcile(model, rules):
    """Croise la réponse du modèle avec celle des règles (base de faits). Retourne (réponse, note).

    - les règles voient deux valeurs pour le champ demandé : conflit, quoi que dise le modèle ;
    - même valeur des deux côtés : on garde les citations des règles, qui suivent toute la chaîne.
    """
    if rules["status"] == "conflict":
        return rules, "contradiction vue par le code"
    said = model.get("answer")
    abstained = str(model.get("status", "")).lower() in ("not_found", "conflict")
    if rules["status"] == "answered" and not abstained and isinstance(said, (str, int, float)) \
            and same_value(str(said), str(rules["answer"])):
        return {"status": "answered", "answer": rules["answer"], "citations": rules["citations"]}, \
            "modèle et base de faits d'accord"
    return model, ""


def finalize(raw, allowed_docs, known_docs, conflicts=()):
    """Met la réponse au format de sortie et applique les contrôles. Retourne (réponse, notes)."""
    notes = []
    if not isinstance(raw, dict):
        return dict(NOT_FOUND), ["sortie illisible"]

    answer = raw.get("answer")
    if isinstance(answer, list):
        answer = [str(a).strip() for a in answer if str(a).strip()]
    else:
        answer = "" if answer is None else str(answer).strip()
    status = str(raw.get("status", "")).strip().lower()
    if status not in STATUSES:
        status = "answered" if answer else "not_found"
        notes.append("statut invalide, déduit de la réponse")

    # citations : seulement des documents qui existent et qui ont été montrés au modèle
    citations = []
    for c in DOC_RE.findall(str(raw.get("citations", ""))):
        if c not in known_docs or (allowed_docs and c not in allowed_docs):
            notes.append(f"citation retirée : {c}")
        elif c not in citations:
            citations.append(c)

    # si la réponse reprend une des valeurs d'une contradiction connue, c'est un conflit
    if status == "answered" and isinstance(answer, str):
        for c in conflicts:
            if any(same_value(answer, v) for v, _ in c["values"]):
                status = "conflict"
                answer = " / ".join(v for v, _ in c["values"])
                citations = list(dict.fromkeys(d for _, docs in c["values"] for d in docs))
                notes.append(f"contradiction sur {c['id']} ({c['field']})")
                break

    if status == "not_found" or not answer:
        return dict(NOT_FOUND), notes
    if status == "answered" and not citations and isinstance(answer, str):
        # le modèle n'a rien cité : on cite les documents du dossier où la valeur apparaît
        citations = [d for d in allowed_docs if appears_in(answer, known_docs[d]["text"])][:3]
        notes.append("citations retrouvées par le code")
    return {"status": status, "answer": answer, "citations": citations}, notes
