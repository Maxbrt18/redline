"""The vault audit, as the coach and the operator read it."""

from __future__ import annotations

DETAIL = " — {detail}"

MORE = "         … et {count} autre(s)"

YAML_INVALID = "{path} : YAML invalide : {error}"

FRONTMATTER_NOT_OBJECT = "{path} : frontmatter attendu comme objet YAML"

TYPE_UNDECLARED = "{path} : type {type!r} non déclaré dans [vault] note_types"

ALIASES_NOT_LIST = "{path} : aliases doit être une liste YAML"

FRONTMATTER = "frontmatter et gabarits valides"

YAML_BLOCKS = "{parsed} bloc(s) YAML"

NO_DATABASE = "DATABASE_URL absent — lance-le avec la base pour ces contrôles"

POSTGRES_UNREADABLE = "lecture PostgreSQL impossible : {name}: {exc}"

STORAGE = "stockage PostgreSQL canonique"

STORAGE_MIGRATION = "migration active {current}, tête attendue {head}"

NO_OBSERVATION = "aucune observation canonique"

STORAGE_UNPUBLISHED = "schéma {current}, {unpublished} événement(s) à projeter"

STORAGE_OK = "schéma {current}, {observations} observation(s)"

OBSERVATIONS = "observations PostgreSQL"

OBSERVATIONS_ORDER = "ordre de lecture non antéchronologique"

OBSERVATIONS_OK = "{observations} observation(s), contraintes SQL et complétude valides"

NO_COPIED_FACT = "aucun fait PostgreSQL recopié dans Git"

COPIED_FACT = "{sid} dans {join}"

GRAPH = "graphe intact"

BROKEN_LINK = "[[{n}]] ← {join}"

GRAPH_OK = "{files} notes, aucun lien cassé"

UNIQUE_NAMES = "noms de notes uniques"

AMBIGUOUS_NAME = "{n} → {join} ([[{link}]] est ambigu)"

UNIQUE_NAMES_OK = "{seen} notes, aucune collision"

BUDGET = "budget de lecture"

NO_BUDGET = "aucun [[vault.budget]] dans redline.toml"

BUDGET_NEAR = "marge < 5 % sur un étage · {detail}"

UPDATED_DATES = "dates `updated` honnêtes"

NO_GIT = "git indisponible"

UPDATED_TOO_LATE = "{path} : updated {updated} > dernier commit {committed}"

DECISIONS_ORDER = "décisions antéchronologiques"

DECISIONS_MISSING = "{decisions} est introuvable"

DECISION_OUT_OF_ORDER = "{b} arrive après {a} (l'ordre est antéchronologique)"

DECISIONS_COUNT = "{dates} décisions"

PREDICTIONS = "prédictions soldées"

PREDICTION_LATE = "{prediction_key} (échue le {due_on}, +{days} j)"

PREDICTIONS_LATE = (
    "{late} prédiction(s) échue(s) depuis plus de {prediction_grace_days} j ; ferme "
    "chacune avec `redline fact-write prediction --key <clé> --outcome true|false|void`")

PREDICTIONS_DUE = "{due} à fermer au prochain bilan : "

THREADS = "fils ouverts"

FOLLOW_UPS = "relances tenues"

FOLLOW_UPS_LATE = "{late} en retard — {follow_up_on} · {body}"

CORRECTIONS = "corrections rejouables"

CORRECTIONS_MISSING = "{corrections} est introuvable"

NO_REPLAY_REGISTER = "pas de registre {corrections_replay}"

REGISTER_UNREADABLE = "registre illisible : {exc}"

CORRECTION_WITHOUT_MODE = "{key} — sans mode de rejeu depuis plus de {grace_days} j"

CORRECTION_NOT_IN_JOURNAL = "{key} — au registre mais absente de {corrections}"

REGISTER = "registre : {corrections_replay}"

CORRECTIONS_PENDING = "{pending} à classer ({first})"

CORRECTIONS_OWED = "{owed} sans scénario (`pending`)"

DEADLINES = "échéances tenues"

DEADLINE_LATE = "{due_on} — {item} (échéance passée, encore à faire)"

CAPABILITY_NOTES = "notes de capacité conformes"

MODEL_UNAVAILABLE = "couche modèle indisponible ({model_unavailable})"

CAPABILITIES_COUNT = "{capabilities} capacité(s)"

FOLDER_EMPTY = "{capabilities_dir}/ est vide"

COCKPIT = "cockpit à jour"

NO_CAPABILITY = "aucune capacité dans PostgreSQL"

CAPABILITY_STALE = "{slug} : évalué il y a {days} j (> {capability_stale_days})"

COCKPIT_OK = "{capabilities} capacité(s), toutes < {capability_stale_days} j"

WEEKLY_PLAN = "programme hebdomadaire transactionnel"

PLAN_NOT_FOUND = "{week} : plan introuvable après découverte SQL"

PLAN_RECOMPUTE = "{week} : recalcul obligatoire : données modifiées depuis la révision"

PLAN_MISSING = "{current} : programme hebdomadaire absent dans PostgreSQL"

PLAN_REASON = "{current} : {reason}"

WEEKLY_PLAN_OK = "{count} semaine(s) typée(s), {current} présente et valide"

VIEWS = "vues générées à jour"

STRENGTH_DRIFT = "{name} a dérivé — la prochaine projection du service le régénère"

HOT_CACHE_DRIFT = (
    "blocs Forme/État du hot-cache dérivés — la prochaine projection du service les "
    "régénère")

VIEWS_OK = "tables de renfo + journal renfo + blocs Forme et État + {seen} bloc(s) de programme"

PLAN_DRIFT = (
    "{name} : le programme a dérivé de PostgreSQL — régénère avec `redline "
    "storage-project`")

TITLE = (
    "\n"
    "  Contrôle du vault — {root}\n")

INSTANCE = "redline.toml lisible"

FAILED = (
    "\n"
    "  \x1b[31m{failed} contrôle(s) en échec.\x1b[0m Corrige maintenant : c'est deux "
    "minutes cette semaine, une refonte dans un mois.\n")

STATIC_OK = (
    "\n"
    "  \x1b[32mAudit statique sain\x1b[0m ; audit opérationnel PostgreSQL non exécuté.\n")

STATIC_OK_WARNED = (
    "\n"
    "  \x1b[32mAudit statique sain\x1b[0m, {warned} vigilance(s), {skipped} contrôle(s) "
    "non exécuté(s). L'audit opérationnel n'est complet que sans ligne --.\n")

HEALTHY = (
    "\n"
    "  \x1b[32mVault sain.\x1b[0m\n")

NO_MIGRATION = "absente"
