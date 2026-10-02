# Security policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub's private vulnerability reporting](https://github.com/rhwendt/compactor/security/advisories/new),
not in a public issue or discussion.

Include what an attacker controls, what they gain, and steps to reproduce. You'll get a reply
within a week. Once a fix is released, the advisory is published with credit to you unless you
ask otherwise.

## Scope

compactor runs as Claude Code hooks and a CLI with your user's permissions. It reads Claude
Code transcripts and writes state under `${XDG_STATE_HOME:-~/.local/state}/claude-compactor/`.
Issues in scope include, for example:

- reading or writing files outside that state directory
- running commands, or code from transcript or tool output
- a way to make a hold block compaction past the safety ceiling

Prompt injection that leads an agent to set or release its own hold is ordinary agent
behavior, not a vulnerability in compactor.

## Supported versions

Only the latest release receives fixes.
