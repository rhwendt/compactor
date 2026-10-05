# compactor: project rules

A public Claude Code plugin (Python 3.9+, stdlib only). Read `CONTRIBUTING.md` for the design
rules and `docs/superpowers/specs/` for the spec and verification notes.

## Layout: only `plugins/compactor/` ships

- `plugins/compactor/` is the plugin: `.claude-plugin/plugin.json` and `icon.png`, `hooks/`,
  `bin/`, `skills/`, the `compactor/` Python package, and `LICENSE`. Installs copy this folder
  and nothing else.
- Everything else (tests, docs, CI, scripts, this file) lives at the repo root, outside the
  plugin. Keep it there: the plugin directory's linter flags non-runtime files inside a plugin,
  for example docs that mention `github.com` beside environment reads, or CI files that use
  `GH_TOKEN`.
- The marketplace manifest (`.claude-plugin/marketplace.json`) stays at the repo root, with
  `"source": "./plugins/compactor"`.
- The icon is permanent once submitted to the directory. `scripts/make_icon.py` regenerates
  it.

## Releases are automated: never bump versions by hand

- Releases come from Conventional Commits on `main`, through release-please
  (`.github/workflows/release.yml`). After every merge it keeps a **release PR** open that bumps
  the version and writes the changelog entry. Merging that PR creates the `vX.Y.Z` tag and the
  GitHub release.
- How commit types map to versions while we're below 1.0 (`bump-minor-pre-major`):

  | Commit type | Version bump | Changelog section |
  |---|---|---|
  | `fix:` | patch | Bug Fixes |
  | `feat:` | minor | Features |
  | `feat!:` or `BREAKING CHANGE:` | minor | as its type |
  | `docs:`, `chore:`, `ci:`, `test:`, `refactor:` | none | not listed |

- A README-only change doesn't need a release: the README is read on GitHub, not from the
  install. Ship the change with the next `fix:` or `feat:`.
- Write commit subjects for users: they become changelog lines. Say what changed for someone
  using the plugin, not how you changed it.
- Never edit version numbers or add changelog entries yourself. release-please owns these
  files:
  - `.release-please-manifest.json`, `version.txt`
  - `plugins/compactor/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
  - `plugins/compactor/compactor/__init__.py` and `.github/ISSUE_TEMPLATE/bug_report.yml`, both marked
    `x-release-please-version`
  - new sections of `CHANGELOG.md`

  `tests/test_plugin_files.py` fails if these files disagree.
- **Bump the version for every user-facing fix.** Claude Code caches an installed plugin by
  version, so a fix merged without a release never reaches existing installs.
- **Releases are fully automated.** release-please opens the release PR with the
  `RELEASE_PLEASE_TOKEN` secret, a fine-grained PAT for this repo with Contents and Pull
  requests read/write. Because of that, CI runs on the PR, and the workflow sets it to
  auto-merge once the checks pass. That merge triggers the release run, which tags and
  publishes. The PAT expires; when releases stop, renew it with
  `gh secret set RELEASE_PLEASE_TOKEN`. If the secret is missing, merge the release PR by hand
  with `gh pr merge <n> --merge --admin`.
- **The official plugin directory pins a tag.** Anthropic's listing (`claude-plugins-official`)
  points at a git tag and SHA, so it needs a new release before it can show newer code.

## Git and GitHub

- `main` is protected by the "Protect main" ruleset: a PR is required, the 15 `test (...)` CI
  jobs must pass, and force-pushes and deletion are blocked. Branch, open a PR, and merge with
  `--merge`, which keeps the atomic commits that release-please reads.
- The repo is public. Never put Claude session links (`Claude-Session:`,
  `claude.ai/code/session_...`) in commits, PR text or files.

## Verifying

- To try a checkout, use `claude --plugin-dir plugins/compactor`. Never run
  `claude plugin marketplace add <local checkout>`: marketplaces are global by name, so that
  re-points every install of `compactor` at the checkout, including other projects, and
  removing it again uninstalls them. If it happens anyway, re-add `rhwendt/compactor` and
  reinstall.

- Run `python3 -m unittest discover -s tests -t .`, `claude plugin validate .` and
  `claude plugin validate plugins/compactor` before any PR.
- Claude Code behavior, such as hook payloads, timing or thresholds, is checked live, not
  assumed. Record the evidence in `docs/superpowers/specs/2026-09-29-verification.md`.
