# Two-board kanban — what changed, and why

Handoff note. Written 2026-09-23, after splitting the single mixed kanban into an
**activity board** and a **project board**, adding card selection with vim keys,
colour coding, and fuzzy keyword matching.

Everything below is **built, tested and applied** unless a line says otherwise.
The one thing outstanding is a `nixos-rebuild switch` (see *Open threads*).

---

## 1. The problem this fixed

The board was one file with flat `## ` headings that mixed two unrelated ideas —
activity (`Active`, `Monitor`, `Route`) and project (`Sceptre`, `Invictus`,
`GNC`), plus `Unknown`. A card sat in exactly one, so a task was either "in
progress" **or** "belongs to Sceptre", never both.

Now a card has **both**, and the two boards are two groupings of one card set:

- **activity board** — Active, Monitor, Route, Backlog
- **project board** — Sceptre, Invictus, GNC, Unsorted, each card border
  coloured by its activity

One source of truth, so the two boards cannot disagree.

## 2. The data model

**Activity is the heading. Project is an `@token` on the card line.**

```markdown
## Active

- [ ] fix the soft-catch report @Sceptre ^rk-a1b2c3d4e5f6
- [ ] watch disk usage ^rk-0f1e2d3c4b5a           <- no project = Unsorted
```

Hand-editing is unchanged in spirit: move the line to another `## ` column to
change its activity, edit `@Sceptre` to change its project.

### Marker syntax in notes

```
*Active/Sceptre> fix the soft-catch report
*Route/GNC> call the vendor
*Monitor> watch disk usage          <- no project  = Unsorted
*/Sceptre> untriaged but owned      <- no activity = Backlog
*> anything                         <- Backlog, Unsorted
```

The user chose the `Activity/Project` prefix explicitly over an Obsidian `#tag`.

### Keyword resolution (`src/rhythm/vocab.py`, new)

`ACTIVITIES` and `PROJECTS` are the canonical spellings, the column order **and**
the corrector's vocabulary, all three. Adding a project is one word in one list.

`resolve()` normalises to `[a-z0-9]`, tries exact, then
`difflib.get_close_matches(cutoff=0.6)`. Measured:

```
montiR  -> Monitor  0.77      invictis -> Invictus 0.88
scEptEr -> Sceptre  0.86      meeting  -> none     0.31  -> Backlog
```

`classify()` resolution order:

1. slot 1 against `ACTIVITIES`, slot 2 against `PROJECTS`
2. **back-compat** — slot 1 matching no activity but *matching a project* is
   read as the project, activity Backlog. Every pre-existing note in the vault
   is the old one-slot form `*Sceptre>`; without this they all lose their
   project. Safe because no project name comes near an activity name
   (`sceptre` 0.31, `gnc` 0.22 — both far under the cutoff). There is a test
   asserting this stays true, so adding a project called e.g. "Rout" would fail
   loudly rather than silently corrupt filing.
3. anything left → Backlog / Unsorted

**Correction is written back into the note** in the same atomic write that
stamps the id: `*montiR/scEptEr>` becomes `*Monitor/Sceptre>`. Only the
*spelling* is fixed, never the *shape* — `*scEptEr>` becomes `*Sceptre>`, not
`*Backlog/Sceptre>`, because rewriting someone's one-slot marker into the
two-slot form is a bigger edit to their note than fixing a typo, and the card
files identically either way. Only on first stamp, so notes are never churned.

## 3. Files changed

| file | what |
|---|---|
| `src/rhythm/vocab.py` | **new** — the two vocabularies, `resolve`, `classify` |
| `src/rhythm/notes.py` | `MARKER` grew the `/Project` slot; `scan()` returns `(id, activity, project, text)`; `_canonical()` write-back |
| `src/rhythm/board.py` | `CARD`/`BARE` grew the `@project` group; `_project_of` guard; `_line`; `payload(view)`; `sync()` under the new model; `migrate()` |
| `src/rhythm/__main__.py` | `board [activity\|project]`, `migrate` |
| `tests/test_grammar.py` | **new** — 20 tests, no filesystem |
| `~/nixos-config/…/lua/rhythm.lua` | four views, selection model, nav keys, colours |
| `~/nixos-config/home.nix` | window rule → `maximize` |

