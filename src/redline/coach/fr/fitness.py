"""What the coach reads of the athlete's fitness: the goal's estimated time and the
calibration in force (`redline cycle`)."""


def number(value: float) -> str:
    """`46,2`: one decimal at most, with a decimal comma."""
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


GOAL = "Objectif de chrono : {time} ({source})."
GOAL_SOURCES = {"athlete": "choix de l'athlète", "coach": "posé avec le coach",
                "equivalence": "équivalence d'un effort à fond déclaré"}
ESTIMATE = ("Estimation du chrono (efforts à fond déclarés) : {time}, fourchette {low} – {high} "
            "à {band} % (VDOT {vdot} ± {sd}). Une estimation, pas un objectif : si l'athlète la "
            "retient, `goal_source = equivalence`.")
ESTIMATE_CAPPED = ("  arrêt déclaré du {start:%d/%m/%Y} au {end:%d/%m/%Y}, après le dernier effort : "
                   "{time} est un « au mieux », la fourchette ne garde que sa moitié lente.")
ESTIMATE_SINGLE = "  elle ne repose que sur un effort, vieux de {months} mois."
ESTIMATE_SOURCE = ("  source : {kind} de {km} km en {clock} le {on:%d/%m/%Y} (il y a {months} mois), "
                   "VDOT {vdot}, équivalent {equivalent}, part {share} %")
EFFORT_KINDS = {"race": "course", "test": "test"}
NO_ESTIMATE = ("Estimation du chrono : aucun effort à fond déclaré à plat d'au moins {km} km dans "
               "les {weeks} dernières semaines (`redline fact-write effort`). Un test de 1 200 m "
               "puis 3 600 m à fond, à plat, en donnerait un.")
BOUND = ("  contrôle : les séances à plat des {weeks} dernières semaines prouvent au moins un "
         "VDOT de {vdot} ({km} km en {clock} le {on:%d/%m/%Y}).")
BOUND_ABOVE = " C'est plus que l'estimation : elle est périmée, l'athlète est plus en forme."
EFFICIENCY = ("  allure à FC égale (EF à plat) : {change} % depuis le {since:%d/%m/%Y} "
              "({before} → {now} m par battement, {runs_before} puis {runs_now} sorties). Un "
              "indicateur, pas une correction du chiffre.")
CALIBRATION = ("Calibration du {on:%d/%m/%Y}, adoptée {decided} sur {source} ({n} effort(s) à "
               "fond), {confidence}. Les allures en découlent jusqu'à la prochaine.")
CALIBRATION_SOURCES = {"test": "un test", "race": "une course",
                       "initial": "les efforts déclarés"}
CONFIDENCES = {"measured": "mesurée", "provisional": "provisoire (effort ancien)"}
DECIDERS = {"rule": "par la règle", "request": "à la demande de l'athlète"}
CALIBRATION_VDOT = "  VDOT {vdot} ± {sd}."
NO_CALIBRATION = ("Calibration : aucune, faute d'effort à fond déclaré. Le coach peut proposer le "
                  "test : 1 200 m puis 3 600 m à fond, à plat, dans la même séance, 30 min de "
                  "récupération entre les deux. Jamais imposé.")
CRITICAL_SPEED = ("  Vitesse critique : {pace}/km ({speed} km/h){error}, D′ {d_prime} m. C'est la "
                  "limite haute du seuil : au-dessus, chaque répétition puise dans D′.")
TEST_SESSIONS_ABOVE = ("  Une séance du {on:%d/%m/%Y} a prouvé un VDOT de {vdot}, au-dessus de la "
                       "bande de la calibration ({top}) : proposer un test. La calibration ne "
                       "bouge pas avant.")
TEST_AFTER_BREAK = ("  Une pause déclarée jusqu'au {on:%d/%m/%Y} suit le dernier effort de la "
                    "calibration : proposer un test avant de se fier à ses allures.")
CRITICAL_SPEED_ERROR = " ± {error} s/km"
CRITICAL_SPEED_TWO = " (deux efforts : aucune erreur d'ajustement)"
CRITICAL_SPEED_DOMAINS = ("  domaines : EF plus lente que {easy}/km ({first} % de la VC) ou sous {hr} "
                          "bpm (haut de la Z2), séances de "
                          "seuil entre {low}/km et {high}/km ({low_pct} à {high_pct} %, convention "
                          "à vérifier sur la FC et le RPE), VMA plus vite que {pace}/km.")
NO_CRITICAL_SPEED = ("  Vitesse critique : aucune dans la calibration, faute de deux efforts à fond "
                     "à plat de 2 à 20 min (au moins 5 min d'écart). Le coach peut proposer le "
                     "test : 1 200 m puis 3 600 m à fond, à plat, dans la même séance, 30 min de "
                     "récupération entre les deux. Jamais imposé.")
