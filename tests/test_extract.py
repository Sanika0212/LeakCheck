"""Rule extractor behaviour on the phrasings it was written for."""

from __future__ import annotations

from leakcheck.extract import RuleExtractor, find_amount, find_item


def test_items_and_amounts_are_found() -> None:
    assert find_item("could you also do the garage door") == "garage door"
    assert find_item("the shutters look tired") == "window shutters"
    assert find_amount("we'll take $1,250 off") == 1250.0


def test_requests_questions_and_cancellations_differ() -> None:
    r = RuleExtractor()
    assert (
        r.message("Can you also paint the shed while the crew is here?", "inbound")[0]
        == "extra_request"
    )
    assert (
        r.message("How much would it cost to do the shed someday?", "inbound")[0]
        == "price_question"
    )
    assert r.message("Actually, skip the shed, we'll leave it.", "inbound")[0] == "cancel_extra"
    assert r.message("What time should we expect the crew tomorrow?", "inbound")[0] == "other"


def test_contractor_replies_and_credits() -> None:
    r = RuleExtractor()
    assert r.message("Sure, we'll take care of the shed this week.", "outbound")[0] == "accept"
    assert r.message("Sorry, we can't fit the shed in this time.", "outbound")[0] == "decline"
    label, _, amount = r.message(
        "Sorry for the delays. We'll take $150 off the final bill.", "outbound"
    )
    assert (label, amount) == ("concession", 150.0)


def test_notes() -> None:
    r = RuleExtractor()
    assert r.note("Finished touch-ups. Also did the shed per the customer's request.")[0]
    done, _, no_charge = r.note("Callback: touched up drips on the trim, no charge.")
    assert no_charge
    assert not done