### Two guards that are load-bearing

**`_project_of`** — a trailing `@word` is a project **only if it resolves**;
otherwise it is handed back to the card text verbatim. Without it,
`- [ ] ping @alice ^rk-…` silently files itself under a project "alice". Found
by probing, not by reasoning; there is a test.

**Whitespace after `>` stays required.** That is what keeps `*ptr->field = 3;`
in pasted code from becoming a card. Adding the `/` slot does not widen it —
`*see https://x.com> foo` still fails, because `:` is in neither slot's
charset. Both are tested.

## 4. The renderer (`~/nixos-config/dotfiles/nvim/.config/nvim/lua/rhythm.lua`)

`ORDER` is now `{ notepad, activity, project, radar }`. Both boards go through
the **same** `compose()`; the card grammar stays in `board.py` and is not
reimplemented in Lua.

**Selection is keyed on card id**, never a row index — the board is refetched on
every sync by the directory watcher, and an index would slide under the
selection whenever a card was added above it. `M.selcol` is the *absolute*
column index so it survives lane scrolling; `M.selidx` is the depth, which is
what the selection falls back to when its card disappears (you removed it).
That fallback re-composes once, only in that rare case.

| key | does |
|---|---|
| `j` / `k` | card down / up within a column |
| `h` / `l` | column left / right, **skipping empty columns**, scrolling an off-screen column into view first |
| `g` / `G` | first / last card in the column |
| `x` | remove selected, after a confirm |
| `r`, `q`, `Alt-Tab` | refetch, next view, cycle views |

`d` and the old cursor-region lookup are **gone** — two ways to remove with
different targets is a bug waiting to happen.

**Colour is foreground-only.** ghostty runs `background-opacity=0.7`, so any
cell given a `bg` renders 100% opaque and looks like a hard block. The groups
link to the theme's own diagnostics rather than hardcoded hex, so they follow
the colorscheme: `RhythmActive`→`DiagnosticError` (red),
`RhythmMonitor`→`DiagnosticOk` (green), `RhythmRoute`→`DiagnosticWarn`
(yellow). **Backlog is deliberately absent** — "no colour" is the fourth state.
`RhythmSel`→`CursorLineNr`, and selection outranks tint on the border.

Which board gets tinted is decided in **`board.py`**, not Lua: the payload sets
`entry.tint` and the renderer colours whatever arrives with one. `entry.meta`
carries the *other* dimension — project on the activity board, activity on the
project board — so the project board never depends on colour alone, which
matters precisely for Backlog.

`M.refresh()` fetches **both** boards. One file backs them both, so the watcher
and every removal refresh both; refreshing one would leave the other showing a
card that no longer exists.

## 5. The window

`home.nix`, the `rhythmToggle` exec-bracket, is now
`[workspace special:rhythm; float; maximize]`.

**Not** `size monitor_w monitor_h`: waybar is a `layer: top` bar with a 32px
exclusive zone (`hyprctl monitors` → `reserved=[0,32,0,0]`), so a literal
full-size window sits *under* the bar and loses its top 32px — exactly where the
tabline is drawn. `maximize` respects the reservation. It exists in the running
Hyprland 0.56.2 (checked in the binary). If it ever misbehaves for a floating
window on a special workspace, the fallback is
`size monitor_w monitor_h-32; move 0 32` — muParser does arithmetic, but not
percentages.

## 6. Migration — already run on the live vault

`migrate()` is idempotent and writes a backup first. It ran against
`/home/pretamc/vaults/root-engine/kanban/tiberius_kanban.md`:

```
## Route                              ## Active
- [ ] tell someone …    ^rk-1876…     ## Monitor
## Sceptre                     →      ## Route
- [ ] wire the radar …  ^rk-30b6…     - [ ] tell someone … ^rk-1876…
- [ ] Write test report ^rk-7ee0…     ## Backlog
## Unknown                            - [ ] wire the radar … @Sceptre ^rk-30b6…
- [ ] work out what …   ^rk-85a1…     - [ ] Write test report @Sceptre ^rk-7ee0…
                                      - [ ] work out what … ^rk-85a1…
```

