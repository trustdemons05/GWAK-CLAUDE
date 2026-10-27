---
name: cursor-delegate
description: Delegate coding tasks to the Cursor Agent CLI (Cursor's headless agent) installed on this machine. Each task runs in an isolated git worktree; Claude then reviews Cursor's diff and test results and applies, iterates on, or discards the work. Use this whenever the user asks to use Cursor, hand off / offload / outsource work to Cursor, "have Cursor do X", use their Cursor plan, credits or Cursor models, get a second agent's attempt to compare, or run several independent coding tasks in parallel, even if they only say "cursor" or "cursor-agent".
---

# Delegating work to Cursor

Everything goes through one script. Call it with `python`; every command prints JSON.

```
SCRIPT=~/.claude/skills/cursor-delegate/scripts/cursor_task.py
```

Cursor never edits the user's real working tree. It works in a git worktree on branch
`cursor/<run-id>`, and nothing lands in the repo until you run `apply`. That isolation is what
makes it safe to hand work off and review it afterwards.

## Is delegating worth it?

Delegation costs a written brief, a careful review and wall-clock time, so use it where that pays:

- well-specified, self-contained tasks: a bug with a known repro, a function to a spec, tests
  for existing code, a mechanical refactor;
- several independent tasks at once (each run gets its own worktree, so they can run in parallel);
- the user wants Cursor's models or plan used, or a second attempt to compare against yours.

Do it yourself when the change is a few edits, depends on context from this conversation that is
hard to write down, or needs back-and-forth with the user. If the user says to stop using Cursor,
stop the run (see "Stopping a run"), discard it and do the work yourself.

## What to expect from Grok 4.7 (from 6 runs on a real project, 2026-10-27)

- **Reliable on a clear brief:** all 6 runs were accepted on the first attempt, with no follow-up
  needed. It never edited a file the brief told it to leave alone, even with 3 runs in parallel
  on one repo. Its reports were honest: it flagged assumptions, listed the commands it ran and
  their results, and admitted what it could not verify.
- **It builds the brief's design exactly, flaws included.** It never questioned a design. One
  brief said a dirty tenant should "keep returning 503 while the sync is unavailable"; it built
  that, plus a guard that livelocked under concurrent writes. All 69 tests passed, yet 20–37% of
  PUTs failed under load. The bug came from the brief and nobody caught it until the work was
  measured. Your design is the weakest link, so stress it in the brief (see Verification below).
- **It verifies only what it is asked to.** It never added a load test unprompted. Its tests
  lean on mocks: 349 test lines for about 150 lines of code, faking processes even when the brief
  asked for a real-process check. It also does not spell out the limits of its own change: it
  missed that its in-flight guard only worked within one gateway.
- **It is slow.** Normal speed took 14–33 min per task at about 18–32 s per tool call, mostly
  reading far more of the codebase than needed. Fast mode took about 8 s per call. Plan for
  wall-clock time, and run independent tasks in parallel.
- **It gets itself unstuck on environment problems.** It worked around an unwritable pytest temp
  dir by itself, and in edit-only mode it reported that it could not run tests instead of hacking
  around it.

### Controlled assessment (6 runs with hidden acceptance tests, 2026-10-27)

Full write-up: `assessment.md` next to this file.
- **Correctness:** 5 of 6 runs passed every hidden test. Even a 2-line brief for a sliding-window
  rate limiter got thread safety, window boundaries and pruning right.
- **Brief detail costs time on standard tasks.** The full spec scored the same 8/8 as the 2-line
  brief but took 18 min instead of 6 (52 s vs 13 s per tool call). Spend words only on semantics
  that are not the obvious default, plus scope and environment.
- **Fast mode** was about 4× faster (4.6 vs 18.2 min on the identical brief), with the same hidden
  score and fewer tests (15 vs 23). Default stays normal (the user's choice); when time matters,
  tell the user fast mode showed no quality loss on well-specified work.
- **It finds bugs on its own** when asked to review against docstrings or a spec: 4 of 4 planted
  bugs fixed, a correct-but-odd function left alone, and regression tests that really fail on the
  buggy code.
- **It flags a bad design but builds it anyway unless given permission.** Without permission it
  wrote "the required sweep is O(n)… will not sustain the stated load… so that is what the code
  does" and failed the scale test. With one sentence of permission it built a better design and
  passed. Always include the permission clause from the Report section.
- **Scope and honesty were perfect in all 6 runs**, and 6 parallel runs were fine on a laptop with
  little free RAM.
- **Follow-ups are cheap.** One specific review comment ("the sweep is too slow at the stated scale;
  keep this behaviour, make it amortized O(1), add a timing test") fixed the design in one turn of
  8 min and 7 calls in the same chat. A quick run plus a targeted follow-up reached the same result
  sooner than one slow, careful run. Prefer `followup` over a fresh run when the work is close.
- **Tokens:** summary usage is in each run's `stream-N.jsonl` result event
  (`usage.inputTokens`, `outputTokens`, `cacheReadTokens`). Small tasks took about 70K fresh
  tokens and 300K cache reads per run; mini-s3 tasks about 1.9M cache reads each. Reading the
  codebase is the main cost, so point briefs at specific files.

## Setup (once per machine)

Run `python $SCRIPT doctor`. It shows the CLI it found, the version and the login state.
If `auth` says "Not logged in", ask the user to run `! python <full path to SCRIPT> login`, which
opens a browser, or to set `CURSOR_API_KEY`. You cannot log in on their behalf.

## Workflow

1. **Write the brief to a file.** Cursor sees none of this conversation, so the brief is all it
   has. Say what "done" means, and give it the exact test command. Template:

   ```markdown
   ## Goal
   <the outcome in one or two sentences, not the steps>
   ## Context
   - Files: path/to/module.py (function X), path/to/test_module.py
   - Background: <why it is needed; paste the exact error or failing output>
   ## Constraints
   - Do not change: <public APIs, files, existing tests>
   - Match the surrounding code style; no new dependencies
   ## Verification
   - Real behaviour, not only unit tests: <e.g. "start a real cluster and check a killed
     process is not orphaned", "1,500 concurrent PUTs: every response 201 and the counter exact">
   ## Done when
   - `<exact test command>` passes
   - <any other observable criterion>
   ## Scale
   - <expected sizes and rates: Grok can only notice that a design will not scale if you say what scale means>
   ## Report
   - If this design looks wrong under concurrency, load, failure or the stated scale, say so and why.
     You may implement a better design instead if you explain the trade-off.
   - State the limits of your change (what it does not cover, which assumptions it needs).
   ```

   Why each part matters, from experience:
   - **Verification.** Cursor won't add load or real-process checks on its own. A green
     unit suite once hid a 20–37% failure rate under load.
   - **Report.** It is the only way a headless run can push back on a design or state its limits.
     The permission sentence ("you may implement a better design") is what makes it act on a flaw it
     notices; without it, it reports the flaw and builds the flawed design anyway.
   - **Brevity.** Keep the Goal short for standard problems. Extra detail tripled run time in testing
     without improving the result.
   - **Environment quirks.** Put the machine's quirks in "How to test", or it burns time
     rediscovering them. For example: which interpreter or venv to use, environment variables that
     break tools, and `--basetemp=.pytest-tmp` if pytest's system temp dir is not writable.
   - **Measured numbers.** Give the reproduction script or measured numbers when you have them.
     A brief with "before: X, expected: Y" gets a sharper result than a vague one.

2. **Start the run in the background** (Bash `run_in_background: true`; the default agent timeout is 30 min):

   ```
   python $SCRIPT run --repo <project dir> --prompt-file <brief.md> --test "<cmd>" [--share .venv] [--model <id>] [--fast] [--name <label>]
   ```

   - The worktree starts from HEAD plus the user's uncommitted edits to tracked files. Untracked
     files are absent: `.venv`, `node_modules` and `.env` included. Pass `--share .venv` (or
     another directory) to link it in, and use absolute interpreter paths in `--test`.
   - Cursor may run any shell command it needs (Cursor's `--force` flag). The user asked for this
     on 2026-10-27, so do not turn it off unless they ask. Commands are not sandboxed: they can
     reach anything on the machine, including a `--share`d `.venv` (a `pip install` changes the
     real one). `--no-shell` makes a run edit-only. Either way, the script also runs `--test`
     itself afterwards, and that result is the one to trust.
   - The default model is `grok-4.7[context=500k,reasoning_effort=xhigh,fast=false]`, which
     Cursor reports as "Grok 4.7 500K Extra High" (normal speed). The user chose it on
     2026-10-27. Add `--fast` only when the user asks for speed. It switches to the fast variant
     ("... Extra High Fast"), which measured about 2–4× faster per tool call. Do not switch models
     unless they ask; `--model <id>` overrides for one run.
   - Model ids: `python $SCRIPT models | head -40` lists the base ids. Always truncate it: the
     full list is hundreds of ids and wastes context. Settings such as context size go in
     brackets, and the parameter names are in `~/.cursor/cli-config.json` under
     `modelParameters`. For Grok 4.7 they are `context`, `reasoning_effort` and `fast`; a wrong
     name such as `effort` is rejected.
   - New cache and build files (`__pycache__`, `.pytest_cache`, `node_modules`, `.coverage`, …)
     are left out of the diff automatically, so commands Cursor runs do not pollute it.

3. **Review it like a pull request from a stranger.** Read the summary first: `status`, `test`
   and `final_message`. Then read `diff.patch` in full. Cursor's final message is a claim, not
   evidence, so check it against the diff and the test output. Look in particular for:
   - tests that were deleted, skipped or weakened;
   - values hardcoded to make a test pass;
   - edits outside the brief's scope, or formatting churn;
   - secrets, or new dependencies.

   Keep the review lean when the user cares about Claude tokens. Read the summary and `git apply
   --stat`, then read in full the hunks that carry the logic (and every changed line of existing
   tests), but only the names of new test functions. Then check it yourself:
   - **Re-run the tests** in the worktree. A run's own test step can fail for harness reasons,
     such as a shared pytest temp dir when runs overlap. In that case the script's private `TMP`
     (and `--basetemp`) avoids the problem, and the failure says nothing about the code.
   - **Check real behaviour** for anything involving processes, concurrency or load: a short
     script against a real cluster or process. Mocked tests passing is not enough (see above).

4. **Decide:**
   - **Good:** `python $SCRIPT apply <run-id>`. This applies the diff to the real working tree
     without committing it and removes the worktree. Then run the tests in the real repo.
   - **Close:** `python $SCRIPT followup <run-id> --prompt-file feedback.md`. This continues the
     same Cursor chat in the same worktree and reruns the test command. Be specific: file, line,
     what is wrong and what you expect instead.
   - **Wrong:** `python $SCRIPT discard <run-id>`. Then do the task yourself or rewrite the brief.

5. **Report to the user:** what Cursor did, what you verified yourself, what you changed, and
   anything still open. Never pass on Cursor's claims as checked when you did not check them.

`status` in the summary is one of `ready`, `no_changes`, `agent_failed` (see `agent_error`) or `timeout`.

## Parallel runs

Several `run`s can go at once. Give them disjoint files, or disjoint functions of one file named
in each brief's constraints ("only edit `membership`, `set_nodes`…; another change edits
`status`…"). Cursor respects these lists, and patches built that way applied cleanly. Apply them
one at a time and commit each. If `apply` reports a 3-way merge, check the touched files for
conflict markers before going on.

After every wave, run the full suite on the merged result, then measure the behaviour the wave
was meant to change: latency, error rate under load, whatever the goal was. Each run's tests pass
on its own base, so only the merged result shows how the changes interact. And a passing suite
can still hide a regression that only a measurement shows.

## Stopping a run

Stop the background command with TaskStop; that ends the agent the script started. Do not kill
`node.exe` or "cursor-agent" processes by name. The Cursor IDE runs its own agent worker
(`... index.js ... worker start ...`, with an API key on its command line), which must be left
alone and never quoted back to the user. Confirm nothing is left by listing processes whose command
line contains the run id. Then run `python $SCRIPT discard <run-id>` to remove the worktree and
branch, and confirm with `git worktree list`.

## Other commands

| Command | What it does |
|---|---|
| `list` | every run with status, turn count and test result |
| `show <run-id>` | the latest summary for a run |
| `apply <run-id> --keep` | apply, but keep the worktree and branch |

Runs live in `~/.claude/cursor-runs/<run-id>/` (override with `CURSOR_RUNS_DIR`). Each holds
`prompt-N.md`, `stream-N.jsonl` (Cursor's raw event stream), `stderr-N.log`, `diff.patch`,
`test.log` and `summary.json`. After apply or discard only these logs remain, and old run folders
can be deleted freely.

## Troubleshooting

- **"not its own git repo"**: the project sits inside a larger repo (for example, a home
  directory that is itself a git repo). The project needs its own `git init` and a first commit. Ask the
  user before creating a repo in their project.
- **"has no commits yet"**: commit once so the worktree has a base.
- **`agent_failed` with an auth error**: run the login step above.
- **`apply` says the patch does not apply**: the real tree changed in the same places. The run is
  kept. Read the patch and merge by hand, or discard it and start a new run on the current tree.
- **Cursor gets updated**: the script picks the newest CLI version on each call, so updates need
  no change. Set `CURSOR_AGENT_BIN` to force a specific install.
