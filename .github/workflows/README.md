# GitHub Actions in the server fork

This fork keeps only workflows that build, test, release, or validate the server/runtime and supported desktop/install artifacts. The upstream documentation website, upstream Canary automation, and upstream catalog publishing/monitoring workflows are intentionally absent.

## CI entry point

`ci.yaml` is the PR, `my/main`, manual, and reusable orchestrator. It uses `.github/actions/detect-changes` and `scripts/ci/classify_changes.py` to select these reusable lanes:

- `tests.yml`, `tests-os.yml`
- `lint.yml`, `js-tests.yml`, `rust-tests.yml`
- `remote-backends.yml`, `bootstrap-installer.yml`
- `e2e-desktop-core.yml`, `e2e-desktop-update.yml`
- `repo-guards.yml`

E2E lanes are disabled for ordinary PR and push events. They run only for an explicit release call or manual dispatch. `e2e-desktop.yml` remains wired with a literal `if: false` because that suite is still deferred as flaky.

All retained workflows use GitHub-hosted runners. The fork does not depend on Blacksmith runners.

## Standalone and path-filtered checks

- `docker.yml`
- `nix.yml`
- `pm-bundle.yml`
- `termux-verify.yml`
- `sandbox-image.yml`
- `windows-bundle-sdk.yml`
- `windows-install-update-e2e.yml`
- `windows-desktop-attach-e2e.yml`
- `windows-venv-e2e.yml`
- `osv-scanner.yml`
- `live-providers.yml`
- `archive-inputs.yml`
- `js-autofix.yml`
- `label-rerun.yml`
- `ci-review-comment.yml`

Linux desktop bundle legs in `desktop-bundled-release.yml` remain disabled; the hosted Ubuntu image does not satisfy the prebuilt `llama-server` GLIBC requirement.

## Stable release and installer workflows

- `stable-release.yml`
- `stable-release-publication.yml`
- `desktop-bundled-release.yml`
- `desktop-bundle-smoke.yml`
- `bootstrap-installer-build.yml`
- `install-e2e.yml`
- `install-e2e-run.yml`
- `install-e2e-windows-run.yml`
- `install-e2e-macos-run.yml`

Stable releases are manually admitted through `scripts/release.py`; release claims decide which lanes run. `stable-release.yml` is the controller, and `stable-release-publication.yml` reconciles or publishes accepted candidates.

## Fork-specific routing

- The protected/default integration branch is `my/main` where a workflow needs the fork branch explicitly.
- Pull-request checks resolve and test the exact PR head SHA.
- Server-fork mode prunes website-only and upstream-only jobs rather than reporting them as required.
- `ci.yaml` preserves the fork runtime, approval, gateway, notification, and release behavior while using the upstream orchestrator and manifest checks.

## Intentionally removed upstream automation

The following workflow families are not part of this fork:

- documentation website deployment, search monitoring, and site checks;
- skills/model/plugin catalog publishing, freshness checks, and catalog-only CI;
- upstream Canary release/E2E and its issue tracker;
- official catalog dead-repository checks.

Do not reintroduce callers, classifier outputs, required-result entries, or tests that expect those workflows unless the fork adopts the corresponding service again.
