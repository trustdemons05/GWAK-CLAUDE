#!/usr/bin/env python3
"""Delegate a coding task to the Cursor Agent CLI inside an isolated git worktree.

    cursor_task.py doctor                       # find the CLI, show version + login state
    cursor_task.py login                        # interactive browser login (run once)
    cursor_task.py run --repo PATH --prompt-file brief.md [--test CMD] [--model M]
                       [--fast] [--no-shell] [--share .venv] [--name NAME] [--timeout SEC]
    cursor_task.py followup RUN_ID --prompt "..." [--test CMD]
    cursor_task.py show RUN_ID
    cursor_task.py apply RUN_ID [--keep]
    cursor_task.py discard RUN_ID
    cursor_task.py list

Cursor never touches the real working tree: it edits a worktree on branch cursor/<run-id>, the
script collects the diff (and optionally runs a test command), and nothing reaches the repo until
`apply`. Runs live in ~/.claude/cursor-runs/<run-id>/ (override with CURSOR_RUNS_DIR).
Standard library only; Python 3.10+.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

RUNS = Path(os.environ.get("CURSOR_RUNS_DIR") or Path.home() / ".claude" / "cursor-runs")
WIN = os.name == "nt"
VERSION_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-(\d{2})-(\d{2})-(\d{2}))?-[0-9a-f]+$")
FINAL_STATES = ("applied", "discarded")
# The user's choice (2026-10-27): Grok 4.7, 500K context, extra-high effort, normal speed.
# Cursor reports it as "Grok 4.7 500K Extra High". `--fast` switches a run to fast mode.
DEFAULT_MODEL = (os.environ.get("CURSOR_DELEGATE_MODEL")
                 or "grok-4.7[context=500k,reasoning_effort=xhigh,fast=false]")


def with_fast(model: str) -> str:
    """The fast variant of `model`: bracket parameters get fast=true, plain ids get -fast."""
    if "fast=false" in model:
        return model.replace("fast=false", "fast=true")
    if model.endswith("]"):
        return model if "fast=" in model else model[:-1] + ",fast=true]"
    return model if model.endswith("-fast") else model + "-fast"
# New files under these are tool output, not Cursor's work, and stay out of the diff.
ARTIFACT_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "node_modules",
                 "htmlcov", ".next", ".gradle", ".parcel-cache"}
ARTIFACT_NAMES = {".coverage", ".DS_Store", "Thumbs.db"}
PREAMBLE = """\
You are running headless: no human will answer questions during this task.
{shell}
Work inside the current workspace. Do not commit, push, rebase, switch branches or change git
config; your edits are collected from the working tree and reviewed before they are used.
If something is ambiguous, make the most reasonable choice and say so at the end.
Finish with a short report: what you changed, which commands you ran and their results, what you
did not do, and anything you are unsure about.

