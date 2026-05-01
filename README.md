# Self-Contain ☤

> Personal AI agent infrastructure — fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) with custom configurations, pruned dependencies, and localized workflow integrations.

## What is this?

A self-hosted AI agent setup tailored for personal research and automation needs. Tracks upstream changes while maintaining local customizations:

- **Config-driven** — Model routing, skill library, and platform adapters tuned for specific workflows
- **Pruned** — Removed official CI, docs site, legacy release notes, and stress tests for a leaner footprint
- **Upstream-synced** — Regularly merges from `NousResearch/hermes-agent` main branch
- **Research-ready** — Genomics pipeline integration, HPC connectivity, cross-platform context bridging

## Key modifications from upstream

| Area | Change |
|------|--------|
| CI/CD | Removed `.github/workflows/` — not running official CI |
| Docs | Removed `website/` — local deployment doesn't need the docs site |
| Releases | Kept only v0.9+ release notes |
| Tests | Removed `tests/stress/` — upstream also dropped in v0.12 |
| Config | Custom model routing, skill preferences, and platform settings |

## Branch structure

| Branch | Purpose |
|--------|---------|
| `my/main` | Custom branch with our modifications, tracking upstream |
| `main` | Mirrors upstream `NousResearch/hermes-agent` main |

## Sync workflow

```bash
git fetch upstream
git checkout my/main
git merge upstream/main     # resolve conflicts if any
git push myfork my/main
```

## Requirements

Same as upstream — see [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs/getting-started/installation).

## License

MIT — same as [upstream](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE).