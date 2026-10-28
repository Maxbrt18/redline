---
type: gabarit
aliases: [débrief séance, debrief-seance]
---

<!-- Après CHAQUE séance. < 60 s. Chaque champ a une destination : le registre
     PostgreSQL via `redline ledger-add` / `redline ledger-complete`. Rien n'est demandé qui n'ait
     pas d'usage. Le pattern de douleur passe avant le chiffre : "empire au fil de la
     sortie" = stade avancé, "apparaît puis se dissipe" = charge encaissée
     (coaching/protocols.md batterie E). Le RPE se lit en voyant la cible du jour :
     au-dessus = séance RATÉE (mandat de réprimande, athlete/athlete.md). -->

**Séance** : <EF | SL | seuil | côtes | cadence | course | rando | renfo>
**Effort ressenti (RPE)** : ___ /10   ← cible du jour : ___ /10
**Douleur** : pic ___ /10 — et elle a :
  ( ) disparu à l'échauffement   ( ) apparu puis s'est dissipé
  ( ) resté stable               ( ) empiré au fil de la sortie
**Plaisir** : ___ /10
**Le truc du jour** (une phrase — un fait, pas un jugement) : 
**Ce que j'ai minimisé** (rien / …) : 

<!-- → une observation de séance PostgreSQL (type, km/temps/allure/FC/cadence via Strava,
     RPE, douleur=pic, plaisir). La nuance ("0 sur 95 %, pic 0,5") va dans `note`, jamais
     dans une colonne chiffrée. Les CSV sont des projections, pas des chemins d'écriture. -->

---

**Format ligne courte** (optionnel, les jours où six champs sont de trop) :

```
d EF 7.5 6:12 rpe3 t0.5 p8 "un peu raide au départ, parti au ralenti"
```

→ `Type=EF · km=7.5 · Allure=6:12 · RPE=3 · Dlr=0.5 · Plaisir=8 · Note=…`

> ⚠️ **À l'initiative de l'athlète, jamais réclamé par le coach.** Le coach continue de poser
> ses questions naturellement — un coach qui exige une syntaxe cesse d'être un coach. Le
> format est un raccourci offert, pas un protocole imposé.
