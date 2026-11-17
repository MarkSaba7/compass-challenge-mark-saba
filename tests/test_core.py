"""Vérifications sans réseau ni corpus : python tests/test_core.py

Couvre ce qui casse en silence : formats de montants et de dates, étiquettes avec coquilles,
JSON mal formé, reprises sur erreur d'API, contradictions, version de politique en vigueur.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archive import parse, planner, solver, textnorm as tn, validate  # noqa: E402
from archive.llm import LLM  # noqa: E402
from archive.store import KB  # noqa: E402

INVOICE = """BRUMEVLA INGÉNIERIE INC.
FACTURE N° F-2025-0001
Date : 3 novembre 2025
Facturé à : Ville de Saint-Fictif
Projet : BRV-2025-01
Conditions : net 30 jours (échéance le 3 décembre 2025)
Description Montant
Rapport technique 10 000 $
Sous-totla : 10 000 $
TPS (5 %) : 500,00 $
TVQ (9,975 %) : 997,50 $
TOTAL : 11 497,50 $"""
SHEET = """Fcihe de projet — Pont de Saint-Fictif
Date du rapport : 2025-01-10
Élément Valeur
Numéro de projet BRV-2025-01
Client Ville de Saint-Fictif
Municipalité Saint-Fictif
Chargée de projet Ada Lovelace
Date de fin prévue 2026-06-01
Budget aprouvé 1 494 000 $"""
EMAIL = """De : Client <c@ville.example>
À : Ada Lovelace <ada.lovelace@brumeval.example>
Date : 2025-01-10 9 h 00
Objet : BRV-2025-01 – budget approuvé
Je vous confirme que le budget approuvé pour le projet Pont de Saint-Fictif (BRV-2025-01) est de 1 359 000,00 $."""
DIRECTORY = """Bottin du personnel — Brumeval Ingénierie inc.
Mise à jour : 1er septembre 2026
Nom Fonction Poste Courriel
Ada Lovelace Chargée de projet 101 ada.lovelace@brumeval.example"""
POLICY = """Meal Allowance Policy
Version {v} — Effective {d}
Approved by the management committee on {d}.
3. Provisions
The daily meal allowance while travelling is set at ${a} per day.
4. Coming into force"""


def kb() -> KB:
    texts = {"doc_0001": INVOICE, "doc_0002": SHEET, "doc_0003": EMAIL, "doc_0004": DIRECTORY,
             "doc_0005": POLICY.format(v=1, d="2020-02-01", a="45.00"),
             "doc_0006": POLICY.format(v=2, d="May 15, 2022", a="49.00"),
             "doc_0007": POLICY.format(v=3, d="2099-01-01", a="99.00")}
    return KB({i: parse.parse_document(i, t) for i, t in texts.items()})


def test_normalisation():
    assert tn.money("12 500,00 $") == tn.money("$12,500.00") == tn.money("CAD 12,500.00") == "12500.00"
    assert tn.money("1 494 000 $") == "1494000.00" and tn.money("0,47 $ par km") == "0.47"
    for s in ("2025-03-04", "4 mars 2025", "March 4th, 2025", "04/03/25", "1er mars 2025"):
        assert tn.date(s) in ("2025-03-04", "2025-03-01"), s
    assert tn.split_label("Budget aprouvé 1 494 000 $", parse.L_SHEET) == ("budget", "1 494 000 $")
    assert not tn.has_phrase(tn.key("Ville de Sainte-Fictif"), tn.key("Saint-Fictif"))


def test_parsing_with_typos():
    base = kb()
    assert base.one("invoice", "F-2025-0001", "subtotal") == "10000.00"   # « Sous-totla »
    assert base.one("invoice", "F-2025-0001", "total") == "11497.50"
    assert base.one("project", "BRV-2025-01", "manager") == "Ada Lovelace"  # titre « Fcihe de projet »
    assert base.one("employee", "Ada Lovelace", "email") == "ada.lovelace@brumeval.example"


def test_conflict_and_policy_version():
    base = kb()
    budget = [c for c in base.conflicts() if c["field"] == "budget"]
    assert budget and {d for _, ds in budget[0]["values"] for d in ds} == {"doc_0002", "doc_0003"}
    name = next(iter(base.policies))
    assert base.policy_in_force(name, "2023-11-18")["version"] == 2      # version applicable à une date
    assert base.policy_in_force(name)["version"] == 2                    # la v3 n'est pas encore en vigueur
    assert base.policy_in_force(name, "2019-01-01") is None


def test_aggregate_and_guardrails():
    base = kb()
    plan = planner.make_plan(base, None, "Quel est le montant total facturé avant taxes à Ville de Saint-Fictif en 2025 ?")
    out = solver.aggregate(base, plan)
    assert out["answer"] == "10000.00 $" and out["citations"] == ["doc_0001"]
    # pas d'agrégat sans filtre d'entité : « chiffre d'affaires 2025 » ne doit pas devenir une somme
    plan = planner.make_plan(base, None, "Quel est le total facturé en 2025 ?")
    assert plan["intent"] != "aggregate" or solver.aggregate(base, plan) is None
    # le code transforme en conflit une réponse qui reprend une valeur contradictoire
    ev = solver.evidence(base, planner.make_plan(base, None, "Budget du projet BRV-2025-01 ?"), "Budget ?")
    final, _ = validate.finalize({"status": "answered", "answer": "1 494 000 $", "citations": ["doc_0002"]},
                                 ev["docs"], base.docs, ev["conflicts"])
    assert final["status"] == "conflict" and set(final["citations"]) == {"doc_0002", "doc_0003"}


def test_validation_of_model_output():
    assert validate.extract_json('Sure!\n```json\n{"status": "answered", "answer": "3",}\n```')["answer"] == "3"
    assert validate.extract_json("no json here") is None
    known = {"doc_0001": {}, "doc_0002": {}}
    f, _ = validate.finalize({"status": "ANSWERED", "answer": "x", "citations": ["doc_0001.pdf", "doc_9999", "doc_0002"]},
                             ["doc_0001"], known)
    assert f["citations"] == ["doc_0001"]                 # inexistante et hors contexte retirées
    assert validate.finalize({"status": "answered", "answer": ""}, [], known)[0]["status"] == "not_found"
    assert validate.finalize("garbage", [], known)[0]["status"] == "not_found"


def test_llm_retries_and_json_repair():
    replies = [(429, "slow down"), (500, "oops"), (200, "I think the answer is 42"), (200, '{"answer": "42"}')]
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.path)
            code, content = replies[len(seen) - 1]
            body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    llm = LLM(f"http://127.0.0.1:{server.server_port}/v1", "test-key", "test-model", timeout=5)
    assert llm.ask_json("s", "u") == {"answer": "42"}
    assert seen == ["/v1/chat/completions"] * 4 and llm.retries == 2
    server.shutdown()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
