"""The activity radar: a log of tmux focus events, and the three lanes the
overlay draws from it.

Imports nothing else from this package, deliberately. It runs from a tmux hook,
in a server whose environment has no RHYTHM_VAULT, and `import paths` calls
sys.exit at import time when neither vault variable is set. The log is machine
state rather than notes, so it lives under XDG_STATE_HOME, not in the vault.
"""
import os
import sqlite3
import subprocess
import time
from datetime import date, timedelta

DB = os.path.join(os.environ.get("XDG_STATE_HOME",
                                 os.path.expanduser("~/.local/state")),
                  "rhythm", "radar.db")

# ONE tmux call answers everything a row needs. list-panes -t <pane> lists
# every pane of the window CONTAINING that pane, which is what lets the label
# say "Claude" whenever Claude runs anywhere in the window rather than only
# when its own pane happens to be focused -- otherwise switching to the shell
# pane of a Claude window silently drops the marker.
FMT = ("#{session_id}\t#{session_name}\t#{window_id}\t#{window_name}"
       "\t#{pane_current_path}\t#{pane_title}\t#{pane_current_command}"
       "\t#{socket_path}\t#{pane_id}")

DAYS = 7                     # how far back RECENT reaches
CLAUDE = "✳"            # Claude Code's own pane-title sentinel
RESTORE_GRACE = 15           # seconds; see tick()

# A window still carrying its shell's name was never named by hand, so the
# name says nothing the folder does not say better.
UNNAMED = {"bash", "zsh", "sh", "fish", "dash", "tmux"}

NOW_SQL = "SELECT key, label, target, ts FROM event ORDER BY ts DESC, id DESC LIMIT 1"

# row_number(), not `max(ts) ... GROUP BY key`: SQLite guarantees the bare
# columns only when a query holds EXACTLY one min/max aggregate, and gives no
# tie-break when two rows share the maximum -- which one-second resolution
# makes ordinary. ORDER BY ts DESC, id DESC is deterministic.
TODAY_SQL = """
SELECT key, label, target, ts FROM (
  SELECT key, label, target, ts,
         row_number() OVER (PARTITION BY key ORDER BY ts DESC, id DESC) rn
  FROM event WHERE ts >= ?)
WHERE rn = 1 ORDER BY ts DESC"""

# `key NOT IN (touched today)` is what makes RECENT mean "and NOT today":
# without it an activity touched yesterday and again today lists in both lanes.
RECENT_SQL = """
SELECT key, label, target, ts FROM (
  SELECT key, label, target, ts,
         row_number() OVER (PARTITION BY key ORDER BY ts DESC, id DESC) rn
  FROM event WHERE ts >= ? AND ts < ?)
WHERE rn = 1 AND key NOT IN (SELECT key FROM event WHERE ts >= ?)
ORDER BY ts DESC"""


def _open():
    """The log, created on first use -- which is what lets the tmux hook run on
    a machine where ~/.local/state/rhythm does not exist yet.

    journal_mode is issued ONLY for a new file: it is persistent in the header,
    and re-asserting it on every hook grabs an exclusive lock on the hot path.
    busy_timeout is per-connection and must be set every time.

    isolation_level=None because the legacy default issues an implicit DEFERRED
    BEGIN, and a deferred read-then-write upgrade fails instantly with
    SQLITE_BUSY_SNAPSHOT -- which the busy handler is never invoked for.
    """
    first = not os.path.exists(DB)
    if first:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, isolation_level=None)
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA synchronous = NORMAL")
    if first:
        # WAL is what stops the overlay's read blocking behind a burst of hooks.
        con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE IF NOT EXISTS event ("
                "id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, key TEXT NOT NULL,"
                " label TEXT NOT NULL, target TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS event_ts ON event(ts)")
    return con


def _label(sname, wname, cwd, claude):
    """What a card SHOWS. Cosmetic: never compared, never a key, may be stale.

    The window name leads when you have curated one. When it is still the
    shell's own name it says nothing, so the folder speaks instead -- which is
    what keeps two windows both called "bash" from becoming one indistinct row.
    """
    what = (wname or "").strip()
    if not what or what.lower() in UNNAMED:
        what = os.path.basename((cwd or "").rstrip("/")) or what or "shell"
    if claude:
        what += " · Claude"
    return "%s / %s" % (sname, what) if sname else what


