"""Traitement d'une question : plan -> calcul ou dossier de preuves -> lecture -> validation."""
import time

from . import planner, reader, rules, solver, validate
from .llm import LLMError


def answer_question(kb, llm, qid, question):
    """Retourne (ligne pour answers.jsonl, trace de ce qui a été fait)."""
    start = time.time()
    trace = {"id": qid, "question": question}
    try:
        plan = planner.make_plan(kb, llm, question)
        trace["plan"] = plan

        result = solver.aggregate(kb, plan) if plan["intent"] == "aggregate" else None
        if result is not None:
            # somme, compte ou liste : calculé par le code, sans le modèle
            trace["route"] = "calcul par le code"
            trace["computation"] = result.pop("trace")
            final, notes = validate.finalize(result, [], kb.docs)
        else:
            evidence = solver.evidence(kb, plan, question)
            trace["hops"] = evidence["hops"]
            by_rules = rules.rules_answer(kb, plan, question)
            raw, note = by_rules, ""
            trace["route"] = "règles (sans modèle)"
            if llm is not None:
                try:
                    raw = reader.read(llm, question, evidence["context"])
                    trace["route"] = "lecture par le modèle"
                    trace["reasoning"] = raw.get("reasoning")
                    raw, note = validate.reconcile(raw, by_rules)
                except LLMError as e:
                    trace["model_error"] = str(e)  # modèle en panne : on garde la réponse des règles
            final, notes = validate.finalize(raw, evidence["docs"], kb.docs, evidence["conflicts"])
            if note:
                notes.insert(0, note)
    except Exception as e:  # une question en erreur ne doit pas faire échouer les autres
        final, notes = dict(validate.NOT_FOUND), [f"erreur interne : {e!r}"]

    trace.update(final, notes=notes, seconds=round(time.time() - start, 1))
    return {"id": qid, **final}, trace
