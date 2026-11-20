"""Read customer messages and crew notes: who asked for extra work, and was it done?

Two extractors write the same tables (``signals.message_labels``, ``signals.note_labels``):

1. :class:`RuleExtractor`: keywords plus the price book's item names. Fast and free, but it
   reads "how much would the fence be?" or "skip the shed" as requests unless the rules
   catch them, and misses requests that never say "also".
2. :class:`LLMExtractor`: a local model through Ollama (qwen2.5:7b by default), asked for
   JSON. Results are cached by prompt in ``data/llm_cache.jsonl``, so a rerun costs nothing.

Message labels: ``extra_request``, ``cancel_extra``, ``price_question``, ``accept``,
``decline``, ``concession`` (the contractor promises a credit, with its amount), ``other``.
Note labels: extra work done (and which item), and no-charge work (callbacks, warranty).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

import duckdb

LABELS = [
    "extra_request",
    "cancel_extra",
    "price_question",
    "accept",
    "decline",
    "concession",
    "other",
]
MessageLabel = tuple[str, str | None, float | None]  # label, item, amount
NoteLabel = tuple[bool, str | None, bool]  # extra work done, item, no charge
ITEMS = [
    "garage door",
    "front door",
    "hallway trim",
    "bathroom ceiling",
    "back porch railing",
    "shed",
    "fence section",
    "closet interiors",
    "window shutters",
    "basement stairs",
    "accent wall",
    "mailbox post",
]


def find_item(text: str | None) -> str | None:
    """The price-book item a text names, matched loosely."""
    if not text:
        return None
    t = text.lower()
    for item in ITEMS:
        if item in t:
            return item
    for item in ITEMS:  # a distinctive word is enough ("the shutters", "the railing")
        if any(w in t for w in item.split() if len(w) > 4 and w not in ("section", "interiors")):
            return item
    close = difflib.get_close_matches(t, ITEMS, n=1, cutoff=0.6)
    return close[0] if close else None


def find_amount(text: str | None) -> float | None:
    m = re.search(r"\$\s?([\d,]+(?:\.\d{1,2})?)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


class Extractor(Protocol):
    name: str

    def message(self, body: str, direction: str) -> MessageLabel: ...

    def note(self, text: str) -> NoteLabel: ...


class RuleExtractor:
    name = "rules"
    REQUEST = re.compile(r"\b(also|as well|too|add|while you'?re here)\b", re.I)
    PRICE = re.compile(r"\b(how much|price|ballpark|cost|budget)\b", re.I)
    CANCEL = re.compile(r"\b(skip|never mind|hold off|cancel)\b", re.I)
    ACCEPT = re.compile(r"\b(sure|no problem|got it|yes we can|adding|we'?ll take care)\b", re.I)
    DECLINE = re.compile(r"\b(sorry|can'?t|pass on|separate quote|different crew)\b", re.I)
    DONE = re.compile(
        r"\b(also did|as well|plus the|incl\.?|per the customer|customer asked)\b", re.I
    )
    CREDIT = re.compile(r"\$\s?[\d,]+.{0,25}\b(off|credit)\b", re.I)
    NO_CHARGE = re.compile(r"\b(no charge|not billable|warranty|on us|callback)\b", re.I)

    def message(self, body: str, direction: str) -> MessageLabel:
        item = find_item(body)
        if direction == "outbound":
            if self.CREDIT.search(body):
                return "concession", None, find_amount(body)
            if self.DECLINE.search(body):
                return "decline", item, None
            return ("accept" if self.ACCEPT.search(body) else "other"), item, None
        if item and self.CANCEL.search(body):
            return "cancel_extra", item, None
        if item and self.PRICE.search(body):
            return "price_question", item, None
        if item and self.REQUEST.search(body):
            return "extra_request", item, None
        return "other", item, None

    def note(self, text: str) -> NoteLabel:
        item = find_item(text)
        no_charge = bool(text and self.NO_CHARGE.search(text))
        return bool(text and self.DONE.search(text) and item and not no_charge), item, no_charge


PROMPT_MESSAGE = """You read messages between a painting contractor and a customer during a job.
Classify the message into exactly one label:
- extra_request: the customer asks the crew to do additional work beyond the job (explicitly or implicitly, e.g. "go ahead with the shed", "could the crew take care of the door").
- cancel_extra: the customer withdraws or postpones a request for additional work.
- price_question: the customer only asks what extra work would cost, without asking for it to be done now.
- accept: the contractor agrees to do additional work.
- decline: the contractor says they will not do the additional work now.
- concession: the contractor promises a price reduction or credit on the bill.
- other: anything else (scheduling, questions, thanks, the original job request).
Also give the extra item named, if any (e.g. "garage door"), else null, and for a
concession the dollar amount as a number, else null.
Answer with JSON only: {{"label": "...", "item": "..." or null, "amount": number or null}}

