"""Note creation and marker-id stamping."""
import os
import re
import secrets
from datetime import date

from paths import UNSORTED, atomic_write

# A marker claims a line for the kanban board.
#
#   *> task              -> Unknown column
#   *Route> task         -> the Route column
#   - *Route> task       -> same, written inside a list bullet
#
# Whitespace after '>' is REQUIRED: without it `*ptr->field = 3;` in a pasted
# code snippet parses as column 'ptr-' / task 'field = 3;' and becomes a card.
# The text must start with a non-space, so `*Route>   ` captures nothing rather
# than minting an invisible card nobody can identify.
MARKER = re.compile(
    r"^\s*(?:[-*+]\s+)?"                 # optional list bullet
    r"\*([A-Za-z][\w -]*)?>"             # *>  or  *Column>
    r"\s+"                               # required
    r"(\S.*?)"                           # task text
    r"(?:\s*\^(rk-[0-9a-f]{12}))?"       # id, once stamped
    r"\s*$"
)


def mint():
    """48 bits of randomness. Ids are minted once and stamped into the note;
    they are never derived from the text or the filename, so renaming a note or
    rewording a marker does not fork the card."""
    return "rk-" + secrets.token_hex(6)


def new_note_path():
    """note_MM-DD-YY.md, then -1, -2, ... for further notes the same day.

    O_CREAT|O_EXCL makes the choice atomic, so two near-simultaneous presses
    cannot land on the same name."""
    os.makedirs(UNSORTED, exist_ok=True)
    stem = "note_" + date.today().strftime("%m-%d-%y")
    n = 0
    while True:
        name = f"{stem}.md" if n == 0 else f"{stem}-{n}.md"
        path = os.path.join(UNSORTED, name)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return path


def scan(skip=None):
    """Read every note, stamp un-stamped markers, return [(id, column, text)].

    `skip` is (abspath, lnum) of the line under the cursor when sync was called
    from the editor. That line is left alone entirely -- neither stamped nor
    collected -- so an id never lands mid-sentence in a line still being typed.
    """
    found = []
    if not os.path.isdir(UNSORTED):
        return found

    skip_path, skip_line = (skip or (None, None))
    if skip_path:
        skip_path = os.path.realpath(skip_path)

    for name in sorted(os.listdir(UNSORTED)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(UNSORTED, name)
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue

        skipping = skip_line if os.path.realpath(path) == skip_path else None
        dirty = False

        for i, line in enumerate(lines):
            if skipping is not None and i + 1 == skipping:
                continue
            m = MARKER.match(line)
            if not m:
                continue
            col, text, tid = m.group(1), m.group(2), m.group(3)
            if not tid:
                tid = mint()
                lines[i] = line.rstrip() + " ^" + tid
                dirty = True
            found.append((tid, (col or "unknown").strip().lower(), text))

        if dirty:
            atomic_write(path, "\n".join(lines) + "\n")

    return found
