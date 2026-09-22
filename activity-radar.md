# Activity Radar

## Goal

Create a lightweight **activity radar** that answers:

- What am I working on right now?
- What tasks did I touch today?
- What did I work on recently that I may want to return to?

The system should not try to measure productivity or exact time spent. Its purpose is to reconstruct the user's **current working set** across terminal work, conversations, and physical/offline activity.

---

## Core Principle

Track **activity evidence**, not just open applications or long-running sessions.

A tmux session can remain open for days without being actively used. A task should appear in the radar when it has been **touched recently**.

For tmux, the following information is sufficient to identify the activity:

- **tmux session name**
- **tmux pane name/title**
- **whether a Claude session/process is running in the pane**

This is intentionally simpler than tracking CWD, command history, elapsed time, window focus duration, or detailed process trees.

Example:

```text
Session: flight-sw
Pane: scheduler
Claude: running
```

That is enough context to infer:

```text
Working on the flight software scheduler with Claude.
```

---

## Activity Sources

### 1. tmux

Automatically record activity whenever the user switches to or interacts with a tmux pane.

Record:

```text
timestamp
session_name
pane_name
claude_active
```

Example:

```json
{
  "timestamp": "2026-09-22T11:20:31-07:00",
  "session_name": "flight-sw",
  "pane_name": "scheduler",
  "claude_active": true
}
```

The radar does not need to continuously sample every pane.

Useful triggers include:

- session change
- pane change
- attaching to tmux
- returning focus to a tmux client

A pane/session only needs a recent event to count as **touched today**.

---

### 2. Manual / Offline Activity

Some important activity happens away from the computer:

- talking to coworkers
- working in the lab
- inspecting hardware
- whiteboarding
- meetings
- walking somewhere to investigate a problem
- remembering something that needs follow-up

Provide a very low-friction global capture command, such as:

```text
Super + N
```

It should open a small input box:

```text
What are you doing / what just happened?

> Talked with John about HIL timing issue
```

Store only:

```text
timestamp
text
```

Classification can happen later.

---

## Activity States

The radar should organize activities by recency.

### NOW

The most recently touched activity.

Example:

```text
NOW
flight-sw / scheduler / Claude
```

### TOUCHED TODAY

Every distinct activity touched during the current day.

Example:

```text
TOUCHED TODAY

flight-sw / scheduler / Claude
gnss / septentrio / Claude
notes-app / architecture
Talked with John about HIL timing
Went to lab to inspect GPS simulator
```

### RECENT

Activities touched during the last several days but not today.

Example:

```text
RECENT

1 day   imu-calibration / analysis
2 days  monte-carlo / runner
4 days  nixos / hyprland
```

The exact age thresholds are not important initially.

---

## tmux Activity Identity

Treat the combination of:

```text
session_name + pane_name
```

as the logical tmux activity.

Claude status is additional context:

```text
session_name + pane_name + claude_active
```

Example:

```text
gnss / jammer-analysis / Claude
```

versus:

```text
gnss / jammer-analysis
```

There is no need initially to track:

- tmux window name
- pane ID
- working directory
- foreground command
- exact time spent
- shell command history
- process CPU usage

These can be added later only if they become useful.

---

## Detecting Claude

For each active/touched pane, determine whether a Claude process is associated with the pane.

Possible implementation:

```bash
tmux display-message -p '#{pane_pid}'
```

Then inspect the pane process tree for a Claude/Claude Code process.

Conceptually:

```text
pane PID
   |
   +-- shell
        |
        +-- claude
```

Store this as a boolean:

```text
claude_active = true
```

The implementation should tolerate Claude running underneath a shell, wrapper, or child process rather than relying only on `pane_current_command`.

---

## Event Database

SQLite is sufficient.

### tmux_events

```sql
CREATE TABLE tmux_events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    session_name TEXT NOT NULL,
    pane_name TEXT NOT NULL,
    claude_active INTEGER NOT NULL DEFAULT 0
);
```

### manual_events

```sql
CREATE TABLE manual_events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    text TEXT NOT NULL
);
```

The event log is the source of truth.

The dashboard can derive current activity and recent activity from these events.

---

## `radar` Command

The primary interface should be one command:

```bash
radar
```

Example output:

```text
ACTIVITY RADAR
────────────────────────────────────────

NOW

● flight-sw / scheduler / Claude
  8 min ago


TOUCHED TODAY

● flight-sw / scheduler / Claude
  8 min ago

● gnss / septentrio / Claude
  46 min ago

● notes-app / architecture
  1h 20m ago

● Talked with John about HIL timing
  2h ago

● Went to lab to inspect GPS simulator
  3h ago


RECENT

○ imu-calibration / analysis
  yesterday

○ monte-carlo / runner
  2 days ago

○ nixos / hyprland
  4 days ago
```

---

## Interactive Radar

Eventually, make `radar` interactive using something like:

- `fzf`
- `rofi`
- a small TUI

For tmux entries, selecting an activity should jump directly to the corresponding session/pane when possible.

Example:

```text
flight-sw / scheduler / Claude
```

Selecting it should switch into the appropriate tmux context.

Manual/offline entries only need to display their notes.

---

## Hyprland Integration

Useful global bindings:

```text
Super + A  -> open Activity Radar
Super + N  -> capture manual/offline activity
```

Possible flow:

```text
Super+A
   |
   v
rofi/fzf/TUI
   |
   +-- flight-sw / scheduler / Claude
   +-- gnss / septentrio / Claude
   +-- Talked with John about HIL timing
   +-- Went to lab
```

This makes the radar function as an external working-memory system.

---

## Design Priorities

1. **Very low friction**
   - Activity detection should mostly be automatic.
   - Manual capture should require only one sentence.

2. **Recency over duration**
   - Knowing that a task was touched today is more valuable than knowing it was active for 37 minutes.

3. **Minimal tmux metadata**
   - Session name
   - Pane name
   - Claude active/not active

4. **Do not equate open with active**
   - Long-running tmux sessions should not appear as today's work unless touched.

5. **Fast retrieval**
   - `Super+A` or `radar` should immediately show the current working set.

6. **Offline activity matters**
   - Conversations and physical work belong in the same activity history as terminal work.

---

## Minimal Viable Version

The first version only needs:

```text
tmux hook/event
    ↓
session name
pane name
Claude status
timestamp
    ↓
SQLite
    ↓
radar
```

plus:

```text
Super+N
    ↓
manual text note
    ↓
SQLite
```

The output only needs three sections:

```text
NOW
TOUCHED TODAY
RECENT
```

Avoid adding task managers, LLM classification, detailed time tracking, or complex project metadata until this basic workflow proves useful.

---

## Future Extensions

Only after the minimal version is working well:

- automatically infer project/task names
- connect entries to Obsidian notes
- generate daily activity notes
- generate a Kanban board from repeatedly touched tasks
- reminders for activities that have gone stale
- summarize conversations/manual notes
- recognize Git repositories
- correlate browser activity with tmux activity
- restore tmux sessions directly from the radar
- add an LLM-generated daily summary

The core system should remain usable even if all of these extensions are disabled.