Direction: {direction} ({who})
Message: {body}"""

PROMPT_NOTE = """A painter's end-of-day note. Does it say the crew did additional work beyond the
original job, for the customer (for example "also did the garage door")? And is the visit
work the customer should not be charged for (a callback or warranty fix of the crew's own
work)? Answer with JSON only:
{{"extra_done": true or false, "item": "..." or null, "no_charge": true or false}}

Note: {text}"""


class LLMExtractor:
    def __init__(
        self,
        model: str = "qwen2.5:7b",
        cache: Path = Path("data/llm_cache.jsonl"),
        host: str | None = None,
    ) -> None:
        import ollama

        self.name = f"llm:{model}"
        self.model = model
        self.client = ollama.Client(host=host) if host else ollama.Client()
        self.cache_path = cache
        self.cache: dict[str, dict[str, Any]] = {}
        if cache.exists():
            for line in cache.read_text().splitlines():
                row = json.loads(line)
                self.cache[row["key"]] = row["value"]

    def _ask(self, prompt: str) -> dict[str, Any]:
        key = hashlib.sha256(f"{self.model}\n{prompt}".encode()).hexdigest()
        if key in self.cache:
            return self.cache[key]
        resp = self.client.generate(
            model=self.model,
            prompt=prompt,
            format="json",
            options={"temperature": 0, "num_predict": 60},
        )
        try:
            value: dict[str, Any] = json.loads(resp["response"])
        except json.JSONDecodeError:
            value = {}
        self.cache[key] = value
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a") as fh:
            fh.write(json.dumps({"key": key, "value": value}) + "\n")
        return value

    def message(self, body: str, direction: str) -> MessageLabel:
        who = "contractor to customer" if direction == "outbound" else "customer to contractor"
        out = self._ask(PROMPT_MESSAGE.format(direction=direction, who=who, body=body))
        label = str(out.get("label", "other"))
        label = label if label in LABELS else "other"
        item = find_item(out.get("item")) or find_item(body)
        amount = out.get("amount")
        try:
            value = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            value = find_amount(str(amount))
        return label, item, value if label == "concession" else None

    def note(self, text: str) -> NoteLabel:
        if not text:
            return False, None, False
        out = self._ask(PROMPT_NOTE.format(text=text))
        no_charge = bool(out.get("no_charge"))
        done = bool(out.get("extra_done")) and not no_charge
        return done, find_item(out.get("item")) or find_item(text), no_charge


def run(
    con: duckdb.DuckDBPyConnection, extractor: Extractor, progress: bool = False
) -> dict[str, int]:
    """Label every message and crew note in silver and write the signals tables."""
    msgs = con.execute(
        "select message_id, job_id, business_id, direction, sent_at, body from silver.stg_messages"
    ).fetchall()
    notes = con.execute(
        "select visit_id, job_id, business_id, note from silver.stg_visits where coalesce(note, '') <> ''"
    ).fetchall()
    m_rows, n_rows = [], []
    for i, (mid, job, biz, direction, sent_at, body) in enumerate(msgs, start=1):
        label, item, amount = extractor.message(body, direction)
        m_rows.append((mid, job, biz, direction, sent_at, label, item, amount, extractor.name))
        if progress and i % 250 == 0:
            print(f"  {i}/{len(msgs)} messages", flush=True)
    for vid, job, biz, text in notes:
        done, item, no_charge = extractor.note(text)
        n_rows.append((vid, job, biz, done, item, no_charge, extractor.name))
    con.execute("create schema if not exists signals")
    con.execute("""create or replace table signals.message_labels (message_id varchar, job_id varchar,
        business_id varchar, direction varchar, sent_at timestamp, label varchar, item varchar,
        amount double, method varchar)""")
    con.execute("""create or replace table signals.note_labels (visit_id varchar, job_id varchar,
        business_id varchar, mentions_extra boolean, item varchar, no_charge boolean, method varchar)""")
    if m_rows:
        con.executemany(
            "insert into signals.message_labels values (?, ?, ?, ?, ?, ?, ?, ?, ?)", m_rows
        )
    if n_rows:
        con.executemany("insert into signals.note_labels values (?, ?, ?, ?, ?, ?, ?)", n_rows)
    return {"messages": len(m_rows), "notes": len(n_rows)}


def labels_for(rows: Iterable[tuple[str, str]], extractor: Extractor) -> list[MessageLabel]:
    return [extractor.message(body, direction) for body, direction in rows]
