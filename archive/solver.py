"""Ce que le code fait lui-même : les agrégations (somme, compte, liste) et le dossier de preuves."""
from . import textnorm as tn
from .store import KB

ETYPE = {"invoices": "invoice", "projects": "project", "contracts": "contract"}
DEFAULT_MEASURE = {"invoice": "subtotal", "project": "budget", "contract": "price"}
ALLOWED_MEASURE = {"invoice": ("subtotal", "total"), "project": ("budget",), "contract": ("price", "annual_fee")}
DATE_FIELD = {"invoice": "date", "project": "start_date", "contract": "start_date"}


def aggregate(kb: KB, plan: dict) -> dict | None:
    """Somme, compte ou liste sur des factures, projets ou contrats. None si le plan n'est pas exécutable."""
    a = plan["aggregate"]
    etype = ETYPE[a["over"]]
    items = kb.ids(etype)
    extra_docs: list[str] = []
    filters: list[str] = []
    by_manager = False

    if a.get("manager") or (plan["people"] and not a.get("client") and not a.get("project")):
        person = kb.resolve_person(a.get("manager") or "") or (plan["people"][0] if plan["people"] else None)
        if not person:
            return None
        brvs = set(kb.manages.get(person, []))
        items = [i for i in items if (i if etype == "project" else kb.one(etype, i, "project")) in brvs]
        filters.append(f"chargé(e) de projet = {person}")
        by_manager = etype != "project"
    if a.get("project") or (plan["projects"] and etype != "project"):
        found = kb.find_projects(a.get("project") or "") or plan["projects"]
        if not found:
            return None
        items = [i for i in items if (i if etype == "project" else kb.one(etype, i, "project")) == found[0]]
        filters.append(f"projet = {found[0]}")
    org_name = a.get("client") or a.get("contractor")
    if org_name or plan["orgs"]:
        org = kb.resolve_org(org_name or "") or (plan["orgs"][0] if plan["orgs"] else None)
        if not org:
            return None
        field = "contractor" if etype == "contract" else "client"
        alt = "subcontractor" if etype == "project" else field
        items = [i for i in items if tn.key(org) in (tn.key(kb.one(etype, i, field) or ""),
                                                     tn.key(kb.one(etype, i, alt) or ""))]
        filters.append(f"{field} = {org}")
    if not filters:
        return None  # pas d'agrégat « sur toute la firme » : ce serait inventer un chiffre d'affaires
    if a.get("year"):
        items = [i for i in items if (kb.one(etype, i, DATE_FIELD[etype]) or "").startswith(str(a["year"]))]
        filters.append(f"année = {a['year']}")
    if not items:
        return {"status": "not_found", "answer": "", "citations": [],
                "trace": {"filters": filters, "rows": [], "why": "aucun élément ne correspond aux filtres"}}

    if by_manager:  # la fiche de chaque projet retenu prouve qui le dirige : elle fait partie de la preuve
        used = sorted({kb.one(etype, i, "project") for i in items})
        extra_docs += [d for b in used for _, ds in kb.values("project", b, "manager") for d in ds]

    measure = a.get("measure") if a.get("measure") in ALLOWED_MEASURE[etype] else DEFAULT_MEASURE[etype]
    rows, docs, total = [], [], 0.0
    for i in sorted(items):
        if a["op"] == "sum":
            vals = kb.values(etype, i, measure)
            if not vals and etype == "contract":
                vals = kb.values(etype, i, "annual_fee")
            if len(vals) > 1:
                cites = [d for _, ds in vals for d in ds]
                return {"status": "conflict", "answer": " / ".join(v for v, _ in vals), "citations": cites,
                        "trace": {"filters": filters, "why": f"{i} : valeurs contradictoires pour {measure}"}}
            if not vals:
                continue
            value, ds = vals[0]
            total += float(value)
            rows.append([i, ds[0], value])
            docs.append(ds[0])
        else:
            doc = kb.docs_of(etype, i)[0]
            rows.append([i, doc, ""])
            docs.append(doc)
    if a["op"] == "sum":
        answer = f"{total:.2f} $"
    elif a["op"] == "count":
        answer = str(len(rows))
    else:
        answer = [r[0] for r in rows]
    return {"status": "answered", "answer": answer, "citations": list(dict.fromkeys(docs + extra_docs)),
            "trace": {"filters": filters, "op": a["op"], "measure": measure, "rows": rows}}


