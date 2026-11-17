"""Base de faits en mémoire : entités, liens entre elles, contradictions, recherche BM25."""
import math
from collections import Counter, defaultdict

from . import textnorm as tn

# Champs pour lesquels deux valeurs différentes sont une contradiction (et non une évolution normale).
CONFLICT_FIELDS = {
    "project": ["budget", "end_date", "start_date", "client", "manager", "location", "subcontractor"],
    "invoice": ["total", "subtotal", "date", "client", "project"],
    "contract": ["price", "annual_fee", "payment_days", "termination_notice_days", "signatory", "start_date",
                 "contractor", "project"],
    "employee": ["title", "email", "extension", "start_date"],
}
PERSON_FIELDS = {("project", "manager"), ("project", "prepared_by"), ("contract", "signatory")}
STOPWORDS = set("le la les de des du un une et est au aux en pour par sur dans que qui quel quelle quels quelles "
                "the of is are was what which who to for in on at and by a an d l s qu ce cette selon ont ete a "
                "il elle son sa ses avec does did do has have".split())


class KB:
    def __init__(self, records: dict[str, dict]):
        self.docs = records
        # self.ent[(type, id)][champ] = [(valeur, document source), ...]
        self.ent = defaultdict(lambda: defaultdict(list))
        for doc_id, r in records.items():
            for etype, eid, field, value in r["facts"]:
                self.ent[(etype, eid)][field].append((value, doc_id))
        self._canonical_people()
        self._canonical_clients()
        self._index_links()
        self._index_policies()
        self._index_text()
        dated = [r["date"] for r in records.values() if r.get("date") and r["type"] != "policy"]
        self.today = max(dated) if dated else "9999-12-31"  # « aujourd'hui » = dernier document daté


    def ids(self, etype: str) -> list[str]:
        return [eid for (t, eid) in self.ent if t == etype]

    def values(self, etype: str, eid: str, field: str) -> list[tuple[str, list[str]]]:
        """Valeurs distinctes d'un champ, chacune avec ses documents sources."""
        groups: dict[str, tuple[str, list[str]]] = {}
        for value, doc in self.ent.get((etype, eid), {}).get(field, []):
            k = f"{float(value):.2f}" if _is_number(value) else tn.key(value)
            groups.setdefault(k, (value, []))[1].append(doc)
        return list(groups.values())

    def one(self, etype: str, eid: str, field: str) -> str | None:
        v = self.values(etype, eid, field)
        return v[0][0] if v else None

    def docs_of(self, etype: str, eid: str) -> list[str]:
        seen: dict[str, None] = {}
        for pairs in self.ent.get((etype, eid), {}).values():
            for _, doc in pairs:
                seen[doc] = None
        return list(seen)

    def sheet(self, brv: str) -> str | None:
        """Document de la fiche de projet (celui qui porte le nom officiel)."""
        pairs = self.ent.get(("project", brv), {}).get("name")
        return pairs[0][1] if pairs else (self.docs_of("project", brv) or [None])[0]

    def conflicts(self, etype: str | None = None, eid: str | None = None) -> list[dict]:
        out = []
        for (t, i) in list(self.ent):
            if (etype and t != etype) or (eid and i != eid) or t not in CONFLICT_FIELDS:
                continue
            for field in CONFLICT_FIELDS[t]:
                vals = self.values(t, i, field)
                if len(vals) > 1:
                    out.append({"type": t, "id": i, "field": field, "values": vals})
        return out


    def _canonical_people(self):
        self.people = {tn.key(eid): eid for (t, eid), f in self.ent.items() if t == "employee" and "email" in f}
        for (t, eid) in [k for k in self.ent if k[0] == "employee" and "email" not in self.ent[k]]:
            target = tn.best_match(eid, self.people)  # « Bienvenue à … » avec une coquille dans le nom
            if target:
                for field, pairs in self.ent.pop((t, eid)).items():
                    self.ent[("employee", target)][field].extend(pairs)
        for (t, eid), fields in self.ent.items():
            for field, pairs in fields.items():
                if (t, field) in PERSON_FIELDS:
                    fields[field] = [(tn.best_match(v, self.people) or v, d) for v, d in pairs]

    def _canonical_clients(self):
        """Le client d'une facture est celui de son projet : la graphie majoritaire l'emporte sur la coquille.

        On ne fusionne jamais deux noms seulement parce qu'ils se ressemblent : le corpus contient
        de vrais quasi-homonymes (Saint-Fictif / Sainte-Fictif).
        """
        by_project = defaultdict(list)
        for (t, eid), f in self.ent.items():
            if t == "invoice" and f.get("project") and f.get("client"):
                by_project[f["project"][0][0]].append((t, eid))
            elif t == "project" and f.get("client"):
                by_project[eid].append((t, eid))
        for members in by_project.values():
            spellings = Counter(v for m in members for v, _ in self.ent[m]["client"])
            canon = spellings.most_common(1)[0][0]
            for m in members:
                self.ent[m]["client"] = [(canon if tn.similar(tn.key(v), tn.key(canon)) >= 0.8 else v, d)
                                         for v, d in self.ent[m]["client"]]
        orgs = Counter()
        for (t, eid), f in self.ent.items():
            for field in ("client", "contractor", "subcontractor"):
                orgs.update(v for v, _ in f.get(field, []))
        self.orgs = {tn.key(o): o for o, _ in orgs.most_common()[::-1]}


    def _index_links(self):
        self.project_docs = defaultdict(list)        # BRV -> documents qui le mentionnent
        self.children = defaultdict(lambda: defaultdict(list))  # BRV -> type -> [id d'entité]
        for doc_id, r in self.docs.items():
            for i in r["ids"]:
                if i.startswith("BRV-"):
                    self.project_docs[i].append(doc_id)
        for (t, eid), f in self.ent.items():
            if t != "project" and f.get("project"):
                self.children[f["project"][0][0]][t].append(eid)
        self.aliases = {}                            # clé du nom -> BRV
        self.by_location = defaultdict(list)
        for brv in self.ids("project"):
            for field in ("name", "alias"):
                for v, _ in self.ent[("project", brv)].get(field, []):
                    self.aliases.setdefault(tn.key(v), brv)
            loc = self.one("project", brv, "location")
            if loc:
                self.by_location[tn.key(loc)].append(brv)
        self.manages = defaultdict(list)
        for brv in self.ids("project"):
            for v, _ in self.values("project", brv, "manager"):
                self.manages[v].append(brv)

    def _index_policies(self):
        self.policies: dict[str, list[dict]] = {}     # nom canonique -> versions triées
        names: dict[str, str] = {}
        for (t, doc_id), f in self.ent.items():
            if t != "policy":
                continue
            raw = f["name"][0][0]
            canon = tn.best_match(raw, names, 0.9)
            if not canon:
                canon = names[tn.key(raw)] = raw
            self.policies.setdefault(canon, []).append({
                "doc": doc_id, "version": int(f["version"][0][0]),
                "effective": (f.get("effective_date") or [("", "")])[0][0],
                "provision": (f.get("provision") or [("", "")])[0][0]})
        for versions in self.policies.values():
            versions.sort(key=lambda v: (v["effective"], v["version"]))

    def policy_in_force(self, name: str, as_of: str | None = None) -> dict | None:
        """Version applicable à une date : la plus récente déjà entrée en vigueur."""
        as_of = as_of or self.today
        live = [v for v in self.policies.get(name, []) if v["effective"] and v["effective"] <= as_of]
        return live[-1] if live else None


    def find_people(self, text: str) -> list[str]:
        k = tn.key(text)
        return [name for nk, name in self.people.items() if tn.has_phrase(k, nk)]

    def find_orgs(self, text: str) -> list[str]:
        k = tn.key(text)
        hits = [o for ok, o in self.orgs.items() if tn.has_phrase(k, ok)]
        return [h for h in hits if not any(h != o and tn.key(h) in tn.key(o) for o in hits)]  # le plus long gagne

    def find_projects(self, text: str) -> list[str]:
        from .parse import PROJECT_RE
        found = list(dict.fromkeys(PROJECT_RE.findall(text)))
        k = tn.key(text)
        named = sorted((a for a in self.aliases if tn.has_phrase(k, a)), key=len, reverse=True)
        if named and self.aliases[named[0]] not in found:
            found.append(self.aliases[named[0]])
        if not found:  # nom traduit ou approximatif : on se rabat sur le lieu du projet
            for loc, brvs in self.by_location.items():
                if tn.has_phrase(k, loc):
                    found.extend(b for b in brvs if b not in found)
        return found

    def resolve_org(self, name: str) -> str | None:
        return tn.best_match(name, self.orgs, 0.9) if name else None

    def resolve_person(self, name: str) -> str | None:
        return tn.best_match(name, self.people, 0.86) if name else None


    def _index_text(self):
        self.tf: dict[str, Counter] = {}
        df = Counter()
        for doc_id, r in self.docs.items():
            c = Counter(tn.tokens(r["text"]))
            self.tf[doc_id] = c
            df.update(c.keys())
        n = len(self.docs) or 1
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.avg_len = sum(sum(c.values()) for c in self.tf.values()) / n

    def search(self, query: str, among: list[str] | None = None, k: int = 5) -> list[str]:
        terms = [t for t in tn.tokens(query) if t not in STOPWORDS and t in self.idf]
        scores = []
        for doc_id in (among if among is not None else self.tf):
            c = self.tf.get(doc_id)
            if not c:
                continue
            length = sum(c.values())
            s = sum(self.idf[t] * c[t] * 2.2 / (c[t] + 1.2 * (0.25 + 0.75 * length / self.avg_len))
                    for t in terms if t in c)
            if s > 0:
                scores.append((s, doc_id))
        return [d for _, d in sorted(scores, reverse=True)[:k]]


    def card(self, etype: str, eid: str, skip: tuple[str, ...] = ("alias",)) -> str:
        """Fiche d'une entité : chaque valeur porte son document source ; les contradictions sont signalées."""
        fields = self.ent.get((etype, eid))
        if not fields:
            return ""
        docs = self.docs_of(etype, eid)
        main = Counter(d for pairs in fields.values() for _, d in pairs).most_common(1)[0][0]
        parts = []
        for field in fields:
            if field in skip:
                continue
            vals = self.values(etype, eid, field)
            if len(vals) > 1 and field in CONFLICT_FIELDS.get(etype, []):
                shown = " VS ".join(f"{v} [{', '.join(dict.fromkeys(ds))}]" for v, ds in vals)
                parts.append(f"{field}: !! CONTRADICTORY SOURCES !! {shown}")
            else:
                v, ds = vals[0]
                parts.append(f"{field}={v}" + ("" if ds[0] == main else f" [{ds[0]}]"))
        return f"{etype.upper()} {eid} [{main}]: " + "; ".join(parts) + (
            "" if len(docs) == 1 else f"  (sources: {', '.join(docs[:6])})")


def _is_number(v: str) -> bool:
    try:
        float(v)
        return "-" not in v[1:]
    except ValueError:
        return False