Backup: `kanban/tiberius_kanban.md.pre-split`. The id set was diffed before and
after — identical. **The seen-set is preserved verbatim, and must always be:** an
id in the seen-set that is not on the board can never be resurrected by `sync`,
so a card dropped by a migration is dropped forever.

## 7. Verified, and how

- **20 unit tests**, no filesystem: `cd ~/dev/rhythm-note-taker && python3 -m unittest discover -s tests`
- **Migration on a copy of the vault** before the real one; idempotent on a
  second run; id set identical.
- **End-to-end sync**: `*montiR/scEptEr> dummy task` → note rewritten to
  `*Monitor/Sceptre>`, card under `## Monitor` with `@Sceptre`, tint `monitor`.
  `*ACTIVE/gnc>` → `*Active/GNC>`. `*>` → Backlog.
- **Renderer driven headless** (`nvim --headless -u NONE --cmd 'set rtp^=…'`,
  with a PATH shim pointing `rhythm` at the test vault): both payloads, the
  navigation keys, the extmark highlight groups, and removal.
- **Removal refreshes both boards** — activity went 7 → 6 cards after removing
  from the project board — and the selection fell back to the next card.

Two bugs this caught, both invisible to inspection:

1. `l` into an empty column (Invictus) silently swallowed the keypress.
2. `@alice` at the end of a card would have become a project.

**Not verified:** anything requiring a real window — that `maximize` gives the
expected geometry, and that the colours read well against the live wallpaper at
0.7 opacity. Both need the switch below.

## 8. Open threads

1. **`sudo nixos-rebuild switch --flake ~/nixos-config#nixos` has not been run**
   — Claude cannot sudo in that session. Until then `~/.config/nvim/lua/rhythm.lua`
   is the OLD three-view file and the window is still 0.9 size. The **Python is
   already live** regardless, because the nix wrapper runs
   `~/dev/rhythm-note-taker/src/rhythm` directly. After switching, close the
   window with `:q` and press SUPER+N — a running window keeps the old renderer.
2. **Nothing is committed**, in either repo. `src/rhythm/vocab.py` and `tests/`
   are untracked. Note `~/nixos-config` is a **git flake**: untracked files are
   invisible to evaluation, so anything new there must be `git add`ed before it
   builds.
3. **The activity radar is next** — see `activity-radar.md`. `fetch("radar")` in
   `rhythm.lua` still returns a hardcoded placeholder, and `compose()` already
   supports what it needs: `entry.meta` is right-aligned on the last text line
   (intended for an age string) and `column.accent` is a free categorical slot
   for a different colour ramp.
4. **Vertical overflow is not scrollable.** A lane taller than the window is
   hard-truncated with `+N more`, and selection clamps to drawn cards. At the
   maximised size a lane fits roughly ten cards, and triaging surfaces the rest,
   so nothing is permanently unreachable — but per-lane scrolling is the fix if
   it becomes annoying.
5. **The dev-layout escape hatch still stands.** The Python is deliberately not
   vendored into the flake, so an edit takes effect on the next invocation with
   no rebuild. To promote it, see the comment above `rhythm` in `home.nix`:
   copy `src/rhythm` to `~/nixos-config/scripts/rhythm`, `git add -A`, and swap
   `SRC` for `${./scripts/rhythm}`.

## 9. Key paths

```
~/dev/rhythm-note-taker/src/rhythm/{vocab,notes,board,paths,__main__}.py
~/dev/rhythm-note-taker/tests/test_grammar.py
~/nixos-config/dotfiles/nvim/.config/nvim/lua/rhythm.lua
~/nixos-config/home.nix                    # rhythm*, lines ~98-245
/home/pretamc/vaults/root-engine/kanban/tiberius_kanban.md
/home/pretamc/vaults/root-engine/kanban/tiberius_kanban.md.pre-split
```