def evidence(kb: KB, plan: dict, question: str, max_texts: int = 4, text_chars: int = 3500) -> dict:
    """Dossier de preuves pour une question : fiches d'entités liées + extraits des documents les plus utiles."""
    cards: list[str] = []
    docs: list[str] = []
    conflicts: list[dict] = []
    hops: list[str] = []

    def allow(*doc_ids):
        docs.extend(d for d in doc_ids if d and d not in docs)

    def add(etype, eid, why=""):
        card = kb.card(etype, eid)
        if card and card not in cards:
            cards.append(card)
            allow(*kb.docs_of(etype, eid))
            conflicts.extend(kb.conflicts(etype, eid))
            hops.append(f"{etype} {eid}" + (f" ({why})" if why else ""))

    def add_person(name, why):
        person = kb.resolve_person(name or "")
        if person:
            add("employee", person, why)

    def add_contract(cid, why=""):
        add("contract", cid, why)
        add_person(kb.one("contract", cid, "signatory"), f"signataire de {cid}")

    def add_project(brv, full, why=""):
        add("project", brv, why)
        add_person(kb.one("project", brv, "manager"), f"chargé(e) du projet {brv}")
        kids = kb.children.get(brv, {})
        for cid in kids.get("contract", []):
            add_contract(cid, f"contrat lié à {brv}")
        lines = []
        for etype in ("meeting", "note", "report") + (("invoice",) if full else ()):
            for eid in sorted(kids.get(etype, []))[:40]:
                f = {k: v[0][0] for k, v in kb.ent[(etype, eid)].items() if k not in ("project", "attendees", "location")}
                doc = kb.docs_of(etype, eid)[0]
                allow(doc)
                lines.append(f"  - {etype} {eid} [{doc}]: " + "; ".join(f"{k}={v}" for k, v in f.items()))
        if lines:
            cards.append(f"DOCUMENTS OF PROJECT {brv}:\n" + "\n".join(lines))

    projects = list(plan["projects"])
    for inv in plan["invoices"]:
        add("invoice", inv)
        projects.append(kb.one("invoice", inv, "project"))
    for con in plan["contracts"]:
        add_contract(con)
        projects.append(kb.one("contract", con, "project"))
    projects = [p for p in dict.fromkeys(projects) if p][:4]
    for brv in projects:
        add_project(brv, full=brv in plan["projects"] and len(plan["projects"]) <= 2,
                    why="" if brv in plan["projects"] else "projet lié")
    for person in plan["people"][:3]:
        add("employee", person)
        managed = kb.manages.get(person, [])
        if managed:
            cards.append(f"PROJECTS MANAGED BY {person}: " + "; ".join(
                f"{b} ({kb.one('project', b, 'name')}) [{kb.sheet(b)}]" for b in managed))
            allow(*(kb.sheet(b) for b in managed))
    for org in plan["orgs"][:2]:
        as_client = [b for b in kb.ids("project") if tn.key(kb.one("project", b, "client") or "") == tn.key(org)]
        as_contractor = [c for c in kb.ids("contract") if tn.key(kb.one("contract", c, "contractor") or "") == tn.key(org)]
        if as_client:
            cards.append(f"ORGANISATION {org}: client of projects " + "; ".join(
                f"{b} [{kb.sheet(b)}]" for b in as_client))
            allow(*(kb.sheet(b) for b in as_client))
        for cid in as_contractor[:5]:
            add_contract(cid, f"contrat avec {org}")

    policy = None
    if plan.get("policy") in kb.policies:
        name, as_of = plan["policy"], plan.get("as_of") or kb.today
        policy = kb.policy_in_force(name, as_of)
        lines = [f"POLICY «{name}» — reference date {as_of}:"]
        for v in kb.policies[name]:
            state = "APPLICABLE" if policy and v["doc"] == policy["doc"] else (
                "not yet in force" if v["effective"] > as_of else "superseded")
            lines.append(f"  - version {v['version']}, effective {v['effective']} [{v['doc']}]: {state}"
                         + (f" — provision: {v['provision']}" if state == "APPLICABLE" else ""))
        if not policy:
            lines.append("  (no version was in force at the reference date)")
        cards.insert(0, "\n".join(lines))
        hops.append(f"policy {name} @ {as_of}" + (f" -> version {policy['version']}" if policy else ""))

    if policy:
        text_ids = [policy["doc"]]
        docs.insert(0, policy["doc"])
    elif plan.get("policy"):
        text_ids = []
    else:
        seeds = plan["projects"] + plan["invoices"] + plan["contracts"]
        linked = list(dict.fromkeys(docs + [d for b in plan["projects"] for d in kb.project_docs.get(b, [])]))
        text_ids = kb.search(question, among=linked, k=max_texts - 1) if (seeds or docs) else []
        text_ids += [d for d in kb.search(question, k=max_texts) if d not in text_ids][:max_texts - len(text_ids)]
    texts = []
    for d in text_ids:
        r = kb.docs[d]
        allow(d)
        texts.append(f"--- {d} ({r['type']}, {r.get('date') or 'undated'}) ---\n{r['text'][:text_chars].strip()}")

    context = "ENTITY CARDS (facts extracted from the documents; [doc_xxxx] = source)\n" + (
        "\n".join(cards) if cards else "(no entity of the archive matches the question)")
    context += "\n\nDOCUMENT EXCERPTS\n" + ("\n\n".join(texts) if texts else "(none)")
    return {"context": context, "docs": docs, "conflicts": conflicts, "hops": hops, "policy": policy}
