# rhythm

A note taker wired into Hyprland. Press one key, write a line, and it files
itself onto a kanban board. A second view remembers what you were working on.

Everything lives in plain markdown in your vault. Nothing here is a database
you can't read, and every board is a file you can edit by hand.

---

## Quick reference

### Global — anywhere

| Key | Does |
|---|---|
| `SUPER + N` | Open / hide the app |
| `SUPER + C` | New note (only while the notepad view is focused) |
| **`SUPER + I`** | **Capture something you did away from the keyboard** — one line, no overlay |

`SUPER+I` is the answer to "I just talked to someone / went to the lab / fixed
a rig, and I want that recorded". Type one sentence, press Enter. Escape writes
nothing.

### Inside the app

| Key | Does |
|---|---|
| `ALT + Tab` | Next view · `ALT + Shift + Tab` previous |
| `ALT + r` | Jump straight to the radar |
| `:q` | Hide · `:q!` really quits |

### On the boards and the radar

| Key | Does |
|---|---|
| `j` `k` | Card down / up |
| `h` `l` | Column left / right (skips empty columns) |
| `gg` `G` | First / last card in the column |
| `x` | Remove the selected card (asks first) |
| `Enter` | Radar only: jump to that tmux pane |
| `r` | Refetch |
| `q` | Next view |

None of these apply on the notepad — that is just your normal Neovim.

---

## The four views

**notepad** — your real Neovim, editing today's note. Your config, your
plugins, your keys. `SUPER+C` starts a new note for today.

**activity board** — Active / Monitor / Route / Backlog. *What state is this in?*

**project board** — Sceptre / Invictus / GNC / Unsorted. *Whose is this?*

Both boards are two groupings of **one** card set, so they cannot disagree.

**radar** — Now / Touched today / Notifications. What you are doing, what you
have touched, and what is waiting on you.

---

## Writing notes

Notes are ordinary markdown in `unsorted/`. A line becomes a card when it
starts with a marker:

```markdown
Standup. Sim clock looked off again.

*Active/Sceptre> fix the soft-catch report
*Route> tell John the HIL harness is free after 3
*/Sceptre> untriaged but owned by us
*> anything at all
```

The shape is `*Activity/Project>`, and both halves are optional:

| You write | Files as |
|---|---|
| `*Active/Sceptre> text` | Active, Sceptre |
| `*Monitor> text` | Monitor, Unsorted |
| `*/Sceptre> text` | Backlog, Sceptre |
| `*> text` | Backlog, Unsorted |
| `- *Route/GNC> text` | works inside a list bullet too |

**Spelling is forgiven.** `*montiR/scEptEr>` files as Monitor / Sceptre, and
the note is rewritten to the correct spelling the first time it is picked up.
Anything unrecognised falls to Backlog and is left exactly as you typed it.

### What is *not* a marker

- **No space after `>`** — `*Active>text` is ignored. This is deliberate: it is
  what stops a pasted `*ptr->field = 3;` becoming a card.
- **No text** — `*Route>` on its own is ignored.
- A `- [ ] *Active> …` checkbox line is not a marker.

Markers are picked up when you save, and swept again every half hour.

---

## Vocabulary

```
Activities   Active   Monitor   Route   Backlog
Projects     Sceptre  Invictus  GNC
```

Adding a project is one word in `src/rhythm/vocab.py` — that list is
simultaneously the spelling, the column order and the spell-checker.

---

## The board file

`~/vaults/root-engine/kanban/tiberius_kanban.md`:

```markdown
## Active

- [ ] fix the soft-catch report @Sceptre ^rk-adfafc439fa6

## Route

- [ ] tell John the HIL harness is free after 3 ^rk-1876054c6e52

%% rhythm-seen rk-adfafc439fa6 rk-1876054c6e52 %%
```

The heading is the activity, `@Project` is the project, and `^rk-…` is the
card's identity. The `%% rhythm-seen %%` line is bookkeeping — it is what stops
a card you deleted coming back.

