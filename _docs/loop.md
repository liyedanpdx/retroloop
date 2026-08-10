# The autonomous fix-and-deploy loop

This is the ruleset for the unattended `/loop` that watches
[retroloop](https://github.com/liyedanpdx/retroloop) for open, unblocked
issues and, when it finds one it can actually finish in one pass, fixes it,
gets it onto `develop`, and redeploys this machine
(`docker compose up -d --build` in `/home/paradx/retroloop_test/retroloop`).

It is a lighter-weight variant of the PM → Engineer → QA pipeline in
`_docs/process.md`: no grooming pass, one agent does fix-test-deploy
end to end, and it only takes issues small enough to do that safely
unattended. Anything bigger stays on the normal process.

To run it: `/loop` (no interval — self-paced) with the prompt "read
`_docs/loop.md` and run one cycle." Update this file to change the rules;
the next wakeup picks up the change automatically.

## Each cycle

1. `gh issue list --state open --repo liyedanpdx/retroloop`. Drop anything
   labeled `blocked`.
2. Walk the remaining issues lowest-numbered first. For each, check whether
   it already has a branch (`git ls-remote --heads origin "feat/<N>-*"`) or an
   open PR (`gh pr list --search "<N>"`). If either exists, skip it — it is
   either mid-review or already failed once this loop, and this loop does not
   retry an issue on its own.
3. If every remaining issue is skipped or there are none, stop. No branch, no
   commit, no comment — a no-op cycle should look like one.
4. Take the first issue nothing skipped. If it reads like a multi-file epic
   with a long unchecked acceptance list (the way #18 or #33 do) rather than
   a small bounded bug or task, skip it too and move to the next candidate —
   do not implement half of a big issue and call it done.

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
  `timeout 180 conda run -n newpython pytest -n auto -q`.
  The suite runs across every CPU core, each `pytest-xdist` worker against its
  own database (`_docs/deployment.md`), and finishes in about a minute — well
  inside the timeout. If it still does not finish inside the timeout, treat
  that as **inconclusive, not passing**: no deploy, comment on the issue
  explaining what happened, stop the cycle. Do not retry.
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
