"""The race countdown milestones, as the coach reads them: title, action, evidence."""

from __future__ import annotations

STAGES = {
    "architecture": (
        "Architecture de préparation",
        "Auditer les limiteurs de la course et attribuer à chacun un prochain test daté.",
        "Chaque limiteur important possède un test, une date et un propriétaire."),
    "terrain": (
        "Terrain et inconnues",
        "Fermer ou planifier les inconnues de parcours, règlement, reconnaissance et matériel.",
        "Les inconnues critiques sont soit résolues, soit portées par une échéance typée."),
    "systems": (
        "Systèmes en conditions réelles",
        "Vérifier que durée, terrain, nuit, nutrition, digestion et chaussures ont chacun"
        " une répétition représentative datée dans le plan ou une échéance typée, ou déjà"
        " faite.",
        "Chaque système a une répétition datée ou une mesure déjà exploitable."),
    "simulation": (
        "Simulation générale",
        "Vérifier que la répétition générale a une date ferme dans le plan ; si elle a "
        "déjà eu lieu, comparer le scénario prédit au scénario observé.",
        "La répétition est datée, ou ses écarts pacing, digestif, matériel et mental sont"
        " écrits avec leur correction."),
    "freeze-v1": (
        "Gel du plan de course v1",
        "Figer pacing, marche, nutrition, matériel et plan mental ; ne laisser ouvertes "
        "que les preuves manquantes.",
        "Une fiche v1 cohérente existe et chaque case encore ouverte nomme sa dernière preuve."),
    "premortem": (
        "Pré-mortem contradictoire",
        "Jouer le scénario d'échec le plus plausible et décider à froid de la réponse.",
        "Le scénario, son signal précoce et la décision pré-engagée sont explicites."),
    "taper": (
        "Entrée dans l'affûtage",
        "Protéger la fraîcheur : supprimer les nouveautés et vérifier que la dernière "
        "charge utile est derrière.",
        "Aucun stimulus nouveau n'est nécessaire pour se rassurer."),
    "proof": (
        "Semaine de preuve",
        "Vérifier sommeil, météo, transport, dossard, batteries, tenue et matériel obligatoire.",
        "Chaque élément logistique est physiquement présent ou attribué à un achat daté."),
    "fuel": (
        "Carburant et horaires",
        "Exécuter seulement le protocole alimentaire et de sommeil déjà testé, avec les "
        "heures réelles du départ.",
        "Le déroulé de J-2 à l'arrivée tient sur une page sans décision nouvelle."),
    "brief": (
        "Brief d'exécution",
        "Réduire toute la stratégie à cinq décisions observables pour le départ et les "
        "moments critiques.",
        "La carte finale nomme départ, marche, nutrition, incident digestif et drapeaux d'arrêt."),
    "race-day": (
        "Jour J",
        "Restituer le plan déjà décidé ; aucune optimisation, aucun test, aucune nouvelle"
        " consigne.",
        "⟨athlète⟩ sait quoi faire dans la première heure et au premier imprévu."),
}
