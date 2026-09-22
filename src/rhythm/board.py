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
CARD = re.compile(r"^\s*- \[[ xX]\] .*\s\^(rk-[0-9a-f]{12})\s*$")
OPEN_CARD = re.compile(r"^\s*- \[ \] (.*?)\s*\^rk-[0-9a-f]{12}\s*$")


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
            on_board.add(m.group(1))

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


def route_tasks():
    """Unchecked cards under ## Route, ids and checkbox stripped."""
    lines, cols, _, _ = read()
    head = cols.get("route")
    if head is None:
        return []
    end = next((j for j in range(head + 1, len(lines))
                if lines[j].startswith("## ")), len(lines))
    out = []
    for line in lines[head + 1:end]:
        m = OPEN_CARD.match(line)
        if m:
            out.append(m.group(1))
    return out
