"""The kanban board: parse, sync markers in, read the Route column out.

Two rules make this safe to run repeatedly:

  * sync NEVER deletes a line from the board, and
  * a card is added only if its id is neither on the board NOR in the seen-set.

Together they mean your manual edits always win. Move a card between columns and
the move sticks; tick it off and it stays ticked; delete it and it is never
resurrected. The seen-set lives in the board file itself, written in the same
atomic replace as the cards, so the cards and the bookkeeping can never desync.
"""
import os
import re

import vocab
from paths import BOARD, atomic_write

SEEN = re.compile(r"^%%\s*rhythm-seen\s+(.*?)\s*%%\s*$")

# Ids are recognised ONLY at end-of-line on a card. Scanning the whole file
# would let a card whose prose happens to mention an id permanently suppress the
# real task with that id.
# ONE grammar for a card, so nothing can disagree about what is on the board:
#   1 = checkbox state, 2 = text, 3 = @project (optional), 4 = id
# The project sits BEFORE the id so the id keeps its end-of-line anchor, and
# the text group is non-greedy so it cannot swallow the project.
CARD = re.compile(
    r"^\s*- \[([ xX])\] (.*?)(?:\s+@([A-Za-z][\w-]*))?\s+\^(rk-[0-9a-f]{12})\s*$")

# A card typed straight onto the board by hand, with no id. sync cannot track
# it, but it is the user's text, so the renderer still shows it.
BARE = re.compile(r"^\s*- \[([ xX])\] (.*?)(?:\s+@([A-Za-z][\w-]*))?\s*$")


def _project_of(text, token):
    """(text, project) -- a trailing @token is a project ONLY if it resolves.

    Load-bearing. Without it `- [ ] ping @alice ^rk-...` silently files itself
    under a project called 'alice'. An unresolved token is prose, so it goes
    back into the text exactly as written."""
    if not token:
        return text, None
    prj = vocab.resolve(token, vocab.PROJECTS)
    if prj is None:
        return f"{text} @{token}", None
    return text, prj


def _line(text, project, tid):
    """One card line. The ONLY place a card is formatted."""
    tag = f" @{project}" if project else ""
    return f"- [ ] {text}{tag} ^{tid}"


def read():
    """-> (lines_without_seen_line, {column_lower: heading_index}, on_board, seen)"""
    try:
        raw = open(BOARD, encoding="utf-8", errors="replace").read().splitlines()
    except FileNotFoundError:
        raw = ["", "# Columns", ""]

    lines, seen = [], set()
    for line in raw:
        m = SEEN.match(line)
        if m:
            seen.update(m.group(1).split())
        else:
            lines.append(line)

    cols = {}
    on_board = set()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            cols.setdefault(line[3:].strip().lower(), i)
        m = CARD.match(line)
        if m:
            on_board.add(m.group(4))

    return lines, cols, on_board, seen


def write(lines, seen):
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ["", "%% rhythm-seen " + " ".join(sorted(seen)) + " %%"]
    os.makedirs(os.path.dirname(BOARD), exist_ok=True)
    atomic_write(BOARD, "\n".join(lines) + "\n")


def _insert(lines, head, cards):
    """Append cards to the bottom of the column whose heading is at `head`,
    preserving the file's heading / blank / cards / blank rhythm."""
    end = next((j for j in range(head + 1, len(lines))
                if lines[j].startswith("## ")), len(lines))
    block = lines[head + 1:end]
    while block and not block[-1].strip():
        block.pop()
    block += cards + [""]
    if block and block[0].strip():
        block.insert(0, "")
    lines[head + 1:end] = block


def sync(skip=None):
    """Fold every marker found in unsorted/ into the board. Returns how many
    cards were added."""
    import notes

    lines, cols, on_board, seen = read()
    want = notes.scan(skip)

    new = [(tid, act, prj, text) for tid, act, prj, text in want
           if tid not in on_board and tid not in seen]

    if not new:
        write(lines, seen | on_board)
        return 0

    # Every activity a new card needs must exist as a heading. notes.scan has
    # already mapped anything unrecognised to Backlog, so these are always one
    # of ACTIVITIES.
    for act in vocab.ACTIVITIES:
        if act.lower() in cols or not any(a == act for _, a, _, _ in new):
            continue
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "## " + act]
        cols[act.lower()] = len(lines) - 1

    by_col = {}
    for tid, act, prj, text in new:
        by_col.setdefault(act.lower(), []).append(_line(text, prj, tid))

    # Bottom-up: inserting into an earlier column shifts every later heading
    # index still held in `cols`.
    for col in sorted(by_col, key=lambda c: cols[c], reverse=True):
        _insert(lines, cols[col], by_col[col])

    write(lines, seen | on_board | {t for t, _, _, _ in new})
    return len(new)


def _span(lines, head):
    """The lines belonging to the column whose heading is at `head`."""
    end = next((j for j in range(head + 1, len(lines))
                if lines[j].startswith("## ")), len(lines))
    return lines[head + 1:end]


def _cards(lines, head):
    """[(text, done, id, project)] for one column. A hand-written card with no
    id is included with id None -- it is the user's text, even though sync
    cannot track it."""
    out = []
    for line in _span(lines, head):
        m = CARD.match(line)
        if m:
            text, prj = _project_of(m.group(2).strip(), m.group(3))
            out.append((text, m.group(1) != " ", m.group(4), prj))
            continue
        m = BARE.match(line)
        if m and m.group(2):
            text, prj = _project_of(m.group(2), m.group(3))
            out.append((text, m.group(1) != " ", None, prj))
    return out


