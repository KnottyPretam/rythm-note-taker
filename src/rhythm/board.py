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

from paths import BOARD, atomic_write

SEEN = re.compile(r"^%%\s*rhythm-seen\s+(.*?)\s*%%\s*$")

# Ids are recognised ONLY at end-of-line on a card. Scanning the whole file
# would let a card whose prose happens to mention an id permanently suppress the
# real task with that id.
# ONE grammar for a card, so nothing can disagree about what is on the board:
#   group 1 = checkbox state, group 2 = text, group 3 = id
CARD = re.compile(r"^\s*- \[([ xX])\] (.*)\s\^(rk-[0-9a-f]{12})\s*$")

# A card typed straight onto the board by hand, with no id. sync cannot track
# it, but it is the user's text, so the renderer still shows it.
BARE = re.compile(r"^\s*- \[([ xX])\] (.*?)\s*$")


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
            on_board.add(m.group(3))

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

    new = []
    for tid, col, text in want:
        if tid in on_board or tid in seen:
            continue
        if col not in cols and col != "unknown":
            print(f"rhythm: unknown column {col!r} -> Unknown", flush=True)
            col = "unknown"
        new.append((tid, col, text))

    if not new:
        write(lines, seen | on_board)
        return 0

    if any(c == "unknown" for _, c, _ in new) and "unknown" not in cols:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "## Unknown"]
        cols["unknown"] = len(lines) - 1

    by_col = {}
    for tid, col, text in new:
        by_col.setdefault(col, []).append(f"- [ ] {text} ^{tid}")

    # Bottom-up: inserting into an earlier column shifts every later heading
    # index still held in `cols`.
    for col in sorted(by_col, key=lambda c: cols[c], reverse=True):
        _insert(lines, cols[col], by_col[col])

    write(lines, seen | on_board | {t for t, _, _ in new})
    return len(new)


def _span(lines, head):
    """The lines belonging to the column whose heading is at `head`."""
    end = next((j for j in range(head + 1, len(lines))
                if lines[j].startswith("## ")), len(lines))
    return lines[head + 1:end]


def _cards(lines, head):
    """[(text, done, id)] for one column. A hand-written card with no id is
    included with id None -- it is the user's text, even though sync cannot
    track it."""
    out = []
    for line in _span(lines, head):
        m = CARD.match(line)
        if m:
            out.append((m.group(2).strip(), m.group(1) != " ", m.group(3)))
            continue
        m = BARE.match(line)
        if m and m.group(2):
            out.append((m.group(2), m.group(1) != " ", None))
    return out


def route_tasks():
    """Unchecked cards under ## Route, ids and checkbox stripped."""
    lines, cols, _, _ = read()
    head = cols.get("route")
    if head is None:
        return []
    return [t for t, done, _ in _cards(lines, head) if not done]


def payload():
    """What the nvim renderer draws. A PURE READ -- it must never call sync(),
    or merely opening the view would rewrite the board and trip its own
    directory watcher."""
    lines, cols, _, _ = read()
    columns = []
    for slot, head in enumerate(sorted(cols.values())):
        columns.append({
            # Display case survives only on the heading line; `cols` is keyed
            # lowercase.
            "title": lines[head][3:].strip(),
            # A categorical SLOT, not a colour. Lua owns the palette, so the
            # radar can reuse this field for a different ramp.
            "accent": slot,
            "entries": [{"text": t, "meta": "", "dim": done, "id": tid or ""}
                        for t, done, tid in _cards(lines, head)],
        })
    name = os.path.basename(BOARD).removesuffix(".md").removesuffix("_kanban")
    return {"title": "BOARD \u00b7 " + name, "columns": columns}


def remove(tid):
    """Delete a card from the board. Its id stays in the seen-set, and that is
    exactly what makes sync ignore the marker in the note from now on."""
    lines, _, on_board, seen = read()
    keep = [l for l in lines
            if not (CARD.match(l) and CARD.match(l).group(3) == tid)]
    if len(keep) == len(lines):
        return False
    # on_board was read BEFORE the removal, so it still holds tid.
    write(keep, seen | on_board)
    return True
