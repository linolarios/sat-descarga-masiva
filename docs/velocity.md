# Velocity & effort tracking (AI-assisted build)

> Calibration start, not a promise. Re-baseline after the first real piece of
> M2 (CSF onboarding + `ContributorProfile` + the three-way RFC check) and
> rescale the rest from that measured number.

## Context

- M1 was built in ~3 days, ~1–2 h/day with one 5 h push ≈ **~8 h total**.
- That was **AI-assisted**: most code was generated; your contribution is the
  design decisions, review, applying/committing, and refactor churn.
- M1's live SAT test is still **unverified** (needs a FIEL de pruebas).

## Milestone effort sheet (AI-assisted hours)

| Milestone | Focus | Core | Overhead | **Total** | Weight |
|---|---|---:|---:|---:|---:|
| M1 DOWNLOAD | SAT round-trip, source store, extract, ingest, ledger, facade, live test | ~6 | incl. | **8** ⚑ | ~15% |
| M2 PROCESS/parse | CSF, ContributorProfile, 3-way RFC identity, perspective, sello, fiscal_events | 10 | +40% | **14** | ~24% |
| M3 ACCOUNTING | rules 4.1–4.15, PostingEligibilityValidator, journal/posting/review schemas | 14 | +40% | **20** | ~33% |
| M4 EXCEL | ledger projection → xlsx (9 sheets) | 6 | +35% | **8** | ~13% |
| M5 DIOT | DIOT projection → SAT bulk-load txt | 6 | +35% | **8** | ~13% |
| M6 CLI+Settings | two flows, ConfigEditor, logging | 4 | +35% | **5** | ~8% |
| **TOTAL** | | ~46 | | **~63h** | 100% |

⚑ M1 is the observed figure and already includes review cycles, so no extra
overhead is added on top.

## Contingency (bugs, reviews, refactors, integration, verification)

- Overhead is already folded into the per-milestone column above (35–40%).
- Add **+10–15%** on top for late-milestone integration and the unverified live
  SAT run → **~70h** realistic total.
- Recommended project range: **~45–70h** AI-assisted.

## Calendar view (using ~70h with contingency)

| Daily hours | Total weeks | Notes |
|---|---|---|
| ~2 h/day | ~7 weeks | realistic side-project pace |
| ~4 h/day | ~3.5 weeks | focused part-time |
| ~8 h/day | ~9 days | full-time sprint |

## Your personal time

AI generates most code; you still own design, review, apply/commit, refactor
churn, and live verification. Estimate **~30–40%** of total hours → roughly
**20–28h of your own focused effort** spread across the calendar span.

## Track-actual-hours checklist

- [ ] M2 piece 1 (CSF + ContributorProfile + RFC check): log start/end times.
- [ ] Compare to the ~4h M2 core estimate; recompute the 35–40% overhead.
- [ ] Rescale M2–M6 from the measured number (don't trust this table blind).
- [ ] Record a separate line for **your** review/decision time vs AI generation.
- [ ] After M3 piece 1, sanity-check the 33% weight on accounting.
- [ ] Update this file each milestone with actual vs estimated hours.

## When estimates are invalid

- Any change to the design/architecture invalidates prior estimates → re-baseline.
- The AI-assistance multiplier shrinks in M2/M3 (more domain/accounting
  judgment, less boilerplate) → those take relatively more of *your* time.
- The unverified live SAT path may surface adapter fixes at M1 close.
