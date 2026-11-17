#!/usr/bin/env python3
"""Audit des archives (angle libre) : python audit.py

Relit la base de faits, sans modèle, et écrit docs/audit.html : contradictions entre documents,
version en vigueur de chaque politique, contrôles, et réponses de la dernière exécution de answer.py.
"""
import json
import re
import sys
from html import escape
from pathlib import Path

from archive import ingest
from archive.store import KB

TRACE = Path(".cache") / "last_run_trace.jsonl"
OUT = Path("docs") / "audit.html"
CSS = """
body { font-family: Arial, sans-serif; font-size: 14px; margin: 25px; }
table { border-collapse: collapse; margin-bottom: 15px; }
td, th { border: 1px solid #999; padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #eee; }
.probleme { background: #ffe0e0; }
.ok { background: #e0f5e0; }
"""


def table(headers, rows):
    """Tableau HTML. Chaque ligne est une liste de cellules déjà échappées."""
    out = "<table><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    for row in rows:
        out += "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
    return out + "</table>"


def source(kb, doc_id):
    doc = kb.docs[doc_id]
    return f"{doc_id} ({escape(doc['title'][:50])}, {doc.get('date') or 'sans date'})"


def integrity_checks(kb):
    """Liste de (contrôle, éléments en défaut, nombre vérifié)."""
    invoices, contracts = kb.ids("invoice"), kb.ids("contract")
    projects = [p for p in kb.ids("project") if kb.one("project", p, "name")]  # projets qui ont une fiche

    def wrong_total(i):
        v = [float(kb.one("invoice", i, k) or 0) for k in ("subtotal", "gst", "qst", "total")]
        return abs(v[0] + v[1] + v[2] - v[3]) > 0.02

    return [
        ("Factures où sous-total + TPS + TVQ ≠ total", [i for i in invoices if wrong_total(i)], len(invoices)),
        ("Factures liées à un projet sans fiche",
         [i for i in invoices if kb.one("invoice", i, "project") not in projects], len(invoices)),
        ("Contrats liés à un projet sans fiche",
         [c for c in contracts if kb.one("contract", c, "project") not in projects + [None]], len(contracts)),
        ("Fiches de projet sans budget approuvé",
         [p for p in projects if not kb.one("project", p, "budget")], len(projects)),
        ("Chargés de projet absents du bottin",
         [p for p in projects if not kb.resolve_person(kb.one("project", p, "manager") or "")], len(projects)),
    ]


def render(kb, traces):
    facts = sum(len(d["facts"]) for d in kb.docs.values())
    html = f"<html><head><meta charset='utf-8'><title>Audit des archives</title><style>{CSS}</style></head><body>"
    html += "<h1>Audit des archives de Brumeval</h1>"
    html += (f"<p>{len(kb.docs)} documents, {facts} faits extraits. Date de référence : {kb.today} "
             "(date du dernier document). Page générée par audit.py, sans appel au modèle.</p>")

    # 1. contradictions
    conflicts = kb.conflicts()
    html += f"<h2>1. Contradictions entre documents ({len(conflicts)})</h2>"
    html += "<p>Deux documents donnent une valeur différente pour le même champ. Le système répond alors « conflict ».</p>"
    rows = []
    for c in conflicts:
        (v1, docs1), (v2, docs2) = c["values"][:2]
        rows.append([c["id"], escape(kb.one(c["type"], c["id"], "name") or ""), c["field"],
                     f"{escape(v1)}<br>{source(kb, docs1[0])}", f"{escape(v2)}<br>{source(kb, docs2[0])}"])
    html += table(["Projet", "Nom", "Champ", "Valeur 1", "Valeur 2"], rows)

    # 2. politiques
    html += "<h2>2. Versions des politiques</h2>"
    rows = []
    for name, versions in kb.policies.items():
        current = kb.policy_in_force(name)
        for v in versions:
            if current and v["doc"] == current["doc"]:
                state = "<b>en vigueur</b>"
            else:
                state = "à venir" if v["effective"] > kb.today else "remplacée"
            rows.append([escape(name), v["version"], v["effective"], state, escape(v["provision"][:100]), v["doc"]])
    html += table(["Politique", "Version", "Date d'effet", "État", "Règle", "Document"], rows)
    proposals = [(doc, value) for (etype, doc), fields in kb.ent.items() if etype == "committee"
                 for value, _ in fields.get("proposal", [])]
    if proposals:
        html += "<p>Propositions faites en comité de direction (pas adoptées, donc pas des politiques) :</p><ul>"
        html += "".join(f"<li>{escape(value)} ({doc})</li>" for doc, value in proposals) + "</ul>"

    # 3. contrôles
    html += "<h2>3. Contrôles</h2><table><tr><th>Contrôle</th><th>Problèmes</th><th>Sur</th><th>Lesquels</th></tr>"
    for label, bad, total in integrity_checks(kb):
        css = "probleme" if bad else "ok"
        html += f"<tr><td>{label}</td><td class='{css}'>{len(bad)}</td><td>{total}</td><td>{escape(', '.join(bad))}</td></tr>"
    html += "</table>"

    # 4. réponses de la dernière exécution
    if traces:
        traces.sort(key=lambda t: t["id"])
        html += f"<h2>4. Réponses de la dernière exécution ({len(traces)} questions)</h2>"
        rows = []
        for t in traces:
            answer = t.get("answer")
            answer = ", ".join(answer) if isinstance(answer, list) else answer
            rows.append([t["id"], escape(t["question"]), t.get("status"), escape(str(answer)),
                         ", ".join(t.get("citations", [])), escape(str(t.get("route", "")))])
        html += table(["Id", "Question", "Statut", "Réponse", "Citations", "Méthode"], rows)

        html += "<h3>Détail des calculs</h3>"
        for t in traces:
            computation = t.get("computation") or {}
            if computation.get("rows"):
                html += f"<p><b>{t['id']}</b> : {escape(' ; '.join(computation['filters']))}</p>"
                html += table(["Élément", "Document", "Montant"], computation["rows"])

        html += "<h3>Chemin suivi pour les autres questions</h3><ul>"
        for t in traces:
            if t.get("hops"):
                html += f"<li><b>{t['id']}</b> : {escape(' → '.join(t['hops']))}</li>"
        html += "</ul>"
    html += "</body></html>"
    # un retour de ligne après chaque bloc, pour que le fichier soit lisible dans un éditeur
    return re.sub(r"(</tr>|</table>|<table>|</p>|</h[123]>|</li>|<ul>|</ul>|</style>|</head>|<body>|</body>)", r"\1\n", html)


def main():
    if not Path("corpus").is_dir():
        print("Erreur : dossier ./corpus/ introuvable (lancez depuis la racine du repo).", file=sys.stderr)
        return 1
    kb = KB(ingest.load_corpus(Path("corpus")))
    traces = []
    if TRACE.is_file():
        traces = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines() if line.strip()]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(render(kb, traces), encoding="utf-8")
    print(f"Audit écrit dans {OUT} ({len(kb.conflicts())} contradictions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