TASK:
"""
SHELL_ON = ("You may run any shell command you need: tests, builds, linters, package installs.\n"
            "Verify your change by running the relevant tests before you finish.")
SHELL_OFF = ("Shell commands are disabled in this session; edit files only. "
             "The tests are run separately after you finish.")


def die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


# ---- locating the Cursor Agent CLI ---------------------------------------------------------
def _newest_version(versions: Path):
    best = None
    if versions.is_dir():
        for d in versions.iterdir():
            m = VERSION_RE.match(d.name)
            if m and (d / "index.js").is_file():
                key = tuple(int(g or 0) for g in m.groups())
                if best is None or key > best[0]:
                    best = (key, d)
    return best[1] if best else None


def agent_cmd() -> tuple[list[str], dict]:
    """argv prefix and extra env for the CLI.

    On Windows the .cmd launcher goes through cmd.exe and PowerShell, which mangles multi-line
    prompts, so we call the bundled node.exe on index.js directly, the way the launcher does.
    """
    env = {"CURSOR_INVOKED_AS": "cursor-agent"}
    candidates = []
    if os.environ.get("CURSOR_AGENT_BIN"):
        candidates.append(Path(os.environ["CURSOR_AGENT_BIN"]))
    for name in ("cursor-agent", "agent"):
        if found := shutil.which(name):
            candidates.append(Path(found))
    if WIN:
        local, roaming = Path(os.environ.get("LOCALAPPDATA", "")), Path(os.environ.get("APPDATA", ""))
        candidates += [local / "cursor-agent",   # standalone installer
                       roaming / "Cursor/User/globalStorage/anysphere.cursor-agent-worker"
                                 "/agent-cli/.local/share/cursor-agent"]   # copy bundled with the IDE
        env["NODE_COMPILE_CACHE"] = os.environ.get("NODE_COMPILE_CACHE") or str(local / "cursor-compile-cache")
    for c in candidates:
        if WIN:
            base = c.parent if c.is_file() else c
            for d in (base, _newest_version(base / "versions")):
                if d and (d / "index.js").is_file() and (d / "node.exe").is_file():
                    return [str(d / "node.exe"), str(d / "index.js")], env
        elif c.is_file():
            return [str(c)], env
    die("Cursor Agent CLI not found. Install it (https://cursor.com/cli) or set CURSOR_AGENT_BIN.")


def _agent_env(extra: dict) -> dict:
    return {**os.environ, **extra}


# ---- git helpers -------------------------------------------------------------------------
def git(repo, *args, check=True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode:
        die(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def git_bytes(repo, *args) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode:
        die(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


# ---- run bookkeeping ---------------------------------------------------------------------
def _load(run_id: str):
    run = RUNS / run_id
    if not (run / "meta.json").is_file():
        die(f"no run {run_id!r} in {RUNS}")
    return run, json.loads((run / "meta.json").read_text(encoding="utf-8"))


def _save(run: Path, meta: dict):
    (run / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _read_prompt(a) -> str:
    if a.prompt_file == "-":
        text = sys.stdin.read()
    elif a.prompt_file:
        text = Path(a.prompt_file).read_text(encoding="utf-8")
    else:
        text = a.prompt or ""
    if not text.strip():
        die("empty prompt: pass --prompt or --prompt-file")
    return text


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:24].strip("-") or "task"


# ---- shared directories (e.g. an untracked .venv) ----------------------------------------
def _link_dir(src: Path, dst: Path):
    if WIN:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True, text=True)
        if r.returncode:
            die(f"could not link {dst} -> {src}: {r.stdout.strip() or r.stderr.strip()}")
    else:
        dst.symlink_to(src, target_is_directory=True)


def _unlink_shares(meta: dict):
    """Remove share links before anything deletes the worktree, so the real directories survive."""
    for rel in meta.get("shares", []):
        p = Path(meta["worktree"]) / rel
        if p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(p)):
            if WIN:
                os.rmdir(p)          # removes the junction itself, never the target's contents
            else:
                p.unlink()


def _cleanup(meta: dict):
    _unlink_shares(meta)
    git(meta["repo_top"], "worktree", "remove", "--force", meta["worktree"], check=False)
    git(meta["repo_top"], "worktree", "prune", check=False)
    git(meta["repo_top"], "branch", "-D", meta["branch"], check=False)


# ---- one agent turn ------------------------------------------------------------------------
def _kill_tree(p: subprocess.Popen):
    if WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    p.wait()


def _parse_stream(path: Path) -> dict:
    info = {"chat_id": None, "model": None, "final_message": None, "is_error": None,
            "tool_calls": 0, "events": 0}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        info["events"] += 1
        info["chat_id"] = ev.get("session_id") or ev.get("chat_id") or info["chat_id"]
        if ev.get("type") == "system" and ev.get("model"):
            info["model"] = ev["model"]
        if ev.get("type") == "tool_call" and ev.get("subtype") == "completed":
            info["tool_calls"] += 1
        if ev.get("type") == "result":
            info["final_message"] = ev.get("result")
            info["is_error"] = bool(ev.get("is_error"))
    return info


def _turn(run: Path, meta: dict, prompt: str, timeout: int, raw: bool) -> dict:
    n = meta["turns"] + 1
    full = prompt if raw else PREAMBLE.format(shell=SHELL_ON if meta.get("allow_shell") else SHELL_OFF) + prompt
    (run / f"prompt-{n}.md").write_text(full, encoding="utf-8")
    argv, env = agent_cmd()
    argv += ["-p", "--output-format", "stream-json", "--trust", "--workspace", meta["workspace"]]
    if meta.get("model"):
        argv += ["--model", meta["model"]]
    if meta.get("allow_shell"):
        argv.append("--force")
    if meta.get("chat_id"):
        argv += ["--resume", meta["chat_id"]]
    argv.append(full)
    t0 = time.time()
    with open(run / f"stream-{n}.jsonl", "wb") as out, open(run / f"stderr-{n}.log", "wb") as err:
        p = subprocess.Popen(argv, cwd=meta["workspace"], stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                             env=_agent_env(env), start_new_session=not WIN)
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            code = "timeout"
    info = _parse_stream(run / f"stream-{n}.jsonl")
    if code != 0:
        err_text = (run / f"stderr-{n}.log").read_text(encoding="utf-8", errors="replace").strip()
        info["agent_error"] = err_text.splitlines()[-10:]
    meta["turns"] = n
    meta["chat_id"] = info["chat_id"] or meta.get("chat_id")
    meta["model_used"] = info["model"] or meta.get("model_used")
    return {"turn": n, "agent_exit": code, "duration_s": round(time.time() - t0, 1), **info}


def _collect(run: Path, meta: dict, test_cmd: str | None, test_timeout: int) -> dict:
    wt = meta["worktree"]
    git(wt, "add", "-A", "--", ".")
    for rel in meta.get("shares", []):   # usually ignored already; unstage in case it is not
        git(wt, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", rel, check=False)
    # Only files that are new since the base: a cache file the repo already tracks is left alone.
    added = git_bytes(wt, "diff", "--cached", "--name-only", "-z", "--diff-filter=A", meta["base"])
    junk = [p for p in added.decode("utf-8", errors="replace").split("\0") if p and _is_artifact(p)]
    if junk:
        subprocess.run(["git", "-C", wt, "rm", "-q", "--cached", "--pathspec-from-file=-",
                        "--pathspec-file-nul"], input="\0".join(junk).encode(), capture_output=True)
    (run / "diff.patch").write_bytes(git_bytes(wt, "diff", "--cached", "--binary", meta["base"]))
    stat = git(wt, "diff", "--cached", "--stat", meta["base"])
    (run / "diffstat.txt").write_text(stat + "\n", encoding="utf-8")
    files = git(wt, "diff", "--cached", "--name-status", meta["base"]).splitlines()
    test = None
    if test_cmd:
        t0 = time.time()
        # A private temp dir per run: parallel runs otherwise collide in shared folders such as
        # pytest's <tmp>/pytest-of-<user> and fail with PermissionError.
        tmp = run / "tmp"
        tmp.mkdir(exist_ok=True)
        test_env = {**os.environ, "TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp)}
        try:
            r = subprocess.run(test_cmd, shell=True, cwd=meta["workspace"], capture_output=True,
                               timeout=test_timeout, env=test_env)
            code, output = r.returncode, (r.stdout + r.stderr).decode(errors="replace")
        except subprocess.TimeoutExpired as e:
            code, output = "timeout", ((e.stdout or b"") + (e.stderr or b"")).decode(errors="replace")
        (run / "test.log").write_text(output, encoding="utf-8")
        test = {"cmd": test_cmd, "exit_code": code, "passed": code == 0,
                "duration_s": round(time.time() - t0, 1), "tail": output.strip().splitlines()[-15:],
                "artifacts_removed": _drop_test_artifacts(meta)}
    return {"files_changed": files, "diffstat": stat.splitlines()[-1:] if stat else [], "test": test}


def _is_artifact(path: str) -> bool:
    *dirs, name = path.split("/")
    return (any(d in ARTIFACT_DIRS or d.endswith(".egg-info") for d in dirs)
            or name in ARTIFACT_NAMES or name.endswith((".pyc", ".pyo")))


def _drop_test_artifacts(meta: dict) -> list[str]:
    """Undo what the test command left behind (caches, coverage, build output).

    Everything Cursor wrote is already staged, so anything that differs from the index now came
    from the test run. Leaving it would leak it into the next turn's diff and into `apply`.
    Paths under shared directories are skipped: those are links to the user's real directories.
    """
    wt = Path(meta["worktree"])
    shares = [s.rstrip("/") + "/" for s in meta.get("shares", [])]
    out = subprocess.run(["git", "-C", str(wt), "status", "--porcelain", "-z", "--untracked-files=all"],
                         capture_output=True).stdout.decode("utf-8", errors="replace")
    removed, restore = [], []
    entries = iter(out.split("\0"))
    for entry in entries:
        if not entry:
            continue
        xy, path = entry[:2], entry[3:]
        if xy[0] in "RC":
            next(entries, None)   # a staged rename/copy is followed by its source path

        if any(path == s[:-1] or path.startswith(s) for s in shares):
            continue
        if xy == "??":
            target = (wt / path).resolve()
            if wt.resolve() in target.parents and target.is_file():
                target.unlink()
                removed.append(path)
                parent = target.parent
                while parent != wt.resolve() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
        elif xy[1] in "MD":
            restore.append(path)
    if restore:
        git(wt, "restore", "--", *restore, check=False)
    return removed[:20] + ([f"... {len(removed) - 20} more"] if len(removed) > 20 else []) + \
        [f"restored {p}" for p in restore]


def _finish(run: Path, meta: dict, turn: dict, collected: dict):
    if turn["agent_exit"] == "timeout":
        status = "timeout"
    elif turn["agent_exit"] != 0 or turn["is_error"]:
        status = "agent_failed"
    elif not collected["files_changed"]:
        status = "no_changes"
    else:
        status = "ready"
    meta["status"] = status
    meta["test_cmd"] = (collected["test"] or {}).get("cmd") or meta.get("test_cmd")
    _save(run, meta)
    summary = {
        "run_id": meta["run_id"], "status": status, **turn, **collected,
        "repo": meta["workspace_src"], "worktree": meta["workspace"], "branch": meta["branch"],
        "paths": {"diff": str(run / "diff.patch"), "stream": str(run / f"stream-{turn['turn']}.jsonl"),
                  "stderr": str(run / f"stderr-{turn['turn']}.log"),
                  "test_log": str(run / "test.log") if collected["test"] else None},
        "next": f"read diff.patch, then: apply {meta['run_id']} | followup {meta['run_id']} --prompt ... | "
                f"discard {meta['run_id']}",
    }
    (run / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    shown = dict(summary)
    if shown.get("final_message") and len(shown["final_message"]) > 3000:
        shown["final_message"] = shown["final_message"][:3000] + " ...[truncated; see summary.json]"
    print(json.dumps(shown, indent=2))


# ---- commands ------------------------------------------------------------------------------
def cmd_run(a):
    src = Path(a.repo).resolve()
    if not src.is_dir():
        die(f"{src} is not a directory")
    top = Path(git(src, "rev-parse", "--show-toplevel"))
    if top.resolve() == Path.home().resolve():
        die(f"{src} is not its own git repo (the enclosing repo is your home directory). "
            f"Run `git init` and commit in the project first.")
    head = git(top, "rev-parse", "--verify", "-q", "HEAD", check=False)
    if not head:
        die(f"{top} has no commits yet; commit once so the worktree has a base.")
    # Snapshot tracked edits without touching the tree, so Cursor starts from what you see.
    base = git(top, "stash", "create") or head
    untracked = [f for f in git(top, "ls-files", "--others", "--exclude-standard").splitlines() if f]
    rel = src.relative_to(top.resolve()) if src != top.resolve() else Path(".")
    prompt = _read_prompt(a)
    run_id = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{_slug(a.name or prompt)}"
    run = RUNS / run_id
    run.mkdir(parents=True)
    wt = run / "wt"
    branch = f"cursor/{run_id}"
    git(top, "worktree", "add", "-q", "-b", branch, str(wt), base)
    workspace = (wt / rel).resolve()
    shares = []
    for name in a.share or []:
        s, d = src / name, workspace / name
        if not s.is_dir():
            die(f"--share {name}: {s} is not a directory")
        if not d.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            _link_dir(s, d)
            shares.append((rel / name).as_posix())
    meta = {"run_id": run_id, "repo_top": str(top), "workspace_src": str(src), "worktree": str(wt),
            "workspace": str(workspace), "branch": branch, "base": base, "head": head,
            "model": with_fast(a.model or DEFAULT_MODEL) if a.fast else (a.model or DEFAULT_MODEL),
            "allow_shell": not a.no_shell, "shares": shares, "turns": 0,
            "chat_id": None, "status": "running", "created": dt.datetime.now().isoformat(timespec="seconds")}
    _save(run, meta)
    if untracked:
        print(f"note: {len(untracked)} untracked file(s) in {top} are not in the worktree "
              f"(e.g. {untracked[0]}); commit or --share them if Cursor needs them", file=sys.stderr)
    turn = _turn(run, meta, prompt, a.timeout, a.raw)
    _finish(run, meta, turn, _collect(run, meta, a.test, a.test_timeout))


def cmd_followup(a):
    run, meta = _load(a.run_id)
    if meta["status"] in FINAL_STATES:
        die(f"run is already {meta['status']}")
    if not Path(meta["worktree"]).is_dir():
        die("worktree is gone")
    if not meta.get("chat_id"):
        print("note: no chat id was captured; this starts a fresh Cursor chat in the same worktree",
              file=sys.stderr)
    if a.model:
        meta["model"] = a.model
    if a.fast:   # switch an existing chat to fast mode; the worktree and chat history carry over
        meta["model"] = with_fast(meta.get("model") or DEFAULT_MODEL)
    turn = _turn(run, meta, _read_prompt(a), a.timeout, a.raw)
    _finish(run, meta, turn, _collect(run, meta, a.test or meta.get("test_cmd"), a.test_timeout))


def cmd_apply(a):
    run, meta = _load(a.run_id)
    if meta["status"] in FINAL_STATES:
        die(f"run is already {meta['status']}")
    patch = run / "diff.patch"
    top = meta["repo_top"]
    if patch.stat().st_size:
        if subprocess.run(["git", "-C", top, "apply", "--check", str(patch)], capture_output=True).returncode == 0:
            git(top, "apply", str(patch))
            how = "clean"
        else:
            r = subprocess.run(["git", "-C", top, "apply", "--3way", str(patch)], capture_output=True, text=True)
            if r.returncode:
                die(f"patch does not apply to {top} (worktree kept):\n{(r.stderr or r.stdout).strip()}")
            how = "3-way merge; check for conflict markers"
    else:
        how = "nothing to apply (empty diff)"
    if not a.keep:
        _cleanup(meta)
    meta["status"] = "applied"
    _save(run, meta)
    print(json.dumps({"run_id": meta["run_id"], "applied_to": top, "how": how,
                      "worktree_removed": not a.keep}, indent=2))


def cmd_discard(a):
    run, meta = _load(a.run_id)
    _cleanup(meta)
    meta["status"] = "discarded"
    _save(run, meta)
    print(json.dumps({"run_id": meta["run_id"], "status": "discarded"}))


def cmd_show(a):
    run, _ = _load(a.run_id)
    path = run / "summary.json"
    print(path.read_text(encoding="utf-8") if path.is_file() else (run / "meta.json").read_text(encoding="utf-8"))


def cmd_list(a):
    rows = []
    for d in sorted(RUNS.glob("*/meta.json")) if RUNS.is_dir() else []:
        m = json.loads(d.read_text(encoding="utf-8"))
        s = d.parent / "summary.json"
        test = json.loads(s.read_text(encoding="utf-8")).get("test") if s.is_file() else None
        rows.append({"run_id": m["run_id"], "status": m["status"], "repo": m["workspace_src"],
                     "turns": m["turns"], "tests_passed": test and test.get("passed")})
    print(json.dumps(rows, indent=2))


def cmd_doctor(a):
    argv, env = agent_cmd()
    def agent(*args):
        r = subprocess.run(argv + list(args), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=_agent_env(env), timeout=120)
        return (r.stdout + r.stderr).strip()
    print(json.dumps({"agent": argv, "version": agent("--version"), "auth": agent("status"),
                      "git": git(".", "--version"), "runs_dir": str(RUNS)}, indent=2))


def cmd_login(a):
    argv, env = agent_cmd()
    sys.exit(subprocess.call(argv + ["login"], env=_agent_env(env)))


def cmd_models(a):
    argv, env = agent_cmd()
    sys.exit(subprocess.call(argv + ["models"], env=_agent_env(env)))


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def prompt_args(sp):
        g = sp.add_mutually_exclusive_group(required=True)
        g.add_argument("--prompt", help="task text")
        g.add_argument("--prompt-file", help="file with the task text, or - for stdin")
        sp.add_argument("--test", help="shell command run in the worktree after Cursor finishes")
        sp.add_argument("--timeout", type=int, default=1800, help="seconds before the agent is killed")
        sp.add_argument("--test-timeout", type=int, default=900)
        sp.add_argument("--raw", action="store_true", help="send the prompt without the headless preamble")

    r = sub.add_parser("run", help="start a task in a fresh worktree")
    r.add_argument("--repo", default=".", help="project directory (a git repo or a subdirectory of one)")
    prompt_args(r)
    r.add_argument("--model", help=f"Cursor model id (see `models`); default {DEFAULT_MODEL}, "
                                   "'auto' for the account default")
    r.add_argument("--fast", action="store_true",
                   help="use the fast variant of the model (only when the user asks for speed)")
    r.add_argument("--no-shell", action="store_true",
                   help="edit-only: do not pass --force, so Cursor cannot run shell commands")
    r.add_argument("--share", action="append", help="untracked dir to link into the worktree, e.g. .venv")
    r.add_argument("--name", help="short label used in the run id")
    r.set_defaults(fn=cmd_run)

    f = sub.add_parser("followup", help="send feedback to the same Cursor chat and worktree")
    f.add_argument("run_id")
    prompt_args(f)
    f.add_argument("--model", help="change the model for this and later turns")
    f.add_argument("--fast", action="store_true", help="switch this chat to the model's fast variant")
    f.set_defaults(fn=cmd_followup)

    for name, fn in (("show", cmd_show), ("discard", cmd_discard)):
        sp = sub.add_parser(name)
        sp.add_argument("run_id")
        sp.set_defaults(fn=fn)
    ap = sub.add_parser("apply", help="apply the run's diff to the real working tree")
    ap.add_argument("run_id")
    ap.add_argument("--keep", action="store_true", help="keep the worktree and branch afterwards")
    ap.set_defaults(fn=cmd_apply)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    sub.add_parser("models", help="list model ids usable with --model").set_defaults(fn=cmd_models)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
