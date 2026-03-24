# Fine-Tuning Strategy

> Distilled from tune1–7 (fine-tuning methodology and learning lab design notes)

## Core discipline: what to train on

**Train on**: `(request, final_answer, correction_signal)` tuples

**Never train on**:
- Raw conversation transcripts (too noisy, context-dependent)
- Chain-of-thought verbatim (intermediate reasoning is implementation, not outcome)
- Emotion logs (emotional state is a control signal, not a prediction target)
- Unvalidated dream outputs (epistemic_status="imagined" never enters training)

The goal: teach the sidecar (0.8–4B) to **predict profound outcomes directly** given a request and context.

## The training pipeline

```
Conversation turn
  → Distillery (mind daemon)
      → quality_signal assessment (user_accepted | user_corrected | self_validated)
      → safety gate (7 checks, see below)
      → TrainingPair { request, outcome, context_used, quality_signal }
  → training_buffer table
  → Counterfactual Rehearsal (if corrected)
      → ErrorAtlas: what_said → what_should_have_said → fix_type
  → Learning Lab (apps/trainerd/)
      → LoRA fine-tune on MLX
      → eval gates (holdout must beat baseline)
      → versioned adapter → optional deployment
```

## Seven safety gates

A training pair enters `training_buffer` only if ALL pass:

1. ✅ Speaker confidence > 0.8
2. ✅ Consent scope allows training
3. ✅ No TTS bleed / self-speech contamination
4. ✅ Quality signal is positive (or explicitly corrective — both are useful)
5. ✅ Not emotionally sensitive unless explicitly allowed
6. ✅ Holdout allocation assigned before training begins
7. ✅ Eval on holdout beats baseline before deployment

## Counterfactual rehearsal

On corrections, the distillery generates an **error record**:

```python
@dataclass
class ErrorAtlas:
    what_said: str            # the actual GARY response
    what_should_have_said: str  # inferred correct response
    what_fix_type: str        # memory | policy | skill | adapter
    confidence: float
```

`fix_type` determines which system to fix:
- `memory` → retrieval/context pack issue, not a model issue
- `policy` → initiative or response style issue
- `skill` → factual knowledge gap → training candidate
- `adapter` → domain-specific behavior → LoRA target

## Adapter versioning

Every LoRA adapter is versioned with:
- Training manifest (which pairs, which epochs, which base)
- Eval results on holdout
- Rollback pointer

**Instant rollback**: revert `symlink` to previous adapter directory. Zero downtime.

## Dream temperature curve

Background cognition uses temperature to control creative risk:

```python
def dream_temperature(idle_seconds: float) -> float:
    if idle_seconds < 30:   return 0.0   # no generation
    if idle_seconds < 120:  return 0.2   # calm reflection
    if idle_seconds < 300:  return 0.5   # focused brainstorm
    if idle_seconds < 600:  return 0.75  # deep association
    return min(1.0, 0.75 + (idle_seconds - 600) / 2400)
```

**Speech → cancel all dream tasks → temperature = 0.0.** All dream outputs are quarantined (`epistemic_status="imagined"`) until explicitly validated by a subsequent reasoning pass.

## Cognitive flywheel

```
User asks → 35B answers → User reacts → Mind distills training pair
  → Learning Lab fine-tunes sidecar → Sidecar gets better at:
      predicting outcomes, validating ideas, scoring initiative
  → Better sidecar → better context packs → better 35B answers
  → More positive reactions → more data → cycle continues
```

The 35B reflex LLM stays untouched. It gets smarter **indirectly** through better context delivered by the increasingly capable sidecar.
