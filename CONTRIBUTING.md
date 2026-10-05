# Contributing

Thanks for helping improve compactor. Bug reports, ideas and pull requests are all welcome.

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Before you start

- **Questions and early ideas** go in [Discussions](https://github.com/rhwendt/compactor/discussions).
- **Bugs** go in an [issue](https://github.com/rhwendt/compactor/issues/new/choose). Include
  `compactor status` and the error log.
- **Security issues** are reported privately; see [SECURITY.md](SECURITY.md).
- For a larger change, open an issue or discussion first so the approach can be agreed before
  you write code.

## Development

compactor is Python 3.9+ with the standard library only. Please don't add dependencies.

```sh
python3 -m unittest discover -s tests -t .   # the full suite
claude plugin validate .                    # the marketplace manifest
claude plugin validate plugins/compactor     # the plugin
```

To try your checkout in a real session, run `claude --plugin-dir plugins/compactor` with
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` set. The README's "Manual smoke test" walks through it.

Design notes live in `docs/superpowers/specs/`: the design spec, and verification notes that
record how Claude Code actually behaves, from observed hook payloads and live runs. If a change
depends on Claude Code behavior, check the verification notes, or add to them.

## Guidelines

- **Hooks fail open.** Any error must let compaction through, never block it.
- **The safety ceiling can't be bypassed.** A hold must never let a session overflow its context.
- **`policy.py` stays pure.** No I/O and no clock reads; every input is a parameter.
- **Agent-facing text lives in `messages.py`**, and every number in it says what it measures.
- **Each behavior change gets a test**, ideally one that fails without the change.

## Pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages, for
  example `fix: ...`, `feat: ...` or `docs: ...`.
- Keep pull requests focused, and describe how you verified the change.
- CI must pass on Linux, macOS and Windows before merging.

## Releases

Releases are automated with [release-please](https://github.com/googleapis/release-please).
Commit types decide the next version: `fix:` gives a patch release and `feat:` a minor one
(while below 1.0). The commit subjects become the changelog, so write them for users.

Don't change version numbers or add changelog entries in a pull request; the release PR does
that. Merging the release PR tags the version and publishes the GitHub release.
