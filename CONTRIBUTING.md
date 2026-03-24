# Contributing to NeverHuman

Thank you for your interest in contributing to GARY! This project is built to last, and we hold contributions to a high standard to keep it that way.

---

## Quick start

```bash
git clone https://github.com/neverhuman/neverhuman.git
cd neverhuman
python3 -m venv gary/.venv && source gary/.venv/bin/activate
pip install -e ".[dev]"
cd gary && pytest testing/ -q
```

All 159 tests must pass before submitting a PR.

---

## Naming rules (strictly enforced)

> **Never use `jarvis` in any code, filename, variable, log string, or comment.**
> Use `gary` (the AI's name) or `neverhuman` (the project name).
>
> `JARVIS` appears only in `README.md` as origin-story context. Nowhere else.

---

## Branch strategy

- `main` — stable, always passing CI
- `dev` — integration branch for feature PRs
- Feature branches: `feat/<short-description>`
- Bug fixes: `fix/<short-description>`

---

## PR requirements

- [ ] All tests pass (`pytest gary/testing/ -q`)
- [ ] No new `jarvis` strings (CI checks this)
- [ ] Code follows existing style (ruff clean)
- [ ] New behavior includes tests
- [ ] Description explains *why*, not just *what*

---

## What we especially want

- 🔊 **Voice quality improvements** — VAD tuning, TTS voice additions
- 🧠 **Mind daemon isolation** — moving `mind_loop` to `apps/mindd/`
- 💾 **Memory improvements** — retrieval quality, schema evolution
- 🔌 **Connectors** — calendar, browser, file system
- 🧪 **Test coverage** — especially integration tests
- 📖 **Documentation** — architecture clarity, tutorials

## What we won't accept

- Cloud sync, telemetry, or analytics (without explicit community vote)
- Breaking changes to the voice pipeline latency budget
- New dependencies without justification
- Code that introduces `JARVIS` naming

---

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(pipeline): add Silero VAD v5 as upgrade path
fix(server): reset is_speaking on WebSocket disconnect
docs(arch): update §26 turn classifier table
```

---

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