def tick(pane, start_time):
    """One focus event. Called ONLY by the tmux pane-focus-in hook."""
    if time.time() - start_time < RESTORE_GRACE:
        # The server just came up. A continuum restore replays into an already
        # attached client and fires a burst of pane-focus-in in the first
        # seconds; without this every restored window would land in TOUCHED
        # TODAY at the same instant, on every reboot.
        return
    # The pane id comes from argv, never from $TMUX_PANE: run-shell does export
    # $TMUX_PANE, but it is run-shell's own default target and stays STALE
    # across consecutive fires for different panes.
    out = subprocess.run(["tmux", "list-panes", "-t", pane, "-F", FMT],
                         capture_output=True, text=True).stdout
    rows = [r.split("\t") for r in out.splitlines() if r]
    if not rows:
        # The pane died between the focus and this call. There is no error to
        # check: a dead target prints nothing and still exits 0.
        return
    # Our own row carries the folder; fall back to the first if it vanished.
    mine = next((r for r in rows if len(r) > 8 and r[8] == pane), rows[0])
    sid, sname, wid, wname, cwd, _title, _cmd, sock = (mine + [""] * 8)[:8]
    if not (sid and wid):
        return
    claude = any((r[5] or "").startswith(CLAUDE) or r[6] == "claude"
                 for r in rows if len(r) > 6)
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO event(ts, key, label, target) VALUES(?,?,?,?)",
                (int(time.time()), sid + ":" + wid,
                 _label(sname, wname, cwd, claude),
                 # socket_path, not a bare %N: pane ids restart at %0 on every
                 # new server, so a stored id would silently resolve to a
                 # DIFFERENT pane after a restart.
                 "%s|%d|%s" % (sock, start_time, pane)))
    con.execute("COMMIT")
    con.close()


def note(text):
    """A manual, offline activity: an event with words and no pane."""
    text = " ".join(text.split())
    if not text:
        return
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    # The key carries the row id, so two notes with the same words stay two
    # activities and `x` forgets exactly one. Both statements are inside the
    # one transaction, so the empty key is never visible to a reader.
    rid = con.execute("INSERT INTO event(ts, key, label, target)"
                      " VALUES(?, '', ?, NULL)",
                      (int(time.time()), text)).lastrowid
    con.execute("UPDATE event SET key = 'note:' || id WHERE id = ?", (rid,))
    con.execute("COMMIT")
    con.close()


def forget(key):
    """Drop an activity from the radar.

    A tmux window returns the moment you focus it again, and that is the right
    semantic: going back to a window genuinely puts it back in the working set.
    Nothing writes a note: key except SUPER+I, and that always allocates a
    fresh row, so a forgotten note is gone for good.
    """
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    con.execute("DELETE FROM event WHERE key = ?", (key,))
    con.execute("COMMIT")
    con.close()


def _midnight(days_ago=0):
    """Epoch seconds of 00:00 local, `days_ago` CALENDAR days back.

    tm_isdst=-1 makes libc resolve the offset from /etc/localtime for THAT
    date. Never midnight - N*86400 (wrong by an hour either side of a DST
    change) and never .replace(hour=0) on an aware datetime (that snapshots
    today's offset). TZ stays unset: on NixOS there is no /usr/share/zoneinfo,
    so TZ=America/Phoenix silently means UTC.
    """
    d = date.today() - timedelta(days=days_ago)
    return int(time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)))


def _age(ts, now):
    """Under an hour, elapsed minutes beat the calendar, so something touched
    at 23:50 and looked at at 00:10 reads "20 min ago" rather than "yesterday".
    Past that, the calendar day is the unit people actually think in.
    """
    delta = max(0, now - ts)            # clock skew must never print a negative
    if delta < 60:
        return "just now"
    if delta < 3600:
        return "%d min ago" % (delta // 60)
    days = (date.fromtimestamp(now) - date.fromtimestamp(ts)).days
    if days <= 0:
        h, m = delta // 3600, (delta % 3600) // 60
        return "%dh %dm ago" % (h, m) if m else "%dh ago" % h
    return "yesterday" if days == 1 else "%d days ago" % days


def _entry(row, now):
    key, label, target, ts = row
    # `tint` is the one cosmetic call here: a dim border says "nothing behind
    # this one", which is the only way Enter behaves differently.
    return {"text": label, "meta": _age(ts, now), "dim": False,
            "id": key, "tint": "" if target else "note", "target": target or ""}


def payload():
    """The SAME envelope the boards emit, so one renderer draws all three
    views. A pure read of the log."""
    now = int(time.time())
    today, back = _midnight(), _midnight(DAYS)
    con = _open()
    head = con.execute(NOW_SQL).fetchall()
    touched = con.execute(TODAY_SQL, (today,)).fetchall()
    recent = con.execute(RECENT_SQL, (back, today, today)).fetchall()
    con.close()
    # entry.id is the SELECTION key and must be unique across the WHOLE
    # payload: compose() borders every card whose id matches the selection,
    # with no column filter, so one activity in two lanes lights up twice.
    # NOW therefore OWNS its activity and the other lanes drop it.
    top = head[0][0] if head else None
    lanes = [("Now", 4, head),
             ("Touched today", 5, [r for r in touched if r[0] != top]),
             ("Recent", 6, [r for r in recent if r[0] != top])]
    return {"title": "RADAR", "columns":
            [{"title": t, "accent": a, "entries": [_entry(r, now) for r in rows]}
             for t, a, rows in lanes]}
