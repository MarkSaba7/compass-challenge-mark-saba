"""Réponse par règles (mots-clés + base de faits), sans modèle.

Sert à vérifier le modèle (contradictions, citations) et à répondre quand même si l'API tombe.
"""
import re

from . import textnorm as tn
from .parse import MONEY_FIELDS
from .store import KB

# (motif dans la question sans accents, type d'entité, champ) — le premier motif reconnu l'emporte
FIELD_RULES = [
    (r"budget", "project", "budget"),
    (r"taxes? inclu|includ\w* tax|ttc", "invoice", "total"),
    (r"avant taxes|before tax|hors taxe|sous-total|subtotal", "invoice", "subtotal"),
    (r"echeance|due date", "invoice", "due_date"),
    (r"emise|issued|date de la facture|invoice date", "invoice", "date"),
    (r"demarrage|kick-?off", "meeting", "date"),
    (r"soumission|bids", "meeting", "bids_received"),
    (r"preavis|resili|terminat|notice", "contract", "termination_notice_days"),
    (r"paiement|payment", "contract", "payment_days"),
    (r"duree|duration", "contract", "duration_months"),
    (r"prix|price|montant|amount|frais annuel|annual fee|valeur", "contract", "price"),
    (r"sous-traitant|subcontractor", "project", "subcontractor"),
    (r"date de fin|end date|fin prevue", "project", "end_date"),
    (r"date de debut|start date", "project", "start_date"),
    (r"municipalite|location|ou se situe", "project", "location"),
    (r"\bclient\b", "project", "client"),
]


def rules_answer(kb: KB, plan: dict, question: str) -> dict:
    fq = tn.fold(question)
    chain: list[str] = []

    def get(etype, eid, field):
        vals = kb.values(etype, eid, field)
        if not vals:
            return {"status": "not_found", "answer": "", "citations": []}
        fmt = (lambda v: f"{v} $") if field in MONEY_FIELDS else (lambda v: v)
        cites = list(dict.fromkeys(chain + [d for _, ds in vals for d in ds]))
        if len(vals) > 1:
            return {"status": "conflict", "answer": " / ".join(fmt(v) for v, _ in vals), "citations": cites}
        return {"status": "answered", "answer": fmt(vals[0][0]), "citations": cites}

    if plan.get("policy") in kb.policies:
        v = kb.policy_in_force(plan["policy"], plan.get("as_of"))
        if not v:
            return {"status": "not_found", "answer": "", "citations": []}
        if "version" in fq:
            return {"status": "answered", "answer": str(v["version"]), "citations": [v["doc"]]}
        amounts = tn.find_money(v["provision"])
        number = re.search(r"\d+(?:[.,]\d+)?", v["provision"])
        answer = f"{amounts[0]:.2f} $" if amounts else (number.group(0) if number else v["provision"])
        return {"status": "answered", "answer": answer, "citations": [v["doc"]]}

    invoice = plan["invoices"][0] if plan["invoices"] else None
    contract = plan["contracts"][0] if plan["contracts"] else None
    project = plan["projects"][0] if plan["projects"] else None
    if invoice:
        chain += kb.docs_of("invoice", invoice)[:1]
        project = project or kb.one("invoice", invoice, "project")

    # questions sur une personne : qui signe, qui dirige, son courriel, son poste
    wants_email = re.search(r"courriel|email|e-mail", fq)
    wants_title = re.search(r"poste|fonction|position|job title|\brole\b|\btitre\b", fq)
    wants_who = re.search(r"\bqui\b|\bwho\b|personne|person", fq)
    if wants_email or wants_title or wants_who:
        person = None
        if contract and re.search(r"sign", fq):
            person = kb.one("contract", contract, "signatory")
            chain += kb.docs_of("contract", contract)[:1]
        elif project and re.search(r"charg|manag|dirig|gestionnaire|responsable", fq):
            vals = kb.values("project", project, "manager")
            if vals:
                person = vals[0][0]
                chain += vals[0][1][:1]
        elif plan["people"]:
            person = plan["people"][0]
        person = kb.resolve_person(person or "") or person
        if not person:
            return {"status": "not_found", "answer": "", "citations": []}
        if wants_email:
            return get("employee", person, "email")
        if wants_title:
            return get("employee", person, "title")
        return {"status": "answered", "answer": person, "citations": chain}

    for pattern, etype, field in FIELD_RULES:
        if not re.search(pattern, fq):
            continue
        if etype == "invoice" and invoice:
            chain.clear()
            return get("invoice", invoice, field)
        if etype == "contract":
            target = contract
            if not target and project:  # « le contrat de sous-traitance lié au projet … »
                linked = kb.children.get(project, {}).get("contract", [])
                target = linked[0] if len(linked) == 1 else None
                chain.append(kb.sheet(project))
            if target:
                if field == "price" and not kb.values("contract", target, "price"):
                    field = "annual_fee"
                return get("contract", target, field)
        if etype == "meeting" and project:
            kick = [m for m in kb.children.get(project, {}).get("meeting", [])
                    if "site" not in (kb.one("meeting", m, "kind") or "")]
            if len(kick) == 1:
                return get("meeting", kick[0], field)
        if etype == "project" and project:
            return get("project", project, field)
    return {"status": "not_found", "answer": "", "citations": []}
