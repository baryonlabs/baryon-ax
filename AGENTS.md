# Working in this repository (agents and humans)

Several people and AI agent sessions work on this repository at the same time. These rules keep them from duplicating work or undoing each other.

## Before you start

1. `git fetch` and read the open pull requests: `gh pr list --state open`. If a PR already covers your task, continue on that branch or comment there instead of starting a new one.
2. Before you **create an external resource** (an analytics property, a Search Console or webmaster site, a cloud project, an OAuth client, a DNS record), search for an existing one and check open PRs for its ID. Two sessions created two GA4 properties for the same site in November 2026; only one survived.
3. One task, one branch, one PR. Name branches `<type>/<topic>` (`feat/`, `fix/`, `docs/`, `ci/`, `chore/`, `site/`).

## While you work

- Never push to `main`. Open a PR and merge only after CI is green (`api`, `web`, `lockfile check`, `vulnerability sweep`).
- Do not stack PRs on another PR's branch unless you will merge them in order yourself. When the base branch is deleted on merge, GitHub closes the stacked PR.
- Keep `apps/api/uv.lock` in sync when you change Python dependencies (`uv lock`). The docling pins change only through `scripts/refresh_docling_pins.sh`.
- This repository is public. Do not commit customer names, internal hosts or IPs, credentials, tokens, or traffic numbers.

## Browser automation

- Use your own tab group or webclaw Space per session (`[OPEN@space:<session-name>]`, then target every marker with `@space:<session-name>` or an exact URL fragment). Never act on tabs you did not open.
- Close the tabs you opened when you finish, after checking each tab's exact URL.
- Never type passwords or one-time codes. Hand the login to a person and continue after they finish.

## Coordination

- Large or multi-step work is split by one coordinating session (or a manager agent), which assigns branches. Other sessions do not start overlapping work on the same files.
- Record decisions that affect other sessions in the PR description or in `docs/` (for example `docs/roadmap.md`), not only in chat.

## Where things are

- Architecture and feature map: `README.md` → Architecture
- Roadmap, target users, open-source scope: `docs/roadmap.md`
- Security CI and the gate: `docs/security-ci.md`, `.github/scripts/security_gate.py`
- Website operations, analytics events, UTM rules: `docs/promotion-playbook.md`
