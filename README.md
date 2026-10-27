# cursor-delegate: a Claude Code skill for handing coding tasks to Grok in Cursor

With this skill, Claude Code can delegate a coding task to Cursor's headless agent (default model: **Grok 4.7,
500K context, extra-high effort**). Each task runs in an isolated git worktree. Claude then reviews the diff, runs
the tests and applies, iterates on or discards the work. Several tasks can run in parallel.

Nothing Cursor writes reaches your working tree until Claude runs `apply`.

## What you need

| Requirement | Why |
|---|---|
| [Claude Code](https://claude.com/claude-code), logged in | Runs the skill |
| Python 3.10+ (`python` on PATH) | Runs `scripts/cursor_task.py`, which needs only the standard library |
| git | Each task gets its own worktree |
| Cursor, logged in to an account that has Grok 4.7 | The agent that does the work |

## Install

**Windows (PowerShell):**
```powershell
git clone <this repo's URL> cursor-delegate-skill
cd cursor-delegate-skill
powershell -ExecutionPolicy Bypass -File install.ps1
```

**macOS / Linux:**
```bash
git clone <this repo's URL> cursor-delegate-skill
cd cursor-delegate-skill
./install.sh
```

Both copy the `cursor-delegate/` folder to `~/.claude/skills/cursor-delegate/`. You can also copy it by hand.
Restart Claude Code afterwards.

### Make sure the Cursor agent can be found

The script looks, in this order, for:
1. the `CURSOR_AGENT_BIN` environment variable (path to the agent);
2. `cursor-agent` or `agent` on PATH;
3. on Windows, the standalone install in `%LOCALAPPDATA%\cursor-agent`, then the copy bundled with the Cursor app.

- **Windows with the Cursor app installed:** usually nothing to do.
- **macOS / Linux:** install the CLI: `curl https://cursor.com/install -fsS | bash`

### Log in and check

In Claude Code, or any terminal:
```bash
python ~/.claude/skills/cursor-delegate/scripts/cursor_task.py login    # opens a browser once
python ~/.claude/skills/cursor-delegate/scripts/cursor_task.py doctor   # shows version + "Logged in as …"
```
Instead of logging in, you can set `CURSOR_API_KEY`.

## Use it

Ask Claude in plain words, for example:
- "Have Grok fix the failing tests in this repo."
- "Use Cursor to build X, and run these three tasks in parallel."
- "Use fast mode for this one."

You can also type `/cursor-delegate`. The project must be a git repo with at least one commit; Claude asks before
creating one.

### Models
- **Default:** `grok-4.7[context=500k,reasoning_effort=xhigh,fast=false]` (normal speed).
- **Fast mode:** ask for it, or the script's `--fast` flag. In testing it was about 4× faster with the same
  result on well-specified tasks.
- **Change the default:** set `CURSOR_DELEGATE_MODEL`, for example if your Cursor account has no Grok 4.7.
  `python …/cursor_task.py models | head -40` lists the ids.

### Script commands (Claude runs these for you)

| Command | What it does |
|---|---|
| `run --repo DIR --prompt-file brief.md [--test CMD] [--fast] [--share .venv]` | Start a task in a new worktree |
| `followup RUN_ID --prompt-file feedback.md [--fast]` | Continue the same Cursor chat; `--fast` switches a running chat to fast mode |
| `apply RUN_ID` / `discard RUN_ID` | Apply the diff to your working tree, or throw it away |
| `list`, `show RUN_ID`, `doctor`, `login`, `models` | Housekeeping |

Runs and logs are kept in `~/.claude/cursor-runs/` (override with `CURSOR_RUNS_DIR`).

## Make it fit your machine

`SKILL.md` tells Claude to put each machine's quirks into the briefs it writes: which venv to use, environment
variables that break tools, and so on. If you hit a recurring quirk, add a line for it under "Environment quirks"
in `SKILL.md`.

## What we learned about using Grok

`cursor-delegate/assessment.md` has the results of a controlled test: 6 runs graded with hidden acceptance tests.
Short version:
- Keep briefs short for standard tasks.
- State the expected scale.
- Always say "you may implement a better design if you explain the trade-off". Without that sentence, Grok
  reports a design flaw and then builds it anyway.
- Prefer a quick run plus one targeted follow-up.
- Verify with your own tests before merging.
