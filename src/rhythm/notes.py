"""Note creation and marker-id stamping."""
import os
import re
import secrets
from datetime import date

import vocab
from paths import UNSORTED, atomic_write

# A marker claims a line for the kanban board. It carries BOTH dimensions:
#
#   *> task                  -> Backlog, no project
#   *Active> task            -> the Active column
#   *Active/Sceptre> task    -> Active, project Sceptre
#   */Sceptre> task          -> Backlog, project Sceptre
#   - *Route/GNC> task       -> same, written inside a list bullet
#
# Either slot may be miscased or misspelled; vocab.classify corrects it, and
# the corrected spelling is written back into the note when the id is stamped.
#
# Whitespace after '>' is REQUIRED: without it `*ptr->field = 3;` in a pasted
# code snippet parses as column 'ptr-' / task 'field = 3;' and becomes a card.
# Adding the '/' slot does not widen that hole -- a URL like
# `*see https://x.com> foo` still fails, because ':' is in neither charset.
# The text must start with a non-space, so `*Route>   ` captures nothing rather
# than minting an invisible card nobody can identify.
MARKER = re.compile(
    r"^(\s*(?:[-*+]\s+)?)"               # 1 indent and optional list bullet
    r"\*([A-Za-z][\w -]*)?"              # 2 activity  (absent in `*>`)
    r"(?:/([A-Za-z][\w -]*)?)?>"         # 3 project   (absent in `*Active>`)
    r"\s+"                               # required
    r"(\S.*?)"                           # 4 task text
    r"(?:\s*\^(rk-[0-9a-f]{12}))?"       # 5 id, once stamped
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


def _canonical(lead, activity, project, text, tid):
    """Rewrite a marker with the corrected spelling of whatever the user typed.

    The SHAPE is preserved, only spelling is fixed: `*scEptEr>` becomes
    `*Sceptre>`, not `*Backlog/Sceptre>`. Rewriting a one-slot marker into the
    two-slot form would be a bigger edit to someone's note than fixing a typo,
    and the card is filed identically either way. A slot that resolves to
    nothing is left exactly as typed."""
    def fix(slot, names):
        return (vocab.resolve(slot, names) or slot) if slot else slot

    act = fix(activity, vocab.ACTIVITIES + vocab.PROJECTS) if activity else None
    prj = fix(project, vocab.PROJECTS) if project else None

    head = "*" + (act or "")
    if project is not None:
        head += "/" + (prj or "")
    return f"{lead}{head}> {text} ^{tid}"


def scan(skip=None):
    """Read every note, stamp un-stamped markers, return
    [(id, activity, project, text)].

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
            lead, act, prj, text, tid = m.groups()
            if not tid:
                # Stamping and spelling-correction are ONE write: a second pass
                # over the file would be a second chance to lose it.
                tid = mint()
                lines[i] = _canonical(lead, act, prj, text, tid)
                dirty = True
            activity, project = vocab.classify(act, prj)
            found.append((tid, activity, project, text))

        if dirty:
            atomic_write(path, "\n".join(lines) + "\n")

    return found
