# Farever+ (fork en français, hors jeu)

> **Ce dépôt est un fork personnel de [Farever+](https://github.com/brudrbear/FareverMeter)**,
> le compteur de dégâts pour **Farever** créé par **Brudr**. Tout le travail de
> lecture du jeu (analyse du bytecode, script Frida, calcul des soins, rapports de
> faille) vient de son projet.
>
> Cette version vise un usage **plus sobre** :
>
> * **en français** ;
> * **externe au jeu** : aucun overlay, rien n'est jamais dessiné dans Farever, le
>   HUD du jeu reste intact ;
> * **purement analytique** : une seule fenêtre, pensée pour un 2nd écran, qui
>   affiche les données en direct et garde ce qui a été enregistré, consultable
>   même jeu fermé.
>
> Le projet d'origine ne publie pas de licence : ce fork est destiné à un usage
> personnel et n'est pas redistribué.

## Position du studio

Message de Steven, community manager de Shiro Games, sur le Discord de Farever,
le 27/05/2026 :

> I'll confirm what has been said earlier
>
> While we won't promote the use of add-ons during the EA (to keep players on
> the intended experience at first), we won't condemn personal use of add-ons
> like minimaps or DPS meter 🙏

Autrement dit : pendant l'accès anticipé, le studio ne met pas en avant les
add-ons, mais tolère leur usage personnel. C'est le cadre de ce fork : usage
personnel, sans diffusion. Cette tolérance peut évoluer, à surveiller.

Pour être exact sur ce que fait Farever+ dans le jeu : il ne modifie aucune
donnée et n'envoie rien sur le réseau, mais Frida s'injecte dans le processus
du jeu et dévie quelques fonctions en mémoire pour être prévenu des coups et
des soins. En cas de plantage à signaler aux développeurs, reproduis-le sans
Farever+ avant de l'envoyer.

## Ce que fait Farever+

Farever+ lit en mémoire les données de combat de Farever (sorts, éléments,
critiques, kills, soins) pour tous les joueurs proches, et les affiche dans une
fenêtre Windows séparée.