### Hand-editing: your edits always win

| You do | What happens |
|---|---|
| Move a card to another column | It stays moved |
| Change `@Sceptre` to `@GNC` | It stays changed — this is how you re-assign |
| Tick it `- [x]` | Stays ticked, and Route reminders skip it |
| Delete the line | Never comes back |
| Rename a note, reword a marker | Nothing happens to the card |

**The board is the source of truth after a card is first created.** Editing a
marker in a note once it has an `^rk-` id will *not* move the card — edit the
board instead.

---

## The radar

Every time you focus a tmux window, that becomes one row in a small log. No
timing, no measurement — only that you touched it.

- **Now** — where you are.
- **Touched today** — everything else you have been in today, plus anything you
  captured with `SUPER+I`.
- **Notifications** — what is waiting on you:
  - **Claude sessions blocked on input**, with how long they have been waiting.
    Press `Enter` to jump straight to that pane.
  - **Slack unread counts**, per workspace.
  - **Slack conversations that pinged you**, captured from the desktop
    notification as it arrives. `x` clears one once you have dealt with it.

Activities are identified by tmux *window*, so two panes in one window are one
thing. The label is the window's name, or the folder when the window is still
called `bash` — name a window with `C-a ,` and the radar follows.

`x` on the radar forgets an activity. A tmux window comes back the moment you
focus it again; a `SUPER+I` note is gone for good.

---

## Commands

You will mostly never type these — keys and timers call them.

| Command | Does |
|---|---|
| `rhythm new` | Create today's next note, print its path |
| `rhythm sync` | Fold markers from `unsorted/` onto the board |
| `rhythm board [activity\|project]` | A board as JSON (read-only) |
| `rhythm radar` | The radar as JSON (read-only) |
| `rhythm remove <id>` | Drop a card |
| `rhythm radar-note <text…>` | Record an offline activity — what `SUPER+I` calls |
| `rhythm radar-forget <key>` | Drop a radar activity |
| `rhythm remind-route` | The Route digest notification |
| `rhythm migrate` | One-off: split the old mixed columns |

---

## Where things live

```
~/vaults/root-engine/unsorted/note_MM-DD-YY.md   your notes
~/vaults/root-engine/kanban/tiberius_kanban.md   the board
~/.local/state/rhythm/radar.db                   the activity log
~/dev/rhythm-note-taker/src/rhythm/              the code — edits are live
```

The Python is **not** vendored into the nix flake: `rhythm` runs the directory
in `~/dev` directly, so an edit takes effect on the next invocation with no
rebuild. Keys, timers and the tmux hook are in `~/nixos-config`, and those
*do* need `rebuild`.

**Automation:** markers are folded on save and every half hour; Route tasks are
nudged hourly 11:00–14:00 on weekdays; a tmux hook records focus changes; a
mako hook records Slack notifications.

---

## Gotchas

- **`g`-prefixed commands.** `gg` and `G` move between cards on the boards and
  radar. Other `g` motions work normally.
- **`q` cycles views** on the boards and radar, so macro recording is not
  available there. It is in the notepad.
- **`:q` hides, `:q!` quits.** Hiding is usually what you want — reopening is
  instant because the app stays alive.
- **The boards are read-only apart from `x`.** There is no key to move a card
  or tick it off. That is hand-editing the markdown, deliberately.
- **Vertical overflow is not scrollable.** A column taller than the window
  truncates with `+N more`.
- **Slack unread counts come from Slack's own state file**, so they are only as
  fresh as the last time the app wrote it. Per-*channel* unreads are not
  available at all — Slack's API does not expose them for channels, and has
  not since `channels.info` was retired in 2021.
- **Notification capture only sees what arrived while you were logged in.** A
  message that came in overnight shows in the workspace count, not as a card.

---

## Testing

```bash
cd ~/dev/rhythm-note-taker && python3 -m unittest discover -s tests
```
