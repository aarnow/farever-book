# Farever France

Compagnon de second écran pour **Farever**, en français. Une seule fenêtre
Windows, à côté du jeu, qui lit ce qui se passe en jeu et le garde consultable
même jeu fermé :

* **externe au jeu** : aucun overlay, rien n'est jamais dessiné dans Farever, le
  HUD du jeu reste intact ;
* **en lecture** : les données du jeu sont lues en mémoire, jamais modifiées ;
* **usage personnel** : le projet n'est pas redistribué.

## Position du studio

Message de Steven, community manager de Shiro Games, sur le Discord de Farever,
le 27/05/2026 :

> I'll confirm what has been said earlier
>
> While we won't promote the use of add-ons during the EA (to keep players on
> the intended experience at first), we won't condemn personal use of add-ons
> like minimaps or DPS meter 🙏

Autrement dit : pendant l'accès anticipé, le studio ne met pas en avant les
add-ons, mais tolère leur usage personnel. C'est le cadre de ce projet : usage
personnel, sans diffusion. Cette tolérance peut évoluer, à surveiller.

Pour être exact sur ce que fait Farever France dans le jeu : il ne modifie
aucune donnée et n'envoie rien sur le réseau, mais Frida s'injecte dans le
processus du jeu et dévie quelques fonctions en mémoire pour être prévenu des
coups et des soins. En cas de plantage à signaler aux développeurs, reproduis-le
sans Farever France avant de l'envoyer.

## Les onglets

| Onglet | Contenu |
|---|---|
| **En direct** | Dégâts et soins du groupe (ou de tous les joueurs), détail du joueur sélectionné (sorts, critiques, types de dégâts), durée du combat, parse 60 s. Ta **chance de butin** (compteurs du Puits des âmes, offrandes actives) et tes **statistiques**, relues chaque minute. |
| **Failles** | Tes compteurs de failles, puis chaque faille terminée avec son rapport : phase de faille et phase du boss, durée, DPS, HPS, MVP, classements, types de dégâts ; copiable en image ou en texte. Suppression par sélection, nombre de failles conservées réglable. Récompenses des failles et chances (paliers de portails, armes légendaires). |
| **Donjons** | Chaque run enregistré : difficulté, résultat, temps, morts, groupe, rapport en deux phases et butin. Records par donjon et difficulté, table de butin du donjon. |
| **Collection** | Montures, planeurs, compagnons, apparences d'équipement et objets du Codex, avec pour chacun la façon de l'obtenir d'après les données du jeu. |
| **Chasse** | Tes kills par monstre (Codex du jeu) et leur rang, par région. |
| **Carte** | La carte de Siagarta avec les points de complétion (coffres, orbes rouges, obélisques…), trouvés ou à récupérer. |
| **Succès** | Les succès du compte, leur progression et leurs récompenses. |
| **Inspecter** | Les joueurs du serveur et, sur demande, la fiche d'un joueur comme en jeu : équipement par emplacement avec les **statistiques de chaque pièce**, **attributs** et stats secondaires, armes et arsenal, imprégnations, barre de sorts, talents et runes. |
| **Réglages** (⚙) | Colonnes de soins, réinitialisation au pull d'un boss, raccourci clavier, taille de l'interface, dossiers. |
| **Aide** (?) | Utilisation, lancement avec Steam, et **Réparer** après une mise à jour du jeu. |
| **Événements** (☰) | Fenêtre des kills de boss, records, fins de faille et donjons ; une pastille compte les nouveaux. |

En haut, le minuteur de la prochaine faille, puis l'état du jeu : **Jouer**
(lance Farever par Steam), **Connexion…**, ou **En jeu** avec le serveur. Un
clic sur cet état ouvre le **suivi de connexion**, étape par étape.

Les statistiques d'équipement et les attributs sont **calculés comme le fait
le jeu**, à partir de ses données : un équipement ne garde en mémoire que son
niveau, ses améliorations et ses augmentations, et le jeu en dérive ses
statistiques (voir `hltools/gear_stats_data.py`).

### Réinitialiser en plein combat

Un raccourci clavier global (par défaut **Maj + \\**) réinitialise le combat
sans quitter le jeu des yeux. Il ne fonctionne que lorsque Farever est au
premier plan, n'affiche rien dans le jeu, et se change dans les **Réglages**.

## Installation et lancement

