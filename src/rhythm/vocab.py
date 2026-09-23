"""The two vocabularies a card is filed under: activity, and project.

These lists are the canonical SPELLINGS, the column ORDER, and the words the
corrector matches against -- all three. Adding a project is one word here.

A card has both: activity is its column on the board file, project is a token
on the card line. The activity board and the project board are two groupings of
that one set, which is why they can never disagree.
"""
import difflib
import re

ACTIVITIES = ["Active", "Monitor", "Route", "Backlog"]
PROJECTS = ["Sceptre", "Invictus", "GNC"]

UNSORTED = "Unsorted"            # the no-project column; not a real project
DEFAULT_ACTIVITY = "Backlog"     # where anything untriaged lands

# 0.6 accepts 'montiR'->Monitor (0.77) and 'scEptEr'->Sceptre (0.86) while
# rejecting 'meeting' (0.31). It is deliberately below the observed typo
# scores and well above the nearest unrelated word.
_CUTOFF = 0.6


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resolve(word, names):
    """The canonical spelling of `word` within `names`, or None.

    Case-insensitive, and tolerant of a typo: 'montiR' -> 'Monitor'. Matching
    is on the normalised form, so punctuation and case never matter."""
    w = _norm(word)
    if not w:
        return None
    by_norm = {_norm(n): n for n in names}
    if w in by_norm:
        return by_norm[w]
    m = difflib.get_close_matches(w, list(by_norm), n=1, cutoff=_CUTOFF)
    return by_norm[m[0]] if m else None


def classify(activity, project):
    """A marker's two raw slots -> (activity, project or None).

    Untriaged goes to Backlog. The back-compat branch matters: every existing
    note in the vault is the OLD one-slot form, '*Sceptre>', where the single
    slot held a project. Without it they would all land in Backlog with their
    project lost. It is unambiguous because no project name comes close to an
    activity name -- 'sceptre' scores 0.31 and 'gnc' 0.22 against the activity
    list, both far under the cutoff."""
    act = resolve(activity, ACTIVITIES)
    prj = resolve(project, PROJECTS)

    if act is None and prj is None and activity:
        prj = resolve(activity, PROJECTS)

    return (act or DEFAULT_ACTIVITY), prj
