"""The activity radar: a log of tmux focus events, and the three lanes the
overlay draws from it.

Imports nothing else from this package, deliberately. It runs from a tmux hook,
in a server whose environment has no RHYTHM_VAULT, and `import paths` calls
sys.exit at import time when neither vault variable is set. The log is machine
state rather than notes, so it lives under XDG_STATE_HOME, not in the vault.
"""
import os
import re
import sqlite3
import subprocess
import time
from datetime import date

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

CLAUDE = "✳"            # Claude Code's own pane-title sentinel
RESTORE_GRACE = 15           # seconds; see tick()

# A window still carrying its shell's name was never named by hand, so the
# name says nothing the folder does not say better.
UNNAMED = {"bash", "zsh", "sh", "fish", "dash", "tmux"}

# Now and Touched today are ONE query split by age, and they show exactly the
# same kinds of thing -- prompts, windows, panes, notes. The only difference
# between the lanes is when.
#
# A rolling window rather than a calendar day: at 00:30 what you were doing at
# 23:00 is still what you are doing, and a midnight boundary would empty the
# radar mid-thought. 16 hours is a waking day, so what you touched this morning
# is still there tonight and yesterday morning is not.
NOW_WINDOW = 2 * 3600
TODAY_WINDOW = 16 * 3600

# row_number(), not `max(ts) ... GROUP BY key`: SQLite guarantees the bare
# columns only when a query holds EXACTLY one min/max aggregate, and gives no
# tie-break when two rows share the maximum -- which one-second resolution
# makes ordinary. ORDER BY ts DESC, id DESC is deterministic.
RECENT_SQL = """
SELECT key, label, target, ts FROM (
  SELECT key, label, target, ts,
         row_number() OVER (PARTITION BY key ORDER BY ts DESC, id DESC) rn
  FROM event WHERE ts >= ?)
WHERE rn = 1 ORDER BY ts DESC"""

NOTICE_SQL = ("SELECT key, label, meta, ts FROM notice WHERE ts >= ?"
              " ORDER BY ts DESC")

# socket_path and the server's start_time are server-WIDE constants and every
# focus row already carries them, so the log itself answers "where is tmux".
# That matters: the nix wrapper leaves tmux out of runtimeInputs on purpose, and
# a bare `tmux` with no TMUX_TMPDIR finds the DEAD socket at /tmp/tmux-1000.
# A note's target is NULL, and `NULL <> ''` is NULL rather than true, so notes
# drop out of this on their own.
# `LIKE '/%'`, not `<> ''`: window-focus rows carry a `win|<address>` target,
# which is not a tmux server at all. A tmux target always opens with the
# absolute socket path. A note's target is NULL, and NULL LIKE anything is
# NULL rather than true, so notes still drop out on their own.
SERVER_SQL = ("SELECT target FROM event WHERE target LIKE '/%'"
              " ORDER BY ts DESC, id DESC LIMIT 1")

# The newest window-focus row per label, for linking a tmux-less Claude
# session to the terminal window whose title carries its name.
WINDOWS_SQL = """
SELECT label, target FROM (
  SELECT key, label, target, row_number() OVER
         (PARTITION BY key ORDER BY ts DESC, id DESC) rn
  FROM event WHERE key LIKE 'win:%')
WHERE rn = 1"""

# "[gov-tiberius] in Lucas Pick, Ryan Morris" -> ("gov-tiberius", "Lucas ...")
MAKO = re.compile(r"^\[([^\]]+)\]\s+(?:in|from)\s+(.+)$")


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
    # Notifications captured from mako as they arrive. Separate from `event`
    # because it is a different question -- "what pinged me" rather than "what
    # was I doing" -- and because the lane queries are unrelated. `key` is
    # UNIQUE so a conversation that pings ten times is one card with the latest
    # time, not ten cards.
    con.execute("CREATE TABLE IF NOT EXISTS notice ("
                "id INTEGER PRIMARY KEY, ts INTEGER NOT NULL,"
                " key TEXT NOT NULL UNIQUE, label TEXT NOT NULL, meta TEXT)")
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
    if key.startswith("prompt:"):
        # A prompt is not an event, so the deletes below would silently match
        # nothing and `x` would appear to do nothing at all.
        import prompts
        con = prompts._open()
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM prompt WHERE pid = ?", (key[7:],))
        con.execute("COMMIT")
        con.close()
        return
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    con.execute("DELETE FROM event WHERE key = ?", (key,))
    con.execute("DELETE FROM notice WHERE key = ?", (key,))
    con.execute("COMMIT")
    con.close()


def notice_tick(mako_id):
    """Record a notification mako is showing. Called from its on-notify hook.

    mako's own history is useless for this: it keeps five EXPIRED notifications,
    in memory, with no timestamps. `exec` hands us only the id (there are no
    format specifiers in an action), so the summary is read back out of
    `makoctl list` while the notification is still on screen.
    """
    out = subprocess.run(["makoctl", "list"], capture_output=True, text=True).stdout
    want = "Notification %s: " % mako_id
    summary = next((l[len(want):].strip() for l in out.splitlines()
                    if l.startswith(want)), "")
    if not summary:
        return
    m = MAKO.match(summary)
    where, label = (m.group(1), m.group(2)) if m else ("", summary)
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    # Keyed on the conversation, so ten pings from one thread stay one card.
    con.execute("INSERT INTO notice(ts, key, label, meta) VALUES(?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET ts = excluded.ts",
                (int(time.time()), "mako:" + label, label, where))
    con.execute("COMMIT")
    con.close()


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


