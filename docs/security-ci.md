# Security CI runbook

This repository uses [security-sweep](https://github.com/baryonlabs/security-sweep) as a merge gate and a weekly tracker. The design goal is simple: **a known vulnerability should stop a merge, and the result should leave evidence.**

## What runs when

| Trigger | Job | What it does | Blocking |
| --- | --- | --- | --- |
| Pull request opened or updated | `vulnerability sweep` | Discovers lockfiles, runs osv-scanner + trivy, verifies every advisory against OSV, enriches with KEV/EPSS, checks reachability, writes `REPORT.md` / `REPORT.html` / `findings.json` | P0/P1 |
| Monday 03:00 KST (Sunday 18:00 UTC) | `vulnerability sweep` | Re-scans the default branch, scans the full git history for secrets with gitleaks, and writes a docling SBOM from its lockfile | P0/P1, leaked secrets |
| Monday 03:00 KST | `image scan (api/web/kordoc)` | Builds each image, scans OS and application packages with Trivy, publishes a CycloneDX SBOM | Critical application packages with a fix |
| Manual `workflow_dispatch` | Both jobs | Same as the scheduled run | Same |

Pull requests do **not** build images, so the PR check stays fast (about 30 seconds for the four projects).

## Gate policy

- **P0** — CISA KEV, or critical/high severity with a call trace.
- **P1** — other critical/high, EPSS >= 0.1, or a detected secret.
- **P2/P3** — reported in the summary and artifacts, but do not fail the build.
- The threshold is `SECURITY_FAIL_ON=P0,P1` in `.github/workflows/security-sweep.yml`.
- If `findings.json` is missing or unreadable, the gate **fails closed**. A broken scan is never a pass.
- A missing lockfile means the project is not scanned. That is a coverage regression, not a clean result.

The current item-level exception mechanism is intentionally limited: lowering `SECURITY_FAIL_ON` applies to the whole scan. An expiring allowlist is tracked in [#5](https://github.com/baryonlabs/baryon-ax/issues/5). Until it exists, record any risk acceptance in the PR description and restore the threshold when the fix lands.

## Coverage rule

Every project must ship a **version-pinned lockfile** and use it in its build:

| Project | Lockfile | Build uses it |
| --- | --- | --- |
| `apps/web` | `package-lock.json` | `npm ci` |
| `apps/api` | `uv.lock` | `uv sync --frozen` |
| `deploy/parsers/docling` | `requirements.txt` pinned from `requirements.in` | `pip install -r requirements.txt` |
| `deploy/parsers/kordoc` | `package-lock.json` | `npm ci` |

A `requirements.txt` that only contains ranges (`pkg>=1,<2`) is **not** a lockfile: OSV filters those packages as unscannable. Always generate the pinned file from `requirements.in`.

### Refreshing lockfiles

```bash
# apps/api
cd apps/api
uv lock
uv lock --check   # CI runs this

# deploy/parsers/docling
cd deploy/parsers/docling
uv pip compile requirements.in \
  --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 \
  --no-header \
  -o requirements.txt

# deploy/parsers/kordoc
cd deploy/parsers/kordoc
npm install --package-lock-only --no-audit --no-fund
npm ci --dry-run --omit=dev   # CI runs this
```

The `lockfile check` job fails if a lockfile is stale. Dependency upgrades that change a lockfile must also update the lock in the same PR.

## Reading the result

On a pull request:

1. **Checks → vulnerability sweep → Summary** shows the gate counts and blocking findings.
2. **Artifacts → `security-sweep-report`** contains the full output, retained for 14 days.
3. `REPORT.md` lists every finding with priority, package version, fixed version, reachability, and next action.
4. `findings.json` is the machine-readable source used by the gate.

On a scheduled run, also download:

- `security-sweep-report` — repo scan, gitleaks, and `sbom-docling.cdx.json`
- `security-sweep-image-api`, `security-sweep-image-web`, `security-sweep-image-kordoc` — `sbom-*.cdx.json` and the Trivy table

To reproduce the gate locally:

```bash
S=/path/to/security-sweep/skills/security-sweep/scripts
OUT=/tmp/security-sweep

bash "$S/discover.sh" "$PWD" --out "$OUT"
bash "$S/sweep.sh" --targets "$OUT/targets.json" --out "$OUT" --jobs 4
python3 "$S/verify_cves.py" "$OUT"
python3 "$S/reachability.py" "$OUT"
python3 "$S/report.py" "$OUT"
python3 .github/scripts/security_gate.py "$OUT/findings.json"
```

To scan an image locally:

```bash
docker build -t baryon-ax-api:scan apps/api
trivy image --scanners vuln --severity CRITICAL,HIGH baryon-ax-api:scan
trivy image --scanners vuln --format cyclonedx -o sbom-api.cdx.json baryon-ax-api:scan
```

## Image scanning policy

- **Application packages** (`--pkg-types library`) with CRITICAL severity and an available fix **fail** the weekly job. These are actionable through dependency upgrades.
- **OS packages** are reported but do not fail. Base-image CVEs depend on upstream rebuilds and would otherwise keep the job red without a code change.
- `docling` is not built in CI because its image is over 10 GB. Its Python dependencies are pinned in `requirements.txt`, scanned by the repo sweep, and exported as `sbom-docling.cdx.json`.
- The image job builds on a fresh runner, so base-image tags are pulled without a code change.
- Node base images include npm's own bundled dependencies (for example `undici`, `brace-expansion`). They appear under language packages but are part of the toolchain, not the application runtime. The critical-only gate keeps them from blocking while still reporting them.

## Required check (branch protection)

A green check is only a merge condition if `main` requires it. The repository uses a ruleset named `main-protection`:

- target: default branch (`main`)
- require a pull request before merging (0 approvals, so a solo maintainer is not blocked)
- require the **`vulnerability sweep`** status check
- require the branch to be up to date before merging
- block force pushes and branch deletion

Check it with:

```bash
gh api repos/baryonlabs/baryon-ax/rulesets
```

If the check name changes, update the ruleset in the same PR. A ruleset that references a check that never runs will block every merge.

## Pinned versions and how to bump them

| Pin | Current | Location |
| --- | --- | --- |
| security-sweep commit | `5b3fc938796fccdf091102ed258ba8e1686d3ba6` | `SECURITY_SWEEP_REF` |
| osv-scanner | `v2.7.0` | `OSV_SCANNER_VERSION` |
| Trivy CLI | `v0.76.1` | `TRIVY_VERSION` |
| setup-trivy action | `81e514348e19b6112ce2a7e3ecbafe19c1e1f567` | workflow `uses` |
| gitleaks | `v8.30.1` | `GITLEAKS_VERSION` |
| uv | `0.13.7` | `UV_VERSION`, API Dockerfile |

Bump procedure:

1. Read the upstream release notes and security advisories.
2. Change **one** pin in a PR. Do not combine a scanner bump with a product change.
3. Let the PR sweep run; compare the finding count with the previous run. A sudden drop can mean a scanner regression, not a fix.
4. Update this table and merge.
5. Never bump a pin during an incident; fix forward with the known-good version.

## Trust boundary and limitations

- The PR workflow runs with `contents: read` and no secrets. A fork PR cannot exfiltrate repository secrets through this workflow.
- The workflow and `.github/scripts/security_gate.py` are read from the PR checkout. A PR that edits them can change its own checks. Until [#4](https://github.com/baryonlabs/baryon-ax/issues/4) is resolved, treat workflow changes as security-relevant and review them explicitly.
- Reachability is grep-based for most ecosystems (Go uses `govulncheck`). `not-imported` is **not** a guarantee of safety, especially for transitive dependencies.
- The scan covers known vulnerabilities, secrets, and container configuration. Authorization logic, business-logic flaws, and trust-boundary mistakes require SAST and human review.
- This is a baseline, not a security certification. Production requirements are in [security.md](security.md).
