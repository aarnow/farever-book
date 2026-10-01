# Le lancer avec le jeu, depuis Steam

> Steam lance les deux d'un coup, et tu n'y penses plus.

Comme le compteur attend Farever, tu peux demander à Steam de lancer les deux
ensemble.

Dans Steam, clic droit sur **Farever** → **Propriétés** → **Général** →
**Options de lancement**, et colle ceci sur une seule ligne :

```
cmd /c start "" "C:\Users\TOI\AppData\Local\Programs\FareverMeter\FareverMeter.exe" & %command%
```

Remplace `TOI` par ton nom d'utilisateur Windows.

Pour avoir le chemin exact sans le taper : cherche **Farever France Meter** dans le
menu Démarrer, clic droit → **Plus** → **Ouvrir l'emplacement du fichier**, puis
clic droit sur le raccourci → **Propriétés** et copie le champ **Cible**.

Désormais, lancer Farever depuis Steam démarre d'abord Farever France, puis le jeu.

## Pourquoi cette forme

Steam remplace `%command%` par le jeu et ses arguments, mais sous Windows il ne
passe pas les options de lancement par un shell — c'est `cmd /c` qui donne un
sens au `&`.

`start ""` lance le compteur *sans l'attendre*, et le `""` vide est le titre de
fenêtre que `start` attend avant un chemin entre guillemets. Sans lui, le chemin
serait pris pour le titre.

Steam attend ensuite `cmd`, et `cmd` attend le jeu : **le temps de jeu,
l'overlay Steam et le statut en jeu continuent de fonctionner** — ce qui n'est
pas le cas des recettes qui lancent aussi le jeu avec `start`.

## Deux choses à savoir

* **Quand le jeu se ferme, Farever France reste ouvert** et repasse à « Hors jeu » :
  tu peux relire tes failles et tes combats, et il se reconnecte tout seul à la
  prochaine session.
* **Si tu lances Farever hors de Steam** — depuis un raccourci sur le bureau,
  par exemple — les options de lancement ne s'appliquent pas : lance alors le
  compteur toi-même.

Si ça te paraît trop compliqué, c'est entièrement facultatif. Le compteur attend
le jeu, donc le lancer quand tu veux marche tout aussi bien.
