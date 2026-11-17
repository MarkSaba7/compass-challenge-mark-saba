# compass-challenge-mark-saba

Solution au défi Compass « Ask the Archive » — Semaine Info/Log 2026.

Le système répond à des questions sur les archives de Brumeval Ingénierie, cite ses sources et
s'abstient quand l'information est absente ou contradictoire. Idée centrale : **le code établit les
faits et fait les calculs ; le modèle ne sert qu'à comprendre la question et à lire un petit dossier
de preuves.**

## Installation (2 étapes)

```bash
python -m venv .venv && source .venv/bin/activate   # facultatif
pip install -r requirements.txt
```

Python 3.11 ou plus récent. Placez les PDF dans `./corpus/`.

## Exécution

```bash
export LLM_BASE_URL=...   # API compatible OpenAI, ex. https://exemple/v1
export LLM_API_KEY=...
export LLM_MODEL=...
python answer.py questions.jsonl answers.jsonl
```

Sortie : une ligne par question, par exemple
`{"id": "s09", "answer": "27479.03 $", "citations": ["doc_0629"], "status": "answered"}`.

Première exécution : environ 40 s pour lire les 3 048 PDF. Les suivantes : 1 s (cache dans `.cache/index.json` ;
seuls les PDF nouveaux ou modifiés sont relus).

Autres commandes :

```bash
python audit.py                      # audit de santé des archives -> docs/audit.html (aucun modèle requis)
ARCHIVE_NO_LLM=1 python answer.py questions.jsonl answers.jsonl   # mode sans modèle, pour comparer
python tests/test_core.py            # vérifications sans réseau ni corpus
```

## Messages d'erreur

| Situation | Message affiché (code de sortie 1) |
|---|---|
| Variable du modèle manquante | `Erreur : variable(s) d'environnement manquante(s) : LLM_MODEL. Exemple : export LLM_BASE_URL=...` |
| Fichier de questions introuvable | `Erreur : fichier de questions introuvable : questions.jsonl` |
| Ligne de questions invalide | `Erreur : questions.jsonl, ligne 3 : JSON invalide ou champ id/question manquant (...)` |
| Dossier `corpus/` absent ou vide | `Erreur : dossier du corpus introuvable ou vide : /chemin/corpus (lancez la commande depuis la racine du repo...)` |
| Dépendance non installée | `Erreur : dépendance manquante : lancez « pip install -r requirements.txt »` |
| PDF illisible | avertissement, le document est ignoré et l'exécution continue |
| API en panne (timeout, 429, 5xx) | reprises avec attente croissante ; en dernier recours la question passe en mode sans modèle |

## Bibliothèques utilisées

- `pypdf==5.1.0` — extraction du texte des PDF. C'est la seule dépendance.
- Tout le reste est la bibliothèque standard de Python : `urllib` (appel du modèle), `json` (cache),
  `difflib` (tolérance aux coquilles), `concurrent.futures` (questions en parallèle).

## Architecture

```
answer.py              point d'entrée, parallélisme, budget de temps
archive/
  ingest.py            PDF -> texte, cache JSON (seuls les PDF modifiés sont relus) } traitement
  parse.py             texte -> type de document + faits, sans modèle              } des
  textnorm.py          montants, dates, étiquettes malgré les coquilles            } documents
  store.py             base de faits : liens, versions, contradictions, BM25       }
  llm.py               client chat/completions : reprises, relance si JSON invalide } interaction
  planner.py           question -> plan (recherche ou agrégation, politique, date)  } avec le
  reader.py            dossier de preuves -> réponse JSON citée                     } modèle
  solver.py            agrégations exactes et collecte des preuves (par le code)
  validate.py          schéma de sortie, citations, garde-fou de contradiction      } validation
  rules.py             réponse par règles : vérifie le modèle, le remplace en panne  } des réponses
  pipeline.py          enchaîne les étapes et garde une trace par question
audit.py               angle libre : audit de santé des archives
```

1. **Documents** : chaque PDF devient des faits `(entité, champ, valeur, document)`, par exemple
   `(facture F-2021-1393, total, 27479.03, doc_0629)`. Les étiquettes sont reconnues malgré les coquilles.
2. **Modèle** : deux appels au plus par question. Le premier classe la question et choisit la politique
   visée ; le second lit un dossier de quelques fiches et extraits, puis répond en JSON.
3. **Validation** : les sommes et les comptes sont calculés par le code ; une valeur présente dans deux
   documents avec deux valeurs devient `conflict` même si le modèle ne l'a pas vu ; toute citation
   inexistante ou hors dossier est retirée.

## Angle libre : audit de santé des archives

Angle choisi : confiance et explicabilité, poussées en amont. Avant toute question, `python audit.py`
relit la base de faits et produit `docs/audit.html` : les 6 contradictions du corpus côte à côte avec
leurs sources, la version en vigueur de chaque politique (et celles remplacées ou pas encore en
vigueur), les propositions de comité à ne pas confondre avec une politique, et cinq contrôles
d'intégrité. La même page montre la preuve de chaque réponse de la dernière exécution : chemin suivi,
tableau du calcul, contrôles appliqués. Le détail est dans `REPORT.pdf`.

## Démo (lien)

Lien vers la démo (2 minutes maximum) : À AJOUTER

Captures d'écran de la page d'audit : [contradictions](docs/scshot1.png), [politiques et contrôles](docs/scshot2.png), [réponses](docs/scshot3.png).
