# compactor: project rules

A public Claude Code plugin (Python 3.9+, stdlib only). Read `CONTRIBUTING.md` for the design
rules and `docs/superpowers/specs/` for the spec and verification notes.

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
  - `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
  - `compactor/__init__.py` and `.github/ISSUE_TEMPLATE/bug_report.yml`, both marked
    `x-release-please-version`
  - new sections of `CHANGELOG.md`

  `tests/test_plugin_files.py` fails if these files disagree.
- **Bump the version for every user-facing fix.** Claude Code caches an installed plugin by
  version, so a fix merged without a release never reaches existing installs.
- **CI doesn't run on release PRs.** release-please opens them with the default
  `GITHUB_TOKEN`, so the required checks never report, and the PR has to be merged with
  `gh pr merge <n> --merge --admin`. It only changes version files and the changelog. Adding a
  `RELEASE_PLEASE_TOKEN` secret (a fine-grained PAT) would let CI run on them.
- **The official plugin directory pins a tag.** Anthropic's listing (`claude-plugins-official`)
  points at a git tag and SHA, so it needs a new release before it can show newer code.

## Git and GitHub

- `main` is protected by the "Protect main" ruleset: a PR is required, the 15 `test (...)` CI
  jobs must pass, and force-pushes and deletion are blocked. Branch, open a PR, and merge with
  `--merge`, which keeps the atomic commits that release-please reads.
- The repo is public. Never put Claude session links (`Claude-Session:`,
  `claude.ai/code/session_...`) in commits, PR text or files.

## Verifying

- Run `python3 -m unittest discover -s tests` and `claude plugin validate .` before any PR.
- Claude Code behavior, such as hook payloads, timing or thresholds, is checked live, not
  assumed. Record the evidence in `docs/superpowers/specs/2026-09-29-verification.md`.
