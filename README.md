# Farever Book

**Farever Book** est une application qui vient se placer à côté de
**Farever**, sur ton second écran. Elle suit tes sessions en direct (dégâts,
soins, failles, donjons, butin…) et peut aussi s'utiliser **hors jeu**, pour
explorer un sujet ou composer un build.

Disponible en anglais et en français, **pour Windows uniquement** (Windows 10
ou 11).

## Ce qu'elle propose

* **En jeu** : le compteur de dégâts et de soins, ton groupe, tes chances de
  butin.
* **Failles et donjons** : le butin, les rapports de chaque faille et de chaque
  run, tes records.
* **Collection, Codex, Succès, Carte** : ta progression, ce qu'il te manque et
  comment l'obtenir.
* **Inspecter** : l'équipement, les talents et les sorts des joueurs autour de
  toi.
* **Build** : composer et comparer des builds, avec leurs statistiques et une
  simulation des dégâts, puis les partager.

## Lecture seule

Farever Book s'appuie entièrement sur les données de **ta version installée du
jeu**, et sur tes sessions quand il tourne en même temps que Farever.

Il ne fait **que lire** : rien n'est jamais écrit ni modifié dans le jeu, et
rien n'y est affiché. Aucune donnée n'est collectée : tout ce que l'application
garde reste sur ton ordinateur. Son code est public pour que chacun puisse le
vérifier.

## Installation

Farever Book fonctionne **uniquement sous Windows** (10 ou 11). Télécharge
l'installateur `FareverBook-<version>-Setup.exe` dans les
[**releases de ce dépôt**](https://github.com/aarnow/farever-book/releases),
uniquement, puis lance-le. Au premier démarrage, l'application lit les données
de ton jeu : c'est l'affaire de quelques instants.

## Mises à jour

* **Une nouvelle version de Farever Book** : un bouton **Mettre à jour**
  apparaît dans l'en-tête de l'application. Un clic suffit : elle télécharge et
  installe la nouvelle version, en gardant tes builds et ton historique.
* **Une mise à jour du jeu** : l'application relit d'elle-même les données du
  jeu quand elles changent. Si un module ne fonctionne plus, le bouton
  **Réparer** de l'onglet Aide le remet en état dans la plupart des cas.

## Projet de fan

Farever Book est un projet de fan, communautaire et gratuit. Il n'est **ni
affilié à Shiro Games, ni approuvé par le studio**. Farever et ses contenus
appartiennent à Shiro Games.

Le code est publié sous la licence [PolyForm Strict 1.0.0](LICENSE) : il peut
être lu, vérifié et compilé, mais pas redistribué ni modifié pour en publier une
autre version. Les idées, les bugs et les questions sont les bienvenus dans les
**issues**. Pour proposer une modification du code (une *pull request*), tu as
le droit de forker ce dépôt et de modifier ta copie dans ce seul but, et tu
acceptes que ta proposition puisse être intégrée à Farever Book, sous sa
licence.

## Remerciements

Farever Book existe aussi grâce à **Brudr**, auteur de
[**Farever+**](https://github.com/brudrbear/FareverMeter), qui a
généreusement partagé son code et nous a autorisés à le reprendre. Sa base,
notamment la lecture du bytecode HashLink et la lecture du jeu avec Frida, a
servi de point de départ à ce projet. Merci à lui !
