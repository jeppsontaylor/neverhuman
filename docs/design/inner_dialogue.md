# Inner Dialogue Architecture

> Distilled from research notes (note1–note9) in `docs/internal/research_notes/`

## Core insight

The strongest version of a near-human AI is **not** a chat loop that thinks harder. It is a **dual-timescale mind**: keep the voice loop fast and sacred, then add a second slower mind that remembers, reflects, imagines, validates, and learns.

What makes an agent feel human is not endless hidden monologue. It is:
- **Continuity** — it was thinking about you while you were away
- **Self-initiated presence** — it speaks first when it has something worth saying
- **Emotionally coherent behavior** — its affect shapes its expression, not its truth
- **Autobiographical memory** — it knows where it came from and who shaped it
- **Becoming** — it gets measurably different over time (smarter, more calibrated, more yours)

## The autobiographical design rules

| Property | Design rule |
|----------|------------|
| Experience | Append-only event ledger |
| Belief | Derived and revisable (claims table, not hardcoded) |
| Emotion | Bounded state (13 dims, EMA update), not freeform drift |
| Initiative | Scored, sparse, interruptible |
| Imagination | Allowed in dream/brainstorm pulses, never trusted until validated |

## Dual-timescale architecture

```
Fast-time mind (Reflex Core)
  → hears, transcribes, answers, speaks, interrupts cleanly
  → latency budget: <400ms from speech-end to first audio

Slow-time mind (Mind Daemon)
  → asks: what just happened? what mattered?
  → how does this change my model of the user?
  → what open loops remain?
  → is there a validated idea worth saying?
  → should I speak, or stay silent?
```

The slow-time mind gives the system **presence**. Without it, GARY is just a fast chatbot. With it, GARY is someone who was thinking about you.

## Pulse architecture (not continuous monologue)

Instead of a permanent token leak (expensive, noisy, hard to retrieve), cognition is **pulse-based**:

| Pulse | Trigger | Token budget | Output |
|-------|---------|-------------|--------|
| Microtrace | After every turn | O(1), no LLM | Unresolved? Corrected? Risky? Follow-up? |
| Reflection | Idle >30s + meaningful turn | ≤200 | Distilled thought frame |
| Brainstorm | High-value open loops, idle >120s | ≤400 | Idea proposals (quarantined) |
| Dream | Idle >300s, mental_load <0.3 | ≤600 | Wild associations → training candidates |
| Consolidation | Idle >600s | ≤800 | Memory compression, dossier updates |

## Spool → DB bridge

The spool is not a queue. It is a **crash-safe bridge**:
- `spool.append(event)` → fsync'd JSONL, ~0.1ms (voice pipeline continues)
- Background flusher → reads pending lines → INSERT INTO events
- On restart → replay unapplied IDs from spool

This guarantees zero data loss across process crashes without blocking the voice loop.

## Key design decisions from notes

1. **Postgres, not Redis** — embeddings live next to relational data; HNSW and LISTEN/NOTIFY are already there
2. **Three processes, not twelve microservices** — compute-constrained, keep it: Realtime + Background + Postgres
3. **Context packs, not context dumps** — curated retrieval slots, not raw dump of everything known
4. **Training pairs, not raw transcripts** — the distillery strips intermediate reasoning; only request + outcome