def _clip(text, n=72):
    """A prompt is not a card title. compose() wraps `text` over as many lines
    as it needs, so an unclipped paragraph would be a twenty-line card."""
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "\u2026"


def _entry(row, now):
    key, label, target, ts = row
    target = target or ""
    # `tint` is the one cosmetic call here: a dim border says "nothing behind
    # this one", which is the only way Enter behaves differently.
    tint = "win" if target.startswith("win|") else ("" if target else "note")
    return {"text": label, "meta": _age(ts, now), "dim": False,
            "id": key, "tint": tint, "target": target}


def _prompt_rows(server, now):
    """(unviewed, sent within NOW_WINDOW, sent between the two windows).

    The `prompt` table lives in the same database but is OWNED by prompts.py,
    which creates it -- so this opens its own connection rather than teaching
    _open() about a table the tmux hot path never touches. Called after
    _open(), so `event` already exists for the unviewed query to join against.
    """
    import prompts                     # lazy: the tmux hot path never needs it
    con = prompts._open()
    try:
        windows = con.execute(WINDOWS_SQL).fetchall()
        un = prompts.unviewed(con, now)
        td = prompts.sent_since(con, now - TODAY_WINDOW)
    finally:
        con.close()

    def card(pid, text, agent, ts, win, tint):
        # A prompt's window id joins the focus log, but jumping needs the live
        # server, which only the focus log knows. A session with no tmux field
        # falls back to the Hyprland window whose title carries its name --
        # the same link the viewed check uses, so the two cannot disagree.
        if win:
            target = "%s|%s|%s" % (server[0], server[1], win) if server else ""
        else:
            target = next((t for lbl, t in windows if agent and agent in lbl), "")
        return {"text": '"%s"' % _clip(text), "dim": False,
                "meta": "%s \u00b7 %s" % (agent, _age(ts, now)),
                "id": "prompt:" + pid, "tint": tint, "target": target}

    unviewed = [card(pid, text, agent, ts, win, "claude")
                for pid, text, agent, ts, win in un]
    # Notifications OWNS an unviewed prompt and Touched today drops it, the
    # same rule NOW already follows -- entry.id is the selection key, and one
    # id in two lanes lights both up. Looking at the pane therefore MOVES the
    # card from Notifications down into Touched today rather than deleting it.
    hot = {r[0] for r in un}
    edge = now - NOW_WINDOW
    sent = [(ts, card(pid, text, agent, ts, win, ""))
            for pid, text, agent, ts, win in td if pid not in hot]
    return (unviewed,
            [c for ts, c in sent if ts >= edge],
            [c for ts, c in sent if ts < edge])


def _server(con):
    """(socket, server_start) for the live tmux server, from the log itself."""
    row = con.execute(SERVER_SQL).fetchone()
    if not row:
        return None
    sock, _, rest = row[0].partition("|")
    return sock, rest.partition("|")[0]


def _waiting(con, now):
    """The Notifications lane: things that have not been got to.

    Three sources, none of them polled -- each is a file its owning app already
    maintains. A source that is missing or unreadable contributes nothing
    rather than failing the lane.
    """
    import notify                      # lazy: the tmux hot path never needs it
    server = _server(con)
    unviewed, fresh, older = _prompt_rows(server, now)
    out = list(unviewed)
    for key, label, why, ts, pane in notify.claude_waiting():
        # Only a pane we can actually reach gets a target. Enter on a target-less
        # card says so rather than jumping somewhere wrong.
        target = "%s|%s|%s" % (server[0], server[1], pane) if server and pane else ""
        out.append({"text": label, "meta": _age(ts, now) if ts else why,
                    "dim": False, "id": key, "tint": "claude", "target": target})
    for key, label, why in notify.slack_unread():
        out.append({"text": label, "meta": why, "dim": False,
                    "id": key, "tint": "slack", "target": ""})
    for key, label, meta, ts in con.execute(NOTICE_SQL, (now - 7 * 86400,)):
        out.append({"text": label, "meta": _age(ts, now), "dim": False,
                    "id": key, "tint": "slack", "target": ""})
    return out, fresh, older


def _lane(rows, sent, now):
    """One lane. What you ASKED comes first, then the windows the tmux hook
    structurally cannot see -- the chat agents and the ssh terminal -- then
    ordinary tmux windows and notes underneath.

    Both lanes are built by this, so Now and Touched today can never disagree
    about what counts as an activity or how it is ordered.
    """
    win = [_entry(r, now) for r in rows if r[0].startswith("win:")]
    panes = [_entry(r, now) for r in rows if not r[0].startswith("win:")]
    return sent + win + panes


def payload():
    """The SAME envelope the boards emit, so one renderer draws all three
    views. A pure read."""
    now = int(time.time())
    con = _open()
    recent = con.execute(RECENT_SQL, (now - TODAY_WINDOW,)).fetchall()
    waiting, fresh, older = _waiting(con, now)
    con.close()
    # One row per activity, at its LATEST touch -- so an activity falls in
    # exactly one lane and entry.id stays unique across the whole payload,
    # which compose() requires: it borders every card matching the selection,
    # with no column filter, so one id in two lanes lights up twice. The
    # notifications lane is namespaced (prompt:/claude:/slack:/mako:) so it
    # cannot collide either.
    edge = now - NOW_WINDOW
    return {"title": "RADAR", "columns": [
        {"title": "Now", "accent": 4,
         "entries": _lane([r for r in recent if r[3] >= edge], fresh, now)},
        {"title": "Touched today", "accent": 5,
         "entries": _lane([r for r in recent if r[3] < edge], older, now)},
        {"title": "Notifications", "accent": 6, "entries": waiting},
    ]}
