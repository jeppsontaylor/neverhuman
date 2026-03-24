# Privacy Policy

## Your data belongs to you

NeverHuman (GARY) is designed from the ground up for complete personal data sovereignty.

---

## What we collect

**Nothing.** We have no servers. We have no accounts. We receive no data.

---

## What stays on your machine

Everything:

| Data | Where it lives |
|------|---------------|
| Voice audio | RAM only, discarded after transcription |
| Conversation transcripts | Local Postgres (Docker) |
| Memories | Local Postgres (Docker) |
| Affect / emotional state | RAM, optionally persisted to local Postgres |
| Model weights | `~/.cache/huggingface/hub/` |
| Runtime data | `~/.neverhuman/` |
| Session logs | `gary/logs/` |

Your microphone audio is **never written to disk**. Float32 PCM frames are processed in RAM and discarded after ASR transcription.

---

## External services used

| Service | When | What's sent |
|---------|------|-------------|
| HuggingFace Hub | First-run model download | Model filenames (no personal data) |
| Google Fonts | Page load | Your IP address (can be avoided by self-hosting fonts) |

Both are optional and can be eliminated by downloading models manually and hosting fonts locally.

---

## Data deletion

To permanently delete all GARY data:
```bash
# Delete conversation history and memories
docker volume rm neverhuman_pgdata

# Delete logs
rm -rf gary/logs/

# Delete model weights
rm -rf ~/.cache/huggingface/hub/models--Qwen*
rm -rf ~/.cache/huggingface/hub/models--hexgrad*
rm -rf ~/.cache/huggingface/hub/models--mlx-community*

# Delete runtime data
rm -rf ~/.neverhuman/
```

Uninstalling Docker Desktop removes the Postgres volume entirely.

---

## Children's privacy

NeverHuman is not designed for children under 13. We collect no data from anyone, including children.

---

## Changes to this policy

Changes require the same governance process as any major decision. See [GOVERNANCE.md](GOVERNANCE.md).
