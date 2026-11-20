# Proof of concept: report

Scored on the three held-out generated businesses against [criteria.md](criteria.md).

| Criterion | Result | Met |
|---|---|---|
| Share of leaked dollars found | 93.0% | yes |
| Times the status-only report | 1.438x (64.7% for status only) | no |
| Precision of flagged jobs | 68.9% | no |
| Time for six months of data | 0.13 s | yes |

**Decision: no-go for a keyword-rules product.** The money is there (most leaked dollars were found), but the list is too noisy to hand an owner. False positives: 20 unapproved discount, 11 unbilled hours, 2 unbilled extra work. They are records the rules could not read: no-charge callbacks and promised credits worded differently from the messages the rules were written against. The next step is reading the text with a language model and scoring again against the same bar.

What the proof of concept could not answer, and the pipeline has to:

1. Whether keyword rules survive real phrasing (they were written against the dev businesses' messages; the held-out businesses word things differently).
2. Whether findings can be fresh (the proof of concept reads a one-off export).
3. Whether an owner will act on them (drafts, approval, an audit trail).