| Page | Contenu |
|---|---|
| **En direct** | Tableau dégâts/soins du groupe (ou de tous les joueurs), détail du joueur sélectionné (sorts, critiques, types de dégâts), durée du combat, mode parse 60 s, fil d'événements (kills de boss, records, fins de faille) |
| **Failles** | Toutes les failles terminées. Chaque rapport compare la phase de faille et la phase du boss : durée, DPS, HPS, MVP, classement complet des dégâts et des soins, dégâts par type. Copiable en image (au visuel de l'interface) ou en texte. |
| **Donjons** | Chaque donjon est enregistré automatiquement : difficulté (lue dans le lobby), résultat (victoire, échec, abandon), temps du run (l'horloge du jeu), morts, groupe, un rapport en deux phases (exploration puis boss) comme pour les failles, et le butin ramassé (dont le coffre de fin). Noms des donjons, objets et boss en français, tirés du jeu. Records par donjon et par difficulté. |
| **Collection** | Montures, planeurs, compagnons, apparences d'équipement (par emplacement) et objets du Codex (nombre obtenu et rang) : ta collection est lue en jeu et gardée hors jeu. Compteurs par catégorie, recherche, filtres (tous, manquants, obtenus) et, pour chaque élément, comment l'obtenir d'après les données du jeu (butin et chances, marchands, coffres, succès, zones de capture et taux d'apparition, butin de faction, récolte, recettes de fabrication, démontage). |
| **Chasse** | Tableau de chasse : le nombre de kills de ton personnage pour chaque monstre, lu dans le Codex du jeu (il inclut donc tout ce que tu as tué avant Farever+), avec le rang du Codex. Filtres par région, recherche, tri. |
| **Carte** | La carte de Siagarta (les tuiles de la minimap du jeu), déplaçable et zoomable, avec les points de complétion : coffres du monde, de chambre forte et de recette, orbes rouges, obélisques et points de réapparition. Filtres par catégorie et par région, compteurs, détail au clic. |
| **Personnage** | Les joueurs autour de toi et, sur demande, le profil d'un joueur : classe, niveau, équipement (améliorations, cadeaux, formules, sceaux et gemmes posés, avec leurs effets ; imprégnations et effets de set actifs à 2 / 4 / 6 pièces), barre de sorts, arbre de talents et runes. Les profils ne sont gardés que pendant la session. |
| **Réglages** | Colonnes de soins, réinitialisation au pull d'un boss, « Tous les joueurs » automatique en faille, raccourci clavier, taille de l'interface, dossiers. |
| **Aide** | Utilisation, lancement avec Steam. |

Le minuteur de faille reste visible en bas du menu, sur toutes les pages : il
compte jusqu'à la prochaine faille (à chaque heure pile), puis les 3 minutes
d'ouverture du portail.

### Avec ou sans le jeu

Farever+ s'ouvre à tout moment. Il détecte Farever tout seul, s'y connecte, et
s'y reconnecte après une fermeture du jeu. Le voyant en haut à droite indique
l'état :

* **● Hors jeu** : le jeu n'est pas lancé, les failles, les combats et les
  réglages restent consultables ;
* **● Connexion…** : le jeu vient d'être détecté ;
* **● En jeu** : les données arrivent en direct ;
* **● Échec — réessayer** : un clic relance une tentative.

### Réinitialiser en plein combat

Un raccourci clavier global (par défaut **Maj + \\**) réinitialise le combat sans
quitter le jeu des yeux. Il ne fonctionne que lorsque Farever est au premier
plan, n'affiche rien dans le jeu, et se change dans les **Réglages**.

## Installation et lancement

Ce fork se lance depuis les sources (Windows). Il faut
[Python](https://www.python.org/downloads/), puis un double-clic sur
**`Installer Farever+.cmd`** : il installe les modules (dans les bonnes
versions) et crée le raccourci **Farever+** sur le Bureau et dans le menu
Démarrer. Le raccourci lance l'application sans fenêtre de console ; son
journal est alors dans `%LOCALAPPDATA%\FareverMeter\meter.log` (bouton du
journal dans les Réglages). Le script peut être relancé sans risque, par
exemple après avoir déplacé le dossier.

À la main, avec la console (pratique pour lire le journal en direct) :

```
pip install frida==17.18.0 pillow pywebview
python meter/farever_meter.py
```

Au premier lancement avec le jeu, Farever+ extrait ses données du jeu
(images, carte, catalogues) : une à deux minutes.

La fenêtre utilise **WebView2**, déjà présent sur Windows 10 et 11 à jour.

**Frida doit être en 17.18.0.** La 17.19.0 fait planter tout processus dont
elle se détache, donc Farever à la fermeture de Farever+ (mesuré le
28/09/2026 sur Windows 11 build 26200 ; les versions 16.7.19 à 17.18.0 n'ont
pas ce problème). Farever+ refuse de s'attacher avec la 17.19.0 et l'indique
dans sa fenêtre.

Pour arrêter : ferme la fenêtre, ou clic droit sur l'icône Farever+ près de
l'horloge → **Arrêter le compteur**. Farever+ se détache alors proprement du jeu.

**Ne l'arrête pas depuis le Gestionnaire des tâches** : le processus serait tué
avant de s'être détaché du jeu, ce qui peut déstabiliser Farever.

## Où sont les fichiers

Depuis les sources, tout est écrit dans le dossier du projet :

| Quoi | Où |
|---|---|
| Rapports de faille (`.json`, `.txt`, `.png`) et parses 60 s | `parses/` |
| Runs de donjon (`.json`) | `donjons/` |
| Ta collection, lue en jeu | `.meter_collection.json` |
| Tes kills par monstre (par personnage) | `.meter_codex.json` |
| Réglages, position de la fenêtre, records de boss | `.meter_settings.json`, `.meter_position.json`, `.meter_besttimes.json` |
| Données du jeu régénérées | `analysis_out/` |
| Journal | la console |

Rien n'est jamais supprimé de `parses/` ni de `donjons/` : fais le ménage toi-même.

## Après une mise à jour de Farever

Les index de fonctions et les positions des champs changent d'une version du jeu
à l'autre. Au lancement, Farever+ compare le `hlboot.dat` du jeu en cours avec
celui qui a servi à générer `analysis_out/`, et **régénère les données tout seul**
si besoin (quelques secondes). Si Farever est installé à un endroit inhabituel,
indique le chemin complet de `hlboot.dat` dans la variable d'environnement
`FAREVER_HLBOOT`.

Pour régénérer à la main :

```
python hltools/build_targets.py     # -> resolver_data.json (fonctions)
python hltools/emit_offsets.py      # -> meter_offsets.json (champs)
```

## Comment ça marche

Farever tourne sur **HashLink** : `Farever.exe` exécute `hlboot.dat`, un bytecode
qui contient encore les noms de toutes les classes, champs et méthodes du jeu.

1. **Analyse du bytecode** (`hltools/`) : le parseur lit `hlboot.dat` et en tire
   l'index de chaque fonction utile et la position de chaque champ lu.
2. **Connexion au jeu** (`GameLink`, dans `meter/farever_meter.py`) : un fil
   d'exécution en arrière-plan attend Farever, s'y attache avec **Frida**, et
   recommence après une fermeture.
3. **Lecture en jeu** (`frida/meter_hook.js`) : le script injecté retrouve la
   table des fonctions de HashLink et observe, **en lecture seule**, les coups
   (`ent.Unit.onInflictDamage` : montant, élément, critique, kill, sort), les
   effets de soin et la vie des cibles, les barres de boss, la faille, la zone,
   le groupe et la liste des joueurs du serveur. Il n'écrit rien dans le jeu et
   n'affiche rien.
4. **Moteur** (`App`, dans `meter/farever_meter.py`) : agrège les combats,
   détecte les pulls et kills de boss, construit les rapports de faille,
   enregistre l'historique, et construit chaque page de la fenêtre.
5. **Fenêtre** (`meter/menu_host.py` + `meter/web/`) : une fenêtre WebView2 dans
   son propre processus, reliée au moteur par ses entrées/sorties standard. Le
   moteur lui envoie l'état de la page plusieurs fois par seconde ; elle lui
   renvoie les clics.

Les soins ne sont jamais transmis tels quels au client : leur montant est
reconstitué à partir de l'effet de soin joué sur la cible et de la hausse de ses
points de vie, et leur taille réelle est estimée à partir des données de sorts
du jeu (d'où la colonne « excès » pour le soin perdu). La régénération naturelle
compte comme du soin.

## Organisation du dépôt

```
meter/         le moteur (farever_meter.py) et la fenêtre (menu_host.py, web/)
frida/         le script injecté dans le jeu (meter_hook.js)
hltools/       le parseur de bytecode HashLink et les générateurs de données
analysis_out/  données générées à partir du jeu (fonctions, champs, noms)
assets/        icône
packaging/     scripts de construction d'un exécutable (projet d'origine, non
               mis à jour pour ce fork)
```

## Limites connues

* Les rapports enregistrés avant la traduction gardent quelques libellés en
  anglais dans leurs fichiers ; l'affichage les traduit.
* L'appartenance au groupe se fait par nom de joueur.
* Si deux copies de Farever tournent en même temps, Farever+ se connecte à la
  première.
* Le suivi des morts en phase de boss est en attente d'une mise à jour du jeu
  (état « à terre »).