def route_tasks():
    """Unchecked cards under ## Route, ids and checkbox stripped."""
    lines, cols, _, _ = read()
    head = cols.get("route")
    if head is None:
        return []
    return [t for t, done, _, _ in _cards(lines, head) if not done]


def _entry(text, done, tid, meta, tint=""):
    # `tint` names an ACTIVITY for the renderer to colour the card border with.
    # The policy of which board is coloured lives here, not in Lua: the
    # renderer just colours whatever arrives with a tint.
    return {"text": text, "meta": meta, "dim": done, "id": tid or "", "tint": tint}


def payload(view="activity"):
    """What the nvim renderer draws, for one of the two boards. A PURE READ --
    it must never call sync(), or merely opening the view would rewrite the
    board and trip its own directory watcher.

    Both boards are the SAME cards, grouped two ways: by their heading
    (activity), or by their @project token. `meta` always carries the other
    dimension, so each board shows what the grouping hides -- and so the
    project board never depends on colour alone, which matters for Backlog,
    whose whole point is that it has none."""
    lines, cols, _, _ = read()

    if view == "project":
        # Every card on the board, wherever it sits.
        by_prj = {}
        for act_key, head in cols.items():
            act = vocab.resolve(act_key, vocab.ACTIVITIES) or lines[head][3:].strip()
            for text, done, tid, prj in _cards(lines, head):
                by_prj.setdefault(prj or vocab.UNSORTED, []).append(
                    _entry(text, done, tid, act.lower(), act.lower()))
        titles = vocab.PROJECTS + [vocab.UNSORTED]
        # A project with no cards still gets its column -- an empty lane is
        # information, and the set of projects is fixed in vocab.
        columns = [{"title": t, "accent": i, "entries": by_prj.get(t, [])}
                   for i, t in enumerate(titles)]
        return {"title": "BOARD \u00b7 projects", "columns": columns}

    # activity: the four canonical columns in vocab order, then any other
    # heading that still holds cards, so a stray column can never hide work.
    columns, seen_heads = [], set()
    for i, act in enumerate(vocab.ACTIVITIES):
        head = cols.get(act.lower())
        entries = []
        if head is not None:
            seen_heads.add(head)
            entries = [_entry(t, done, tid, (prj or "").lower())
                       for t, done, tid, prj in _cards(lines, head)]
        columns.append({"title": act, "accent": i, "entries": entries})

    for head in sorted(set(cols.values()) - seen_heads):
        entries = [_entry(t, done, tid, (prj or "").lower())
                   for t, done, tid, prj in _cards(lines, head)]
        if entries:
            columns.append({"title": lines[head][3:].strip(),
                            "accent": len(columns), "entries": entries})

    return {"title": "BOARD \u00b7 activity", "columns": columns}


def migrate():
    """One-off: fold the old mixed headings into activity + @project.

    The board used to have one flat set of headings that mixed activities with
    projects, so a card could be filed under Sceptre OR Route, never both. Any
    heading that names a project becomes a @project token on its cards, which
    then move to Backlog; anything else unrecognised (the old 'Unknown') moves
    to Backlog untagged.

    Idempotent: a second run finds no non-activity headings and changes
    nothing. Returns (cards_moved, backup_path or None)."""
    lines, cols, on_board, seen = read()

    doomed = {}                       # heading index -> project or None
    for key, head in cols.items():
        if vocab.resolve(key, vocab.ACTIVITIES):
            continue
        doomed[head] = vocab.resolve(key, vocab.PROJECTS)
    if not doomed:
        return 0, None

    backup = BOARD + ".pre-split"
    atomic_write(backup, "\n".join(lines) + "\n")

    moved = []
    for head, prj in doomed.items():
        for text, done, tid, existing in _cards(lines, head):
            if tid is None:           # hand-written, no id: keep the raw line
                moved.append(f"- [{'x' if done else ' '}] {text}")
                continue
            line = _line(text, existing or prj, tid)
            moved.append(line.replace("- [ ]", "- [x]", 1) if done else line)

    # Drop the doomed headings and everything under them, bottom-up so the
    # indices still held in `doomed` stay valid.
    for head in sorted(doomed, reverse=True):
        end = next((j for j in range(head + 1, len(lines))
                    if lines[j].startswith("## ")), len(lines))
        del lines[head:end]

    # Rebuild a clean spine: the four activity columns, in vocab order.
    cols = {}
    for i, line in enumerate(lines):
        if line.startswith("## "):
            cols.setdefault(line[3:].strip().lower(), i)
    for act in vocab.ACTIVITIES:
        if act.lower() not in cols:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", "## " + act]
            cols[act.lower()] = len(lines) - 1

    if moved:
        _insert(lines, cols[vocab.DEFAULT_ACTIVITY.lower()], moved)

    # on_board still holds every id read BEFORE the move, so no card can fall
    # out of the seen-set and be resurrected as a duplicate by the next sync.
    write(lines, seen | on_board)
    return len(moved), backup


def remove(tid):
    """Delete a card from the board. Its id stays in the seen-set, and that is
    exactly what makes sync ignore the marker in the note from now on."""
    lines, _, on_board, seen = read()
    keep = [l for l in lines
            if not (CARD.match(l) and CARD.match(l).group(4) == tid)]
    if len(keep) == len(lines):
        return False
    # on_board was read BEFORE the removal, so it still holds tid.
    write(keep, seen | on_board)
    return True
