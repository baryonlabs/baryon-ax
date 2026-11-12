# Security policy

Please do not report vulnerabilities in public issues. Send a private report to the repository maintainers with a reproduction, impact, and suggested mitigation where possible. Maintainers should acknowledge reports promptly and coordinate a fix before public disclosure.

## Automated checks

Every pull request runs `security-sweep` and fails on P0/P1 findings. Weekly runs re-scan the default branch, build and scan container images, publish CycloneDX SBOMs, and scan the full git history for secrets. See [docs/security-ci.md](docs/security-ci.md) for gate policy, coverage, and the scanner bump procedure.
