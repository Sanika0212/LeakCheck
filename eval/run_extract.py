"""How well does each extractor read messages and crew notes?

    python eval/run_extract.py

Labels every generated message with both extractors (the LLM answers come from
``data/llm_cache.jsonl`` when they were already computed) and compares them with what the
generator wrote. Reported separately for the dev businesses (phrasings the rules and
prompts were written against) and the test businesses (held-out phrasings).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from common import prf, read_jsonl, test_businesses, write

from leakcheck.extract import LLMExtractor, RuleExtractor

# What the right label is for each kind of message the generator writes.
EXPECTED = {
    "request": "other",
    "scheduling": "other",
    "question": "other",
    "thanks": "other",
    "price_inquiry": "price_question",
    "cancel_extra": "cancel_extra",
    "extra_request": "extra_request",
    "accept": "accept",
    "decline": "decline",
    "concession": "concession",
}
FOCUS = ["extra_request", "price_question", "cancel_extra", "accept", "decline", "concession"]


def main() -> None:
    test = test_businesses()
    kinds = {m["message_id"]: m["kind"] for m in read_jsonl("message_truth")}
    messages = read_jsonl("messages")
    visits = [v for v in read_jsonl("visits") if v.get("note")]
    result = {}
    for ex in (RuleExtractor(), LLMExtractor()):
        by_split: dict[str, dict] = {}
        for split in ("dev", "test"):
            rows = [m for m in messages if (m["business_id"] in test) == (split == "test")]
            counts: dict[str, Counter[str]] = defaultdict(Counter)
            correct = 0
            for m in rows:
                want = EXPECTED[kinds[m["message_id"]]]
                got, _, _ = ex.message(m["body"], m["direction"])
                correct += got == want
                for label in FOCUS:
                    if got == label and want == label:
                        counts[label]["tp"] += 1
                    elif got == label:
                        counts[label]["fp"] += 1
                    elif want == label:
                        counts[label]["fn"] += 1
            notes = [v for v in visits if (v["business_id"] in test) == (split == "test")]
            nc = Counter()
            for v in notes:
                _, _, no_charge = ex.note(v["note"])
                want = bool(v.get("no_charge"))
                nc[
                    "tp" if no_charge and want else "fp" if no_charge else "fn" if want else "tn"
                ] += 1
            by_split[split] = {
                "messages": len(rows),
                "accuracy": round(correct / len(rows), 3),
                "per_label": {k: prf(c["tp"], c["fp"], c["fn"]) for k, c in counts.items()},
                "no_charge_notes": prf(nc["tp"], nc["fp"], nc["fn"]),
            }
            print(
                ex.name,
                split,
                by_split[split]["accuracy"],
                {k: v["f1"] for k, v in by_split[split]["per_label"].items()},
                "no-charge F1",
                by_split[split]["no_charge_notes"]["f1"],
                flush=True,
            )
        result[ex.name] = by_split
    print("wrote", write("extract", result))


if __name__ == "__main__":
    main()
