# Process

## How work flows

1. Tasks are GitHub issues in [retroloop](https://github.com/liyedanpdx/retroloop/issues)
2. PM grooms an issue before anyone implements it
3. Engineer implements one groomed issue at a time
4. Commit regularly, do not close the issue — leave a comment saying what you did

## Branches

One branch per issue, named `feat/<issue-number>-<short-slug>`.

Each branch is cut from the previous issue's branch, not from `master`, because
the backlog is ordered by dependency — #4 needs #3's project model, #5 needs #4's
cycles. `master` therefore sits behind the work, and the tip of the chain is the
branch for the highest-numbered finished issue.

Nothing merges to `master` automatically. That is a deliberate call to make, not
something an engineer does on the way past.

## Background

- `_docs/decisions.md` — the calls already made, with reasons. Read it before
  grooming or implementing, and do not reopen a decision without changing it
  there first
- `_docs/outdated/*` holds the plan, architecture, and original task list. They
  are reference, not the backlog — where they disagree with `decisions.md` or
  an issue, they lose

## Roles

- PM — grooms a task before anyone implements it, follows `_docs/team/pm.md`
- Engineer — implements one groomed task, follows `_docs/team/software-engineer.md`
- QA - checks the result against the acceptance criteria, follows _docs/team/qa-engineer.md

## Labels

- `mvp` — all current issues are MVP scope
- `backend` — Python / FastAPI work
- `frontend` — React / TypeScript work
- `infra` — Docker, CI, environment setup


## Orchestrator

The main session is the orchestrator. It launches the PM, the engineer
and QA as subagents. It does not groom, implement or test itself.

Lifecycle

1. Pick the next open issue from the backlog
2. PM grooms it
3. Engineer implements it
4. QA verifies it
5. On FAIL, back to step 3 with the QA comment as input
6. On PASS, close the issue
7. Repeat until the backlog is empty

Rules

- Do not skip step 2
- The engineer does not close the issue
- QA does not fix the code, only outputs PASS or FAIL
- The orchestrator closes the issue only after QA outputs PASS