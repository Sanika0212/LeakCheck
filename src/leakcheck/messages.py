"""Customer messages and crew notes, written so that finding extra work is not a keyword hunt.

Requests for extra work come explicit ("can you also paint the garage door"), implicit
("the front door looks tired next to the new trim, could the crew take care of it") or
buried in small talk. The traps are messages that mention the same things without asking
for work: price questions, cancellations, compliments that name a room, scheduling notes.
All of it is template-based and synthetic; real customer messages are messier.

There are two sets of phrasings. Set A is used by the three dev businesses; the rules and
prompts were written against it. Set B, written after the rules were frozen and in
different words, is used only by the three held-out test businesses, so the evaluation
measures how the extractors cope with phrasing they have not seen.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Extra items customers ask for, with the range of hours each adds.
EXTRA_ITEMS: dict[str, tuple[float, float]] = {
    "garage door": (2.5, 6.0),
    "front door": (1.5, 3.5),
    "hallway trim": (3.0, 8.0),
    "bathroom ceiling": (2.0, 5.0),
    "back porch railing": (3.0, 7.0),
    "shed": (5.0, 12.0),
    "fence section": (4.0, 10.0),
    "closet interiors": (2.0, 6.0),
    "window shutters": (3.0, 7.0),
    "basement stairs": (2.0, 5.0),
    "accent wall": (2.0, 4.5),
    "mailbox post": (1.0, 2.0),
}

FIRST = [
    "Maria",
    "James",
    "Priya",
    "Tom",
    "Ngozi",
    "Hiro",
    "Lena",
    "Mateo",
    "Ingrid",
    "Kwame",
    "Rosa",
    "Dmitri",
    "Ana",
    "Yusuf",
    "Beth",
    "Omar",
    "Grace",
    "Luis",
    "Mei",
    "Paul",
]
LAST = [
    "Gonzalez",
    "Okafor",
    "Lindqvist",
    "Hendricks",
    "Nakamura",
    "Wojcik",
    "Abernathy",
    "Castillo",
    "Mbeki",
    "Raghavan",
    "Fitzgerald",
    "Petrovic",
    "Adeyemi",
    "Vasquez",
    "Kowalski",
    "Thorne",
    "Haddad",
    "Brennan",
    "Delacroix",
    "Szabo",
]
STREETS = [
    "Elm",
    "Maple",
    "Linwood",
    "Hertel",
    "Delaware",
    "Bailey",
    "Kenmore",
    "Niagara",
    "Parkside",
    "Richmond",
]

GREET = ["Hi", "Hello", "Hey", "Good morning", "Hi there", "", "Hi again"]
SIGN = ["Thanks!", "Thank you", "thx", "Best,", "", "Cheers", "Appreciate it"]

REQUEST_OPEN = {
    "interior": [
        "Looking to get {rooms} rooms repainted, walls and ceilings.",
        "We need the interior painted, about {rooms} rooms.",
        "Could you quote painting {rooms} rooms in our house?",
    ],
    "exterior": [
        "Our house needs the exterior painted, it's a {stories}-story.",
        "Looking for a quote on exterior paint, siding and trim.",
        "The outside of the house is peeling, can you take a look?",
    ],
    "cabinets": [
        "Interested in having our kitchen cabinets refinished ({doors} doors).",
        "Can you paint kitchen cabinets? About {doors} doors and drawers.",
    ],
    "deck": [
        "Need our deck stained before summer.",
        "Can you quote staining a deck, roughly {area} sq ft?",
    ],
    "commercial": [
        "We manage an office suite and need it repainted, around {area} sq ft.",
        "Requesting a bid for repainting a retail space.",
    ],
}

EXPLICIT = [
    "Can you also paint the {item} while the crew is here?",
    "Would it be possible to add the {item} to the job?",
    "Please add the {item} too, we'd like it done this week.",
    "Could you guys do the {item} as well?",
    "One more thing: can you take care of the {item}?",
    "we'd love it if you could also do the {item}",
]
IMPLICIT = [
    "The {item} looks really tired next to the fresh paint. Could the crew take care of it before they leave?",
    "Now that everything else looks great, the {item} kind of sticks out. Happy to have your team do it.",
    "My husband says if you're already set up, go ahead with the {item}.",
    "While you're here, the {item} could use a coat too. Just go ahead.",
    "Since the color matches anyway, let's have the {item} done as well.",
]
SMALL_TALK = [
    "The kids love the new color.",
    "Sorry for the dog barking this morning.",
    "We'll be out Thursday but the side door is open.",
    "Your crew has been great so far.",
    "Saw the forecast, hope the rain holds off.",
]

NOISE = {
    "scheduling": [
        "Can the crew start an hour later on Tuesday?",
        "We'll be away Friday, is it ok if you work while we're gone?",
        "What time should we expect the crew tomorrow?",
        "Could we push the start to next week? Family visiting.",
    ],
    "question": [
        "What color did we pick for the trim again?",
        "Do we need to move the furniture out of the {room} ourselves?",
        "How long should the paint dry before we hang pictures?",
        "Is the paint low-VOC? Asking because of the baby.",
    ],
    "thanks": [
        "The {room} looks amazing, thank you!",
        "Just walked through the {room}, really happy with it.",
        "Great job on the trim, the {room} feels brand new.",
    ],
    "price_inquiry": [
        "Out of curiosity, how much would it cost to do the {item} someday?",
        "Not now, but could you send a price for the {item} for next spring?",
        "Ballpark for the {item}? Just budgeting for next year.",
    ],
    "cancel_extra": [
        "Actually, skip the {item}, we'll leave it for another year.",
        "Never mind about the {item}, my brother-in-law says he'll do it.",
        "Let's hold off on the {item} after all.",
    ],
}
ROOMS = ["living room", "kitchen", "hallway", "bedroom", "office", "dining room", "entry"]

ACCEPT = [
    "Sure, we'll take care of the {item} this week.",
    "No problem, the crew will do the {item} before wrapping up.",
    "Got it, adding the {item}.",
    "Yes we can do the {item}, we'll fit it in tomorrow.",
]
DECLINE = [
    "Sorry, we can't fit the {item} in this time, but happy to quote it separately.",
    "We'll pass on the {item} for now, the schedule is full.",
    "That one needs a different crew; we'll send a separate quote for the {item}.",
]
CLOSING = [
    "Final coat done, cleaned up and walked through with the customer.",
    "Touch-ups finished. Drop cloths removed. Job complete.",
    "Wrapped up, customer happy.",
    "Done. Leftover paint left in the garage for the customer.",
]
CALLBACK = [
    "Callback: touched up drips on the {room} trim, no charge.",
    "Warranty visit, fixed peeling spot by the {room} window. Not billable.",
    "Came back to fix our own roller marks in the {room}, on us.",
    "Return trip, redid a section the customer flagged. No charge per owner.",
]
CONCESSION = [
    "Sorry again for the delays. We'll take ${amount} off the final bill.",
    "Because we ran over schedule, we're knocking ${amount} off your invoice.",
    "Thanks for your patience with the weather. ${amount} off as a goodwill credit.",
]
EXTRA_DONE = [
    "Finished touch-ups. Also did the {item} per the customer's request.",
    "Job complete. {Item} done as well, customer asked on site.",
    "Wrapped up the main job plus the {item}.",
    "All done incl. the {item}.",
]


# Set B: held-out phrasings for the test businesses (see the module docstring).
B = {
    "explicit": [
        "Any chance the crew could hit the {item} before you pack up?",
        "Could your guys freshen up the {item}? Want it done with everything else.",
        "If there's paint left, please do the {item} this week.",
        "Let's get the {item} painted while you're on site.",
    ],
    "implicit": [
        "The {item} is going to look shabby next to all this. I'd rather it got done now.",
        "Honestly the {item} needs it more than the rest. Have the crew do it before they go.",
        "Talked it over with my wife, we want the {item} done too, same color.",
    ],
    "price_inquiry": [
        "What would you charge for the {item} next spring?",
        "For next year: can you give me a number on the {item}?",
        "Thinking ahead, roughly what does a {item} run?",
    ],
    "cancel_extra": [
        "Change of plans, leave the {item} alone.",
        "Forget the {item}, we're replacing it anyway.",
        "Don't bother with the {item} after all.",
    ],
    "accept": [
        "Will do, the {item} is on the list for tomorrow.",
        "Can do. We'll get the {item} before we leave.",
        "Okay, the guys will handle the {item}.",
    ],
    "decline": [
        "We're booked solid, the {item} will have to be a separate job.",
        "Not this time unfortunately, the {item} needs its own estimate.",
    ],
    "callback": [
        "Went back for touch-ups the customer pointed out in the {room}; we're eating this one.",
        "Fixed drip marks in the {room}, our mistake. Owner said don't bill it.",
        "Redid a patch in the {room} we missed the first time, no invoice for this.",
    ],
    "concession": [
        "We'll credit you {amount} bucks for the delay.",
        "Go ahead and deduct ${amount} from the balance for the trouble.",
        "Pay ${amount} less than the quote, our way of saying sorry for the wait.",
    ],
    "extra_done": [
        "Done for the day. Customer had us do the {item} too.",
        "Finished, and knocked out the {item} while we had the ladders up.",
        "Main job wrapped. {Item} painted on the homeowner's request.",
    ],
}


class MessageWriter:
    def __init__(self, rng: np.random.Generator, style: str = "A") -> None:
        self.rng = rng
        self.style = style

    def _pick(self, options: list[str]) -> str:
        return str(options[int(self.rng.integers(0, len(options)))])

    def _choose(self, key: str, pool_a: list[str]) -> str:
        return self._pick(B[key] if self.style == "B" else pool_a)

    def _wrap(self, core: str) -> str:
        parts = [self._pick(GREET)]
        if self.rng.random() < 0.35:
            parts.append(self._pick(SMALL_TALK))
        parts.append(core)
        if self.rng.random() < 0.2:
            parts.append(self._pick(SMALL_TALK))
        parts.append(self._pick(SIGN))
        text = " ".join(p for p in parts if p)
        if self.rng.random() < 0.15:
            text = text.lower()
        return text

    def customer(self, business_id: str) -> dict[str, Any]:
        first, last = self._pick(FIRST), self._pick(LAST)
        n = int(self.rng.integers(10, 9999))
        return {
            "business_id": business_id,
            "name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}{int(self.rng.integers(1, 99))}@example.com",
            "phone": f"716-555-{int(self.rng.integers(100, 199)):04d}",
            "address": f"{n} {self._pick(STREETS)} Street, Buffalo, NY",
        }

    def request(self, spec: dict[str, Any]) -> str:
        template = self._pick(REQUEST_OPEN[spec["job_type"]])
        core = template.format(
            rooms=spec.get("rooms", 3),
            stories=spec.get("stories", 2),
            doors=spec.get("doors", 20),
            area=int(spec.get("area_sqft", 400)),
        )
        return self._wrap(core)

    def extra_request(self, item: str) -> str:
        if self.rng.random() < 0.55:
            return self._wrap(self._choose("explicit", EXPLICIT).format(item=item))
        return self._wrap(self._choose("implicit", IMPLICIT).format(item=item))

    def noise(self, kind: str) -> str:
        item = self._pick(list(EXTRA_ITEMS))
        pool = (
            self._choose(kind, NOISE[kind])
            if kind in ("price_inquiry", "cancel_extra")
            else self._pick(NOISE[kind])
        )
        return self._wrap(pool.format(item=item, room=self._pick(ROOMS)))

    def accept(self, item: str) -> str:
        return self._choose("accept", ACCEPT).format(item=item)

    def decline(self, item: str) -> str:
        return self._choose("decline", DECLINE).format(item=item)

    def closing_note(self) -> str:
        return self._pick(CLOSING)

    def callback_note(self) -> str:
        return self._choose("callback", CALLBACK).format(room=self._pick(ROOMS))

    def concession(self, amount: float) -> str:
        return self._choose("concession", CONCESSION).format(amount=f"{amount:,.0f}")

    def extra_done_note(self, item: str) -> str:
        return self._choose("extra_done", EXTRA_DONE).format(item=item, Item=item.capitalize())
