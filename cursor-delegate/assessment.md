# How to use Grok 4.7 (via Cursor CLI) efficiently: assessment results

> This is the write-up only. The test harness it refers to (`workspace/`, `hidden/`, `briefs/`, `grade.py`) is not included in this repo.

Run on 27 Oct 2026, 13:19–13:37, on a Windows laptop. Six runs went in parallel through the `cursor-delegate`
pipeline (isolated git worktrees). Grok never saw the acceptance tests. They live in `hidden/` and
`grade.py` runs them against each worktree. Model: `grok-4.7[context=500k,reasoning_effort=xhigh]`, at
normal speed unless marked fast.

## The project

`workspace/` is a small "shoptools" repo with three modules:

- `ratelimit.py`: empty. Task: implement a per-key sliding-window rate limiter.
- `ttlcache.py`: empty. Task: implement a TTL cache. The brief *requires* a deliberately bad design (a full
  sweep of the dict on every call) and also states a scale (100k entries, thousands of ops/s) at which that
  design fails.
- `inventory.py`: holds 4 planted bugs (a shared mutable default, an off-by-one in pagination, half-even
  instead of half-up rounding, `<=` instead of `<`) and one trap: a correct but odd-looking ceil idiom
  that must not be "fixed".

## Results

| Run | Question | Time | Tool calls | s/call | Hidden tests | Tests it wrote | Scope |
|---|---|---|---|---|---|---|---|
| rl-terse | 2-line brief | 5.9 min | 27 | 13 | **8/8** | 16 | ok |
| rl-detailed | full spec | 18.2 min | 21 | 52 | **8/8** | 23 | ok |
| rl-detailed-fast | same full spec, fast mode | **4.6 min** | 13 | 21 | **8/8** | 15 | ok |
| bughunt | find bugs from docstrings alone | 9.7 min | 19 | 31 | **6/6** (4 bugs fixed, trap untouched) | 4 (each fails on the buggy code) | ok |
| cache-silent | bad design, no permission to deviate | 5.6 min | 14 | 24 | 5/6 (failed the scale test) | 14 | ok |
| cache-invited | bad design, invited to challenge it | 17.4 min | 20 | 52 | **6/6** | 21 | ok |

**Follow-up turn on cache-silent:** permission to change the design was given afterwards as review
feedback. That turn took **8.1 min and 7 tool calls** in the same chat and scored **6/6**. Token use for
the turn was 35K input, 33K output and 327K cache reads.

| Path to a correct cache | Total time | Tool calls |
|---|---|---|
| One careful run with permission up front (cache-invited) | 17.4 min | 20 |
| Quick run, then one specific follow-up (cache-silent + follow-up) | 5.6 + 8.1 = 13.7 min | 21 |

The follow-up reuses the chat, so Grok does not re-read the project. One concrete review comment fixed the
problem in a single turn.

## Findings

1. **Correctness was high everywhere.** 5 of 6 runs passed every hidden test. Thread safety, window
   boundaries, pruning and denied-requests-don't-count all came out right even from the 2-line brief. On a
   well-known algorithm, Grok's defaults are good.
2. **A detailed brief bought nothing here and cost 3× the time.** rl-terse and rl-detailed both scored 8/8.
   The detailed run took 18 min instead of 6, with longer reasoning per call (52 s vs 13 s) and more tests.
   Write detailed semantics only where the right behaviour is *not* the obvious one.
3. **Fast mode was about 4× faster with no loss of quality.** On the identical detailed brief it took
   4.6 min instead of 18.2, scored 8/8 on hidden tests, and wrote 15 tests instead of 23.
4. **Unaided bug finding works on a small module with docstrings.** It found all 4 bugs, did not touch the
   trap, and kept every signature. All 4 of its regression tests fail on the original code, so they are real
   tests. The earlier observation that "it never finds anything outside the brief" came from briefs that
   never asked it to look.
5. **It flags a bad design but obeys it unless given permission.** cache-silent wrote in its report that the
   required sweep "is O(n) per operation, so a cache of 100,000 entries will not sustain thousands of
   operations per second… The sweep is what the task asks for, so that is what the code does." It then failed
   the scale test. cache-invited, told it may deviate, built an ordered-by-deadline design (amortized O(1)),
   passed everything, and flagged that a checker might expect the literal sweep. **One sentence of
   permission was the difference between a wrong and a right result.**
6. **Scope and honesty were perfect in all 6 runs.** It edited only the allowed files, and its reports matched
   what the grader found.
7. **Time varies more with reasoning depth than with task size.** It ranged from 13 to 52 s per call. The
   slowest runs had the most demanding briefs (full specs, design judgement), not the most code.
8. **Six parallel runs were fine** on a laptop with little free RAM: small pure-Python tasks, no clusters.
9. **Follow-ups are cheap and effective.** One specific review comment fixed the design in a single 8-minute,
   7-call turn. A quick run plus a targeted follow-up reached the same 6/6 sooner than one slow, careful run.
10. **Token use** for the 6 assessment runs was 423K fresh input, 257K output and 1.8M cache reads. The
    mini-s3 runs on a larger codebase used about 1.9M cache reads each, so reading is the main cost; point
    briefs at specific files.

## How to brief Grok, distilled

- Keep the brief short when the task is standard. Spend words on non-obvious semantics, constraints
  (files it may edit) and the environment.
- **Always add a permission clause:** "If the required design looks wrong at the stated scale, say so; you
  may implement a better one if you explain the trade-off." Without it, Grok reports the problem and
  ships it anyway.
- State the scale or load explicitly, so it can notice problems.
- Use fast mode for clear, well-specified work. It was 4× faster here with the same result. Keep normal
  speed for open design work if you want its most careful reasoning (but measure: normal mode did not
  score higher in this set).
- Still verify with your own hidden or acceptance tests. Its own tests were good here, but a green suite
  is not proof (see the mini-s3 503 regression in `cursor-delegate`'s skill notes).

## Limits of this assessment

- One run per condition (n=1). The differences in time are large; the quality findings are a direction,
  not a statistic.
- Small, self-contained tasks (≤150 lines). The mini-s3 runs (multi-file, real clusters) were slower and
  produced one design regression.
- Grading by hidden tests measures behaviour, not code quality. The code was read only spot-wise.
