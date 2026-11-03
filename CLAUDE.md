# Alfred · kernel branch

The server core of Alfred. The project is split across branches that are never merged:
`kernel` (this: server), `web` (interface, fetched into ./web by `make web`), `android`,
`linux`, `windows` (clients), `docs` (BRIEF.md with milestone Status, PLAN.md, API.md,
DEVICES.md, MILESTONES.md), and `master` (README linking them all).

Requirements and progress: `docs` branch → BRIEF.md (update its Status section there).
Work one milestone at a time; plan changes that affect behaviour need the owner's approval.
Fully local by owner decision: Gemma 3 12B via Ollama, Whisper, Piper; no API keys.

Secrets live only in `.env` (git-ignored). Never print, log, commit, or echo them.
