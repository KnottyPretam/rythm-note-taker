"""Things waiting on you: Claude sessions blocked on input, and Slack unreads.

Pure readers -- no database, no network, no imports from the rest of the
package. radar.py owns the store and formats the ages; this file only answers
"what is waiting right now".

Neither source is polled: both are files the owning app already maintains.
"""
import glob
import json
import os

SESSIONS = os.path.expanduser("~/.claude/sessions/*.json")
SLACK_STATE = os.path.expanduser("~/.config/Slack/storage/root-state.json")


def _domain():
    """This machine and pid namespace, in the form Claude writes into pidDomain."""
    try:
        with open("/etc/machine-id") as f:
            return "linux:%s:%s" % (f.read().strip(), os.readlink("/proc/self/ns/pid"))
    except OSError:
        return None


def _live(s, domain):
    """True only when this file's pid is THIS machine's, and is still that process.

    Mandatory, not belt-and-braces. Nothing rewrites a session file except a
    status change -- one sat untouched for eight hours while its session was
    alive -- so a killed session leaves a file indistinguishable from a live one
    forever. The pid is the only liveness signal there is.
    """
    if not domain or s.get("pidDomain") != domain:
        return False                       # another machine, or another pid namespace
    pid = s.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        with open("/proc/%d/stat" % pid) as f:
            stat = f.read()
    except OSError:
        return False                       # gone
    # comm (field 2) is parenthesised and here is literally "(.claude-wrapped)",
    # so the only safe split is after the LAST ')'. That drops fields 1-2,
    # putting starttime at index 19 rather than 21. procStart is a JSON STRING;
    # compare as strings, or every session looks recycled and the lane goes
    # silently empty -- which reads as "nothing is waiting", the worst failure
    # this code can have.
    try:
        return stat[stat.rindex(")") + 2:].split()[19] == str(s.get("procStart"))
    except (ValueError, IndexError):
        return False


def claude_waiting():
    """[(key, label, why, ts_seconds, pane)] for sessions blocked on you.

    Only status == "waiting". `busy` is Claude working, `idle` is it sitting
    there having finished -- neither is waiting on YOU.
    """
    domain, out = _domain(), []
    for path in glob.glob(SESSIONS):
        try:
            with open(path) as f:
                s = json.load(f)
        except (OSError, ValueError):
            # These files are rewritten in place; a torn read is normal and the
            # view's own refetch fixes it.
            continue
        if s.get("status") != "waiting" or not _live(s, domain):
            continue
        # "Home:@2.%2" -> "%2". rpartition on '.', NOT split(':'): the Lua target
        # regex wants % plus digits, and handing it the window id @2 does not
        # error, it silently reports "nothing to jump to".
        pane = (s.get("tmux") or "").rpartition(".")[2]
        out.append(("claude:" + str(s.get("sessionId") or path),
                    s.get("name") or os.path.basename(s.get("cwd") or "") or "claude",
                    s.get("needs") or s.get("waitingFor") or "waiting",
                    int(s.get("statusUpdatedAt") or 0) // 1000,   # ms -> s
                    pane if pane.startswith("%") else ""))
    return out


def slack_unread():
    """[(key, label, why)] -- one per workspace that has anything unread.

    Per WORKSPACE only. Slack's Web API publishes no per-channel unread for
    channels at all (conversations.info: "included for DM conversations only",
    and channels.info was retired in 2021), so this file is the whole story.
    It is written by the running app, so with Slack closed it is a frozen
    snapshot.
    """
    try:
        with open(SLACK_STATE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for tid, team in (state.get("webapp", {}).get("teams") or {}).items():
        u = team.get("unreads") or {}
        n, hi = u.get("unreads") or 0, u.get("unreadHighlights") or 0
        if not (n or hi):
            continue
        name = (state.get("workspaces", {}).get(tid) or {}).get("domain") or tid
        out.append(("slack:" + tid, "Slack · " + name,
                    "%d unread%s" % (n, ", %d @you" % hi if hi else "")))
    return out
