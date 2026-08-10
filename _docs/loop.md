# The autonomous fix-and-deploy loop

This is the ruleset for the unattended `/loop` that watches
[retroloop](https://github.com/liyedanpdx/retroloop) for open, unblocked
issues and, when it finds one it can actually finish in one pass, fixes it,
gets it onto `develop`, and redeploys this machine
(`docker compose up -d --build` in `/home/paradx/retroloop_test/retroloop`).

It is a lighter-weight variant of the PM → Engineer → QA pipeline in
`_docs/process.md`: one agent plays every role instead of three separate
ones, in one pass rather than a human handing off between them, there is
no separate QA pass, and it only takes issues small enough to do that
safely unattended. Anything bigger stays on the normal process.

To run it: `/loop` (no interval — self-paced) with the prompt "read
`_docs/loop.md` and run one cycle." Update this file to change the rules;
the next wakeup picks up the change automatically — but only because that
next wakeup actually re-reads it. Nothing enforces this outside the
agent's own behavior: the wakeup prompt says to read this file, and that
only does anything if it is read with the `Read` tool, fresh off disk,
every single cycle — never from what an earlier cycle's turn in the
conversation remembers the rules to be. A long-running session can have
this file's content summarized out of view before a later cycle fires;
memory of "what loop.md said" is not this file.

## Each cycle

0. Read this file, `_docs/loop.md`, in full, with the `Read` tool — even if
   it was already read earlier in this conversation. This is step 0 and
   not folded into step 1 so skipping it cannot be a shortcut taken under
   time pressure.
1. `gh issue list --state open --repo liyedanpdx/retroloop`. Drop anything
   labeled `blocked`.
2. Walk the remaining issues lowest-numbered first. For each, check whether
   it already has a branch (`git ls-remote --heads origin "feat/<N>-*"`) or an
   open PR (`gh pr list --search "<N>"`). If either exists, skip it — it is
   either mid-review or already failed once this loop, and this loop does not
   retry an issue on its own.
3. If every remaining issue is skipped or there are none, stop. No branch, no
   commit, no comment — a no-op cycle should look like one.
4. Take the first issue nothing skipped.

## Grooming

If the issue does not already read like a groomed task — no `## Goal`,
`## Acceptance criteria`, `## Out of scope` and `## Constraints` sections,
the shape `_docs/task-template.md` lays out — groom it before doing
anything else that touches code, following `_docs/team/pm.md`: read
`_docs/decisions.md` first (`_docs/process.md`'s own rule for grooming),
rewrite the issue using the template, make every acceptance criterion
something you could point at the screen and check, and write down the
edge cases the person who filed it did not consider.

`gh issue edit <N> --body <groomed body>` so the issue on GitHub becomes
the groomed version — the issue itself, not a comment alongside the
original, the way a human PM would leave it for the next person. Leave a
short comment noting it was groomed by the automated loop, so a human
reading it later knows why the body changed shape.

Then judge the freshly-groomed issue the same as any other, below — small
enough, keep going in this same cycle and implement it; it turned out
bigger than it read, stop and move to the next candidate instead. Most
issues worth filing this tersely are small once written out; do not make
grooming-then-implementing-in-the-same-pass the exception.

## Sizing

If the issue — freshly groomed or already written this way — reads like a
multi-file epic with a long unchecked acceptance list (the way #18 or #33
do) rather than a small bounded bug or task, skip it and move to the next
candidate. Do not implement half of a big issue and call it done; a task
that grooming turned out to be this big belongs on the full
`_docs/process.md` pipeline, not this loop.

## Implementing

- Branch from the tip of `origin/develop`, named `feat/<N>-<short-slug>` —
  matches every existing branch in this repo (see `_docs/process.md`).
- Never touch `master` (nothing merges there automatically — also
  `_docs/process.md`), never force-push, never delete a branch or tag, never
  `--no-verify`.
- Backend dependencies only in `backend/pyproject.toml`, frontend only in
  `frontend/package.json`. Add one only if the fix genuinely needs it, and
  say why in the commit message — do not add anything speculative.
- Python via `conda run -n newpython`, never the system interpreter. Every
  `open()` call uses `encoding='utf-8'`.
- No hardcoded credentials. Never print `.env` contents or resolved secrets
  into a commit message, PR body, or issue comment.

## Testing

- Backend: confirm `backend/.env` exists first (copy the root `.env` if not
  — never edit its contents). Then, from `backend/`:
  `timeout 240 conda run -n newpython pytest -q`.
  A known issue (`_docs/deployment.md`) makes the full suite hang partway
  through against the real external Mongo — dozens of tests each opening and
  closing their own connection to a real, non-local box. If it does not
  finish inside the timeout, treat that as **inconclusive, not passing**: no
  deploy, comment on the issue explaining the suite hung, stop the cycle.
  Do not retry.
- Frontend, if touched: `cd frontend && npm run build && npx vitest run`.
- A real failure (not a timeout): push the branch anyway so it is visible,
  open a PR against `develop` but do not merge it, paste the relevant test
  output on the issue, and stop. The branch now existing is what keeps step 2
  from retrying this issue next cycle — a human has to look at it.

## On green

1. Commit (repo's existing style: short, says *why*, not *what*; do not
   describe the fix mechanically).
2. Push the branch, `gh pr create --base develop`, `gh pr merge --merge`.
3. `git checkout develop && git pull`.
4. `docker compose up -d --build`, wait for `docker compose ps` to show both
   services `healthy`.
5. Verify against the real LAN address, not `localhost` — read
   `BACKEND_PORT`/`FRONTEND_PORT` from `.env` rather than assuming a port:
   `curl http://192.168.1.27:${BACKEND_PORT:-8000}/api/ready` and
   `curl http://192.168.1.27:${FRONTEND_PORT:-3000}/`.
6. If the fix touches a user-facing flow (login, a page, anything clickable),
   use the `playwright` MCP tools against the deployed address for a quick
   real-browser sanity pass. Report what it found either way — this is a
   supplementary check, not a gate; a browser-automation flake does not mean
   the deploy failed.

## Closing the loop

Comment on the issue: commit hash, PR link, what was verified (test
counts, curl results, browser check if any). Close the issue only if its
acceptance criteria are now fully met — otherwise say plainly what is still
missing and leave it open, the way the #18 update in this issue tracker does.

Never report a test as passing or a deploy as healthy without having actually
run the command and read its output in this cycle.

## Ending a cycle

Before finishing, decide the next wakeup: 20–30 minutes, whether this cycle
did nothing or just shipped something. No need to check back sooner —
nothing here is time-sensitive enough to justify tighter polling.
