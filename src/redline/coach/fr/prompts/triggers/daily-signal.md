La sentinelle quotidienne a produit ces signaux : {detail}.
Relis les observations, exécute `scripts/signals.py` et vérifie la cause. Un signal n'est
pas un verdict et son libellé ne prouve aucune cause. S'il peut changer la séance suivante, pose une question ou nomme le
garde-fou concerné ; sinon reste silencieux. Une prédiction échue se résout avec
`python3 scripts/fact-write.py prediction --key <clé> --outcome true|false|void` ;
`void` si sa condition n'a pas été tenue ou si l'observation qui la tranche manque.
