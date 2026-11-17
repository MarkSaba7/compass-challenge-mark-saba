"""Plan d'une question : recherche ou agrégation, politique visée, filtres, entités mentionnées."""
import re

from . import textnorm as tn
from .llm import LLMError
from .parse import CONTRACT_RE, INVOICE_RE
from .store import KB

SYSTEM = """You turn a question about a company's document archive into a JSON plan. Do NOT answer the question.

Reply with ONE JSON object and nothing else:
{"intent": "lookup" | "aggregate",
 "policy": <exact name from POLICIES> | null,
 "as_of": "YYYY-MM-DD" | null,
 "aggregate": null | {"over": "invoices" | "projects" | "contracts", "op": "sum" | "count" | "list",
                      "measure": "subtotal" | "total" | "budget" | "price" | null,
                      "client": <string> | null, "project": <string> | null, "manager": <person> | null,
                      "contractor": <string> | null, "year": <integer> | null}}

Rules:
- intent "aggregate" ONLY when the answer combines several documents of the same kind: a sum, a count or a
  list (total billed to a client in a year, total billed for a project, number of invoices, list of projects
  managed by a person...). A question about ONE identified invoice, contract, project or person is "lookup",
  even if it asks for its "total amount".
- Only invoices (amounts billed), projects (budgets) and contracts (prices) can be aggregated. If the question
  asks for something else (hours worked, salary, company revenue, profit...), use "lookup".
- measure: "subtotal" = before taxes, "total" = taxes included, "budget" = project budgets,
  "price" = contract prices. For op "count" or "list", measure is null.
- client / contractor / manager / project: copy the name or identifier exactly as written in the question.
- policy: if the question is about an internal policy rule (allowance, reimbursement, vacation, telework,
  overtime, training, safety, referral, devices...), pick the matching name from POLICIES, even if the
  question is in another language; otherwise null.
- as_of: the date at which the policy rule is asked for, if the question gives one; null for "currently"."""


def detect(kb: KB, question: str) -> dict:
    """Entités mentionnées dans la question, repérées par le code."""
    return {
        "invoices": list(dict.fromkeys(INVOICE_RE.findall(question))),
        "contracts": list(dict.fromkeys(CONTRACT_RE.findall(question))),
        "projects": kb.find_projects(question),
        "people": kb.find_people(question),
        "orgs": kb.find_orgs(question),
    }


def _rules(kb: KB, question: str) -> dict:
    """Plan sans modèle : mots-clés seulement. Moins fin, mais ne tombe jamais en panne."""
    fq = tn.fold(question)
    plan: dict = {"intent": "lookup", "policy": None, "as_of": None, "aggregate": None}
    qwords = set(tn.key(question).split())
    best = 0
    for name in kb.policies:  # ne reconnaît une politique que nommée dans la langue du document
        core = [w for w in tn.key(name).split() if len(w) > 3 and w not in ("politique", "policy")]
        if core and all(w in qwords for w in core) and len(core) > best and (
                "politique" in qwords or "policy" in qwords):
            best = len(core)
            plan["policy"] = name
            plan["as_of"] = tn.date(question)
    many = re.search(r"\b(total|somme|sum|combien de (factures|projets|contrats)|how many|quels? (sont|projets)|"
                     r"which projects|list|liste|ensemble)\b", fq)
    if many and not INVOICE_RE.search(question) and not plan["policy"]:
        over = "projects" if re.search(r"projets? (sont|dirig|ger)|which projects|budget", fq) else (
            "contracts" if re.search(r"contrats|contracts", fq) else "invoices")
        op = "count" if re.search(r"combien de|how many|nombre", fq) else (
            "list" if re.search(r"\b(quels|which|list|liste)\b", fq) else "sum")
        taxed = re.search(r"taxes? inclu|includ\w* tax|with tax|ttc", fq)
        measure = None if op != "sum" else {"projects": "budget", "contracts": "price"}.get(
            over, "total" if taxed else "subtotal")
        year = re.search(r"(?<![-\d])(20\d\d)(?![-\d])", question)
        manager = re.search(r"charg|manag|dirig|gestionnaire|responsable", fq)
        people, orgs = kb.find_people(question), kb.find_orgs(question)
        projects = kb.find_projects(question)
        plan.update(intent="aggregate", aggregate={
            "over": over, "op": op, "measure": measure, "client": orgs[0] if orgs else None,
            "project": projects[0] if projects else None,
            "manager": people[0] if people and manager else None, "contractor": None,
            "year": int(year.group(1)) if year else None})
    return plan


def make_plan(kb: KB, llm, question: str) -> dict:
    plan, source = None, "rules"
    if llm is not None:
        user = (f"POLICIES: {list(kb.policies)}\nTODAY: {kb.today}\n\nQUESTION: {question}")
        try:
            raw = llm.ask_json(SYSTEM, user, max_tokens=300)
            plan, source = _clean(kb, raw), "llm"
            if not plan["policy"] and re.search(r"politique|policy|policies", tn.fold(question)):
                plan["policy"] = _pick_policy(kb, llm, question)
        except LLMError as e:
            source = f"rules (modèle indisponible : {e})"
    if plan is None:
        plan = _rules(kb, question)
    plan["source"] = source
    plan.update(detect(kb, question))
    return plan


def _pick_policy(kb: KB, llm, question: str) -> str | None:
    """Question courte et fermée : laquelle de ces politiques ? Plus fiable qu'un champ parmi d'autres."""
    names = list(kb.policies)
    listing = " | ".join(f"{i}. {n}" for i, n in enumerate(names, 1))
    raw = llm.ask_json(
        "A question mentions a company policy, possibly in another language (French/English) than the policy's "
        'official name. Reply with ONE JSON object: {"policy_number": <number of the matching policy, or 0 if none>}.',
        f"POLICIES: {listing} || QUESTION: {question}", max_tokens=40)
    n = raw.get("policy_number")
    return names[int(n) - 1] if str(n).isdigit() and 1 <= int(n) <= len(names) else None


def _clean(kb: KB, raw: dict) -> dict:
    """Ne garde du plan du modèle que ce qui respecte le schéma."""
    plan = {"intent": "aggregate" if raw.get("intent") == "aggregate" else "lookup", "policy": None,
            "as_of": None, "aggregate": None}
    if isinstance(raw.get("policy"), str):
        plan["policy"] = tn.best_match(raw["policy"], {tn.key(n): n for n in kb.policies}, 0.8)
    if isinstance(raw.get("as_of"), str):
        plan["as_of"] = tn.date(raw["as_of"])
    agg = raw.get("aggregate")
    if plan["intent"] == "aggregate" and isinstance(agg, dict):
        year = agg.get("year")
        over = {"subtotal": "invoices", "total": "invoices", "budget": "projects", "price": "contracts"}.get(
            agg.get("measure"), agg.get("over"))
        plan["aggregate"] = {
            "over": over if over in ("invoices", "projects", "contracts") else "invoices",
            "op": agg.get("op") if agg.get("op") in ("sum", "count", "list") else "sum",
            "measure": agg.get("measure") if agg.get("measure") in ("subtotal", "total", "budget", "price") else None,
            **{k: (str(agg[k]) if agg.get(k) else None) for k in ("client", "project", "manager", "contractor")},
            "year": int(year) if str(year or "").isdigit() else None}
    else:
        plan["intent"] = "lookup"
    return plan
