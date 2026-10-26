# Ingestion des sources — mode d'emploi

> Sorti de `CLAUDE.md` le 2026-07-27. Raison : ces procédures se lisaient à **chaque** session
> alors qu'elles ne servent qu'au moment d'ingérer une source — c'est-à-dire rarement, et jamais
> en répondant à une question de coaching. Elles coûtaient ~450 mots de budget de démarrage à
> chaque échange pour un usage épisodique.
>
> **Ce qui est resté dans `CLAUDE.md`** : la politique (aucune hiérarchie de sources, une source
> par lacune, le marqueur de source, la convention de nommage). Ce qui est ici : le **comment**.
>
> Liens en chemins bruts et non en wikilinks : `scripts/` est dans les `SKIP_DIRS` de
> `vault-check.py`, donc un wikilink cassé n'y serait pas détecté.

## Pipeline RunWise / Koopcast (YouTube)

Trois temps. Les podcasts YouTube passent tous par le même chemin, sans exception.

1. Sam exporte la playlist via <https://export-youtube-playlist.vercel.app/tools/youtube-playlist-link-extractor/>
   et sauvegarde le fichier dans `sources/` (TXT, CSV ou Markdown — peu importe).
2. `python3 scripts/playlist-to-csv.py sources/<export> -o sources/playlist.csv`
   → CSV canonique (`position,video_id,url,title`).
3. `python3 scripts/fetch-transcripts.py sources/playlist.csv`
   → transcripts bruts dans `inbox/`. Idempotent : ne retélécharge pas l'existant ; les échecs
   partent dans `sources/transcripts_failed.csv`.

Dépendance : `pip install youtube-transcript-api`.

**Nettoyer `sources/transcripts_failed.csv` après chaque run réussi** : un ID présent dans
`inbox/` n'a rien à faire dans les échecs.

## Règles d'ingestion

- Le brut atterrit dans `inbox/`, horodaté, et **n'est jamais modifié**. Il sert à la provenance
  (recouper une affirmation) et à une éventuelle refonte future.
- **Ingestion en deux passes** :
  - **Passe 1 (par épisode)** : lire, démêler (les sous-titres auto n'ont ni ponctuation ni
    locuteurs fiables), extraire le signal (principes, protocoles, chiffres, désaccords), jeter le
    bruit (pub, bio, blabla). Écrire la note d'épisode (`templates/episode.md`), et empiler des
    puces sourcées dans les notes de concept concernées. **On ne fait qu'accumuler à ce stade.**
  - **Passe 2 (transversale)** : une fois assez d'épisodes ingérés, pour chaque concept,
    réconcilier ses puces, écrire la Synthèse (consensus) et remplir Désaccords, puis passer
    `statut` à `consolidé`. Cette passe travaille sur **un seul concept à la fois** — jamais
    besoin de tout le corpus en contexte.
- Chaque affirmation forte porte un marqueur de source ⟨HH:MM:SS⟩ + les `[[concepts]]` inline.
  C'est ce qui tisse le graphe et permet de recouper au brut.

## Épisodes publiés après consolidation

Protocole défini le 2026-07-09.

1. Passe 1 normale : note d'épisode + puces sourcées empilées dans « Ce qu'en disent les
   épisodes » des concepts touchés. **Ne pas retoucher la Synthèse à chaud.**
2. Si une puce **contredit ou nuance matériellement** la Synthèse d'un concept, passer son
   `statut` à `à-resynthétiser` et noter le point de friction en une ligne sous la Synthèse
   (préfixe `⚠ post-consolidation :`).
3. Re-synthèse d'un concept (un seul à la fois, comme la passe 2) quand : statut
   `à-resynthétiser`, OU ≥5 nouvelles puces accumulées depuis la dernière synthèse. Repasser
   alors `statut: consolidé` et mettre à jour `maj`.
4. Un nouvel épisode peut toujours créer un nouveau concept (statut `en-accumulation`) — la
   politique de vocabulaire de `CLAUDE.md` s'applique.

## Nommage des notes de source

Toutes dans `episodes/`, **aucune source privilégiée par l'arborescence** — la provenance est
portée par le frontmatter (`podcast:` / `auteur:` / `source_type:`) et par le nom de fichier :

| Provenance | Nom de fichier |
|---|---|
| RunWise | `ep-<invité-ou-sujet>.md` |
| Koopcast | `ep-koop-<sujet>.md` |
| Autre podcast / chaîne YouTube | `ep-<auteur-ou-chaîne>-<sujet>.md` |
| Articles / livres / papiers | `src-<auteur>-<sujet>.md` |

⚠ **Un même auteur peut avoir deux notes** (invité chez RunWise *et* sa propre chaîne) : ce sont deux
sources distinctes, on ne les fusionne pas. Cas existant : `ep-mathieu-blanchard-trail` (RunWise) et
`ep-blanchard-programmation-saison` (sa chaîne).

Mettre le titre lisible en `aliases` du frontmatter (ex. `Ep – Le seuil anaérobie`) pour des
wikilinks affichables `[[ep-…|Ep – …]]`.

Transcripts bruts (générés par script) : `<video_id>__<slug-du-titre>.txt` — l'ID fait toujours
11 caractères.