Farever France se lance depuis les sources (Windows). Il faut
[Python](https://www.python.org/downloads/), puis un double-clic sur
**`Installer Farever France.cmd`** : il installe les modules (dans les bonnes
versions) et crée le raccourci **Farever France** sur le Bureau et dans le menu
Démarrer. Le raccourci lance l'application sans console ; son journal est alors
dans `%LOCALAPPDATA%\FareverMeter\meter.log` (bouton dans les Réglages).

À la main, avec la console (pratique pour lire le journal en direct) :

```
pip install frida==17.18.0 pillow pywebview
python meter/farever_meter.py
```

La fenêtre utilise **WebView2**, déjà présent sur Windows 10 et 11 à jour.

**Frida doit être en 17.18.0.** La 17.19.0 fait planter tout processus dont
elle se détache, donc Farever à la fermeture de Farever France (mesuré le
28/09/2026 sur Windows 11 build 26200). Farever France refuse de s'attacher
avec la 17.19.0 et l'indique dans sa fenêtre.

Pour arrêter : ferme la fenêtre, ou clic droit sur l'icône près de l'horloge.
Farever France se détache alors proprement du jeu. **Ne l'arrête pas depuis le
Gestionnaire des tâches** : le processus serait tué avant de s'être détaché, ce
qui peut déstabiliser Farever.

## Où sont les fichiers

Depuis les sources, tout est écrit dans le dossier du projet :

| Quoi | Où |
|---|---|
| Rapports de faille (`.json`, `.txt`, `.png`) | `failles/` |
| Runs de donjon | `donjons/` |
| Collection, kills par monstre, succès, carte | `.meter_collection.json`, `.meter_codex.json`, `.meter_achievements.json`, `.meter_elements.json` |
| Réglages, position de la fenêtre, records de boss | `.meter_settings.json`, `.meter_position.json`, `.meter_besttimes.json` |
| Données tirées du jeu | `analysis_out/` |

## Après une mise à jour de Farever

Les index de fonctions et les positions des champs changent d'une version du
jeu à l'autre. À chaque connexion, Farever France compare le `hlboot.dat` du
jeu en cours avec celui qui a servi à générer `analysis_out/`, et **régénère
les données tout seul** si besoin (une dizaine de secondes ; l'indicateur
affiche « Mise à jour… »). Le bouton **Réparer** de l'Aide refait cette lecture
depuis zéro et se reconnecte, sans relancer l'application.

Si Farever est installé à un endroit inhabituel, indique le chemin complet de
`hlboot.dat` dans la variable d'environnement `FAREVER_HLBOOT`.

## Comment ça marche

Farever tourne sur **HashLink** : `Farever.exe` exécute `hlboot.dat`, un
bytecode qui garde les noms de toutes les classes, champs et méthodes du jeu.

1. **Données du jeu** (`hltools/`) : le parseur lit `hlboot.dat` (index des
   fonctions, positions des champs, et même le code des fonctions avec
   `hlbc_code.py`) ; les générateurs tirent de `res.pak` et `data.cdb` les
   noms français, images, catalogues et règles de jeu dans `analysis_out/`.
2. **Lecture en jeu** (`frida/meter_hook.js`) : le script injecté retrouve la
   table des fonctions de HashLink et observe, en lecture seule, les coups,
   les soins, les barres de boss, la faille, la zone, le groupe, les joueurs du
   serveur, la collection, le Codex et les profils. Il n'écrit rien dans le jeu
   et n'affiche rien.
3. **Moteur** (`meter/`, un module par responsabilité) :

   | Module | Rôle |
   |---|---|
   | `farever_meter.py` | démarrage et arrêt |
   | `app.py` | l'application : état, actions, pages |
   | `gamelink.py` | la connexion au jeu (attache, hook, reconnexion, étapes) |
   | `combat.py` | comptage dégâts/soins, enregistrement des failles et donjons |
   | `gamedata.py` | les tables tirées du jeu et leur régénération |
   | `gearstats.py` | statistiques d'équipement et attributs, comme le jeu |
   | `views.py` | construction des pages à partir des données |
   | `reports.py` | rapports de faille : page et image |
   | `bridge.py` | le processus de la fenêtre et le canal vers elle |
   | `winsys.py` | Windows : DPI, raccourci, icône, presse-papiers, instance unique |
   | `common.py` | chemins, constantes, petits utilitaires |

4. **Fenêtre** (`meter/menu_host.py` + `meter/web/`) : une fenêtre WebView2
   sans cadre, dans son propre processus, reliée au moteur par ses
   entrées/sorties standard. Le moteur lui envoie l'état de la page plusieurs
   fois par seconde ; elle lui renvoie les clics.

Les soins ne sont jamais transmis tels quels au client : leur montant est
reconstitué à partir de l'effet de soin joué sur la cible et de la hausse de
ses points de vie, et leur taille réelle est estimée à partir des données de
sorts du jeu (d'où la colonne « excès »).

## Organisation du dépôt

```
meter/         le moteur (modules ci-dessus) et la fenêtre (menu_host.py, web/)
frida/         le script injecté dans le jeu (meter_hook.js)
hltools/       le parseur de bytecode HashLink et les générateurs de données
analysis_out/  données générées à partir du jeu
assets/        icône, icônes de classe, images de la fiche personnage
packaging/     raccourci, icône, et construction d'un exécutable
```

## Limites connues

* L'appartenance au groupe se fait par nom de joueur.
* Si deux copies de Farever tournent en même temps, Farever France se connecte
  à la première.
* Les attributs de la fiche ne comptent ni le blocage du bouclier ni la
  puissance des armes ; les effets actifs sont ceux du moment de l'analyse.
* Le suivi des morts en phase de boss attend une mise à jour du jeu (état « à
  terre »).
