# Support

## Getting help

### 📖 Read first

1. **[README.md](README.md)** — installation and quick start
2. **[gary/ARCHITECTURE.md](gary/ARCHITECTURE.md)** — full system deep-dive
3. **[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — common issues

### 🐛 Found a bug?

[Open a bug report](.github/ISSUE_TEMPLATE/bug_report.md) — include:
- macOS version and chip (M1/M2/M3/M4)
- `bash install.sh` output or `pytest gary/testing/ -q` output
- What you expected vs. what happened

### 💡 Feature idea?

[Open a feature request](.github/ISSUE_TEMPLATE/feature_request.md)

### 💬 General discussion

Use [GitHub Discussions](https://github.com/jeppsontaylor/neverhuman/discussions) for:
- Questions about setup
- Ideas that aren't quite feature requests
- Sharing what you've built with GARY

### ⚠️ Security issues

See [SECURITY.md](SECURITY.md) — do not open public issues for vulnerabilities.

---

## What we don't support

- Windows or Linux (Apple Silicon required; community ports welcome)
- Running GARY in the cloud (by design — it's local-only)
- Modifications to the voice pipeline that increase latency above 400ms
