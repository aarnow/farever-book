# Launch it with the game, from Steam

> Steam starts both at once, and you never think about it again.

Since the meter waits for Farever, you can ask Steam to launch both together.

In Steam, right-click **Farever** → **Properties** → **General** →
**Launch options**, and paste this on a single line:

```
cmd /c start "" "C:\Users\YOU\AppData\Local\Programs\FareverBook\FareverBook.exe" & %command%
```

Replace `YOU` with your Windows user name.

To get the exact path without typing it: search for **Farever Book** in the
Start menu, right-click → **More** → **Open file location**, then right-click
the shortcut → **Properties** and copy the **Target** field.

From now on, launching Farever from Steam starts Farever Book first, then the
game.

## Why this form

Steam replaces `%command%` with the game and its arguments, but on Windows it
does not pass launch options through a shell: `cmd /c` is what gives the `&`
its meaning.

`start ""` launches the meter *without waiting for it*, and the empty `""` is
the window title `start` expects before a quoted path. Without it, the path
would be taken as the title.

Steam then waits for `cmd`, and `cmd` waits for the game: **play time, the
Steam overlay and the in-game status keep working**, which is not the case
with recipes that also launch the game with `start`.

## Two things to know

* **When the game closes, Farever Book stays open** and goes back to
  "Offline": you can read your rifts and fights again, and it reconnects on its
  own at the next session.
* **If you launch Farever outside Steam** (from a desktop shortcut, for
  example), the launch options do not apply: launch the meter yourself.

If this looks too complicated, it is entirely optional. The meter waits for
the game, so launching it whenever you like works just as well.
