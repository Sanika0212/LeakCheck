# Proof of concept: success criteria

The question the proof of concept answers: is there enough unbilled money in a small
painting contractor's own records, findable from the data alone, to justify building the
pipeline?

It runs on raw exports (the JSON a field-service system can export), with one DuckDB
script and keyword rules: no streaming, no warehouse, no model. It is scored on the three
held-out generated businesses, where the planted leakage is known.

| # | Criterion | Why it matters | Bar |
|---|---|---|---|
| 1 | Share of leaked dollars found | Worth an owner's time only if most of the money is found | at least 70% |
| 2 | Better than the report owners already get | "Completed jobs with no invoice" is what most tools show | at least 1.5 times its dollar recall |
| 3 | Precision of the list | An owner stops reading a list that is mostly wrong | at least 80% of flagged jobs are real |
| 4 | Speed | Must run on an office laptop | six months of data in under a minute |

**Go** if all four hold. **No-go** if 1 or 3 fails: the money is not findable from records,
or the list would be too noisy to act on.
