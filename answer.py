#!/usr/bin/env python3
"""Point d'entrée : python answer.py questions.jsonl answers.jsonl

Lit le corpus dans ./corpus/, le modèle dans LLM_BASE_URL / LLM_API_KEY / LLM_MODEL, et écrit une
ligne JSON par question : {"id", "answer", "citations", "status"}. Toute la logique est dans archive/.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CORPUS_DIR = Path("corpus")
TRACE_PATH = Path(".cache") / "last_run_trace.jsonl"
TIME_BUDGET_S = 16 * 60  # limite officielle : 20 min ; au-delà de 16 min on termine sans modèle
WORKERS = 6


def fail(message: str) -> int:
    print(f"Erreur : {message}", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) != 3:
        print("usage : python answer.py questions.jsonl answers.jsonl", file=sys.stderr)
        return 2
    questions_path, answers_path = Path(sys.argv[1]), Path(sys.argv[2])
    started = time.time()

    if not questions_path.is_file():
        return fail(f"fichier de questions introuvable : {questions_path}")
    if not CORPUS_DIR.is_dir() or not any(CORPUS_DIR.glob("*.pdf")):
        return fail(f"dossier du corpus introuvable ou vide : {CORPUS_DIR.resolve()} "
                    "(lancez la commande depuis la racine du repo, avec les PDF dans ./corpus/)")
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return fail("dépendance manquante : lancez « pip install -r requirements.txt »")
    from archive import ingest, pipeline
    from archive.llm import LLM, LLMError
    from archive.store import KB

    questions = []
    with open(questions_path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                q = json.loads(line)
                questions.append((str(q["id"]), str(q["question"])))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                return fail(f"{questions_path}, ligne {n} : JSON invalide ou champ id/question manquant ({e})")

    llm = None
    if os.environ.get("ARCHIVE_NO_LLM") != "1":  # ARCHIVE_NO_LLM=1 : mode sans modèle, pour comparer
        try:
            llm = LLM.from_env()
            llm.deadline = started + TIME_BUDGET_S
        except LLMError as e:
            return fail(f"{e}. Exemple : export LLM_BASE_URL=https://exemple/v1 LLM_API_KEY=... LLM_MODEL=...")

    kb = KB(ingest.load_corpus(CORPUS_DIR))
    print(f"Index prêt en {time.time() - started:.0f} s ; {len(questions)} question(s).", file=sys.stderr)

    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with open(answers_path, "w", encoding="utf-8") as out, open(TRACE_PATH, "w", encoding="utf-8") as tr, \
            ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(pipeline.answer_question, kb, llm, qid, text) for qid, text in questions]
        for fut in as_completed(futures):
            line, trace = fut.result()
            out.write(json.dumps(line, ensure_ascii=False) + "\n")
            out.flush()  # chaque réponse est sur disque dès qu'elle est prête
            tr.write(json.dumps(trace, ensure_ascii=False) + "\n")
            done += 1
            print(f"[{done}/{len(questions)}] {line['id']} -> {line['status']}", file=sys.stderr)
    used = f"{llm.calls} appels au modèle, {llm.retries} reprises" if llm else "mode sans modèle"
    print(f"Terminé en {time.time() - started:.0f} s ({used}). Trace : {TRACE_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
