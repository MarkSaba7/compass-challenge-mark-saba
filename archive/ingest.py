"""Lecture du corpus : PDF -> texte -> faits. Un cache évite de relire les fichiers inchangés."""
import hashlib
import json
import sys
from pathlib import Path

from pypdf import PdfReader

from . import parse

CACHE = Path(".cache") / "index.json"


def load_corpus(corpus_dir, cache_path=CACHE):
    """Retourne {doc_id: fiche}. Un PDF n'est ré-analysé que si son contenu a changé."""
    old = {}
    if cache_path.is_file():
        try:
            saved = json.loads(cache_path.read_text(encoding="utf-8"))
            if saved.get("parser") == parse.PARSER_VERSION:
                old = saved["docs"]
        except (json.JSONDecodeError, KeyError):
            pass  # cache abîmé : on repart de zéro

    docs, fresh = {}, 0
    for path in sorted(corpus_dir.glob("*.pdf")):
        sha = hashlib.sha1(path.read_bytes()).hexdigest()
        entry = old.get(path.stem)
        if not entry or entry["sha"] != sha:
            try:
                text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
            except Exception as e:  # un PDF illisible ne doit pas arrêter les autres
                print(f"Avertissement : {path.name} illisible ({e}), ignoré.", file=sys.stderr)
                continue
            entry = {"sha": sha, "record": parse.parse_document(path.stem, text)}
            fresh += 1
        docs[path.stem] = entry

    if fresh or len(docs) != len(old):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"parser": parse.PARSER_VERSION, "docs": docs}, ensure_ascii=False),
                              encoding="utf-8")
    print(f"Corpus : {len(docs)} documents, dont {fresh} analysés (le reste vient du cache).", file=sys.stderr)
    return {doc_id: entry["record"] for doc_id, entry in docs.items()}
