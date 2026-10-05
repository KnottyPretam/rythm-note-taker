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
| `<leader>tt` | Neo-tree, rooted at the vault |
| `:q` | Hide · `:q!` really quits |

### On the boards and the radar

| Key | Does |
|---|---|
| `j` `k` | Card down / up |
| `h` `l` | Column left / right (skips empty columns) |
| `gg` `G` | First / last card in the column |
| `x` | Remove the selected card (asks first) |
| `Enter` | Radar only: jump to that pane, or focus that window |
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

**radar** — Now / Touched today / Notifications. The last 2 hours, the 14
before that, and what is waiting on you.

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

Three things feed it, none of them polled: a tmux hook on every pane focus,
Hyprland's event socket for the windows that are not tmux, and Claude Code's
own hooks for the prompts you send. No timing, no measurement — only that you
touched it.

**Now** and **Touched today** are the same lane split by age. They show exactly
the same kinds of thing; the only difference is *when*.

- **Now** — touched in the **last 2 hours**.
- **Touched today** — touched between **2 and 16 hours** ago.

Anything older than 16 hours drops off both. It is a rolling window, not a
calendar day: at 00:30 what you were doing at 23:00 is still what you are
doing, and a midnight boundary would empty the radar mid-thought.

An activity is listed at its **latest** touch, so it sits in exactly one lane —
something you looked at this morning *and* a minute ago is in Now, not history.

Within each lane, in this order:

1. **Prompts you sent to an agent**, newest first.
2. **Chat agents and the ssh terminal** — the windows the tmux hook cannot
   see, because ChatGPT/Claude/Grok are Chromium web apps and your ssh
   session runs a *remote* tmux.
3. **tmux windows**, and anything you captured with `SUPER+I`.

Then:

- **Notifications** — what is waiting on you, regardless of age:
  - **Prompts that have finished and that you have not looked at.**
  - **Claude sessions blocked on input**, with how long they have been waiting.
  - **Slack unread counts**, per workspace.
  - **Slack conversations that pinged you**, captured from the desktop
    notification as it arrives.

`Enter` jumps to whatever is behind a card: a tmux pane, or the Hyprland window
for a chat agent. A dim border means there is nothing behind it.

### Notifications clear themselves

A prompt is *unviewed* when it has finished and **you have not focused its
window since**. So looking at the pane clears the card — there is no read flag,
and nothing to mark. The card is not deleted, it moves down into **Touched
today**.

The link to a window is the tmux window id, or — for a Claude session started
outside tmux — the session name, which Claude puts in the terminal title. A
notification that nothing clears ages out after a week.

`x` still works, for the things you are simply never going to look at.

### What does not become a prompt

- **Slash commands.** `/plan`, `/model` and `/effort` are you operating the
  tool, not asking it something. Filtered via Claude's `UserPromptExpansion`
  hook, not by matching a leading `/`.
- **Machine-injected turns** — task notifications, `/loop` wakeups, SDK calls.
  The hook's `source` field is what separates these from a prompt you typed.
- **Turns parked on background work.** A turn waiting on a subagent has not
  finished, so it stays in Touched today rather than claiming your attention.

Web-app prompts (claude.ai, ChatGPT, Grok) cannot be captured — those apps
record nothing per prompt that is readable from outside. The chat agents appear
as *attention*, never as prompts.

### Identity

tmux activities are identified by *window*, so two panes in one window are one
thing. The label is the window's name, or the folder when the window is still
called `bash` — name a window with `C-a ,` and the radar follows.

Other terminals are identified by their Hyprland window, and labelled from the
title. That is a heuristic: all Ghostty windows share one process, so the title
is the only thing that tells them apart.

`x` forgets an activity. A tmux window comes back the moment you focus it
again; a `SUPER+I` note is gone for good.

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

These four are Claude Code hooks, fed a JSON payload on stdin. They print
nothing and always exit 0 — a `UserPromptSubmit` hook's stdout is appended to
the model's context, and a non-zero `Stop` hook tells Claude it may not stop.

| Hook command | Claude event |
|---|---|
| `rhythm prompt-sent` | `UserPromptSubmit` |
| `rhythm prompt-done` | `Stop` |
| `rhythm prompt-failed` | `StopFailure` — a turn that died never fires `Stop` |
| `rhythm prompt-expand` | `UserPromptExpansion` — the slash-command filter |

And one daemon:

| Command | Does |
|---|---|
| `rhythm focus-watch` | Log window focus; run by `rhythm-focus.service` |

---

## Where things live

```
~/vaults/root-engine/unsorted/note_MM-DD-YY.md   your notes
~/vaults/root-engine/kanban/tiberius_kanban.md   the board
~/.local/state/rhythm/radar.db                   the activity log + prompts
~/dev/rhythm-note-taker/src/rhythm/              the code — edits are live
```

The Python is **not** vendored into the nix flake: `rhythm` runs the directory
in `~/dev` directly, so an edit takes effect on the next invocation with no
rebuild. Keys, timers and the tmux hook are in `~/nixos-config`, and those
*do* need `rebuild`.

**Automation:** markers are folded on save and every half hour; Route tasks are
nudged hourly 11:00–14:00 on weekdays; a tmux hook records pane focus;
`rhythm-focus.service` records window focus; a mako hook records Slack
notifications; and four Claude Code hooks record your prompts.

The prompt hooks live in `/etc/claude-code/managed-settings.d/`, not in
`~/.claude/settings.json` — Claude writes to that one itself, so it cannot be
a read-only nix file. Managed settings are **read at startup**: restart your
Claude sessions after a rebuild, or the hooks will not be loaded.

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
- **The radar's two lanes are 2 hours and 16 hours**, not "now" and "today".
  Both boundaries are `NOW_WINDOW` and `TODAY_WINDOW` at the top of
  `src/rhythm/radar.py`, in seconds.
- **Slack unread counts come from Slack's own state file**, so they are only as
  fresh as the last time the app wrote it. Per-*channel* unreads are not
  available at all — Slack's API does not expose them for channels, and has
  not since `channels.info` was retired in 2021.
- **Notification capture only sees what arrived while you were logged in.** A
  message that came in overnight shows in the workspace count, not as a card.
- **`<leader>tt` behaves differently in here.** In the note app it opens the
  tree at the vault, so a search starts where the notes are. Your everyday
  Neovim keeps toggle-and-reveal, which is what you want inside a repo.
- **Prompt hooks only apply to sessions started after a rebuild**, because
  managed settings are read once at startup. They also record *this* session:
  wiring them means every prompt you send any agent lands in the radar.
- **A second non-tmux Ghostty window will be mislabelled.** Terminals are told
  apart by their title, because every Ghostty window shares one process.

---

## Testing

```bash
cd ~/dev/rhythm-note-taker && python3 -m unittest discover -s tests
```
