"""
Expensive prompt template for tenant_c.
Pads each request with ~25 turns of synthetic conversation history
to drive input token counts to ~1,500 (vs ~80 for normal prompts).

The padding is realistic-looking customer support dialogue so the
model doesn't behave unusually — we want the anomaly to be purely
in token counts, not in response quality or latency shape.
"""

import random
from typing import List

_HISTORY_PAIRS = [
    (
        "I'd like to check the status of my refund for ticket REF-20241115-1122.",
        "Your refund request is being processed. Refunds typically take 5-7 business "
        "days to appear on your original payment method. Your request was submitted "
        "on November 15th so you should see it by November 22nd at the latest.",
    ),
    (
        "Can I use my BahnCard discount on international routes to Austria?",
        "Yes, BahnCard discounts apply on DB-operated services including routes to "
        "Austria, Switzerland, and select destinations in France and Belgium. "
        "The discount is applied automatically when you enter your BahnCard number "
        "during booking.",
    ),
    (
        "The app isn't showing my saved tickets after the update.",
        "I'm sorry to hear that. This sometimes happens after a major app update. "
        "Please try logging out and back in — your tickets are stored server-side "
        "so they won't be lost. If the issue persists, you can also access your "
        "tickets at bahn.de using the same credentials.",
    ),
    (
        "Is there a quiet zone on the 14:23 ICE from Berlin to Hamburg?",
        "Yes, ICE trains have a designated quiet zone in coach 11 (first class) "
        "and coach 29 (second class) on this route. Phone calls and loud music "
        "are not permitted in these coaches. You can request a quiet zone seat "
        "during seat reservation at no extra charge.",
    ),
    (
        "My colleague booked a ticket in my name but used their email. "
        "How do I get the ticket transferred to my account?",
        "Tickets are non-transferable to other passengers, but we can update the "
        "delivery email so you receive the ticket directly. Please contact our "
        "service centre with the booking reference and your colleague's consent "
        "and we'll arrange the transfer within 24 hours.",
    ),
    (
        "Do DB trains have WiFi and is it free?",
        "ICE and IC trains offer onboard WiFi through the DB Navigator portal. "
        "Basic browsing is free for all passengers. A premium tier with higher "
        "speeds and streaming support is available for first-class passengers "
        "and BahnComfort members at no additional charge.",
    ),
    (
        "I need to travel with my dog next weekend. What are the rules?",
        "Small dogs in carriers travel free of charge on all DB services. "
        "Larger dogs require a half-price child ticket and must be kept on a "
        "lead and muzzled in all public areas of the train. Dogs are not "
        "permitted in the dining car or quiet zones.",
    ),
    (
        "The ticket machine at my station is broken. Can I buy on board?",
        "Yes, you can buy tickets from the train conductor on board. "
        "Please board at the first available door and inform the conductor "
        "immediately that you were unable to purchase at the station. "
        "A small on-board surcharge may apply depending on the route.",
    ),
    (
        "Can I split my journey and stop overnight in Nuremberg?",
        "Certain DB long-distance tickets allow stopovers at intermediate stations "
        "within the validity period. Point-to-point Sparpreis tickets do not allow "
        "stopovers. Flexpreis tickets do. Please check the fare conditions shown "
        "during booking — the stopover option will be listed if permitted.",
    ),
    (
        "My train was cancelled and I missed a connection. Who covers my hotel?",
        "If a DB train cancellation causes you to miss the last connection of the "
        "day, DB is obligated to cover reasonable hotel accommodation costs. "
        "Please keep all receipts and submit a claim through the DB Fahrgastrechte "
        "portal within 12 months of the journey date. Reimbursement is typically "
        "processed within 4 weeks.",
    ),
]

_rng = random.Random(99)  # fixed seed — same history every run


def expensive_prompt(tenant_id: str) -> str:
    """
    Builds a prompt padded with ~25 turns of synthetic conversation history.
    Target: ~1,400-1,600 input tokens.
    """
    # Shuffle but keep deterministic via seed
    pairs = _rng.sample(_HISTORY_PAIRS, min(len(_HISTORY_PAIRS), 8))

    # Repeat pairs to reach ~25 turns
    history_turns: List[str] = []
    while len(history_turns) < 25:
        for user_msg, assistant_msg in pairs:
            history_turns.append(f"Customer: {user_msg}")
            history_turns.append(f"Agent: {assistant_msg}")
            if len(history_turns) >= 25:
                break

    history = "\n".join(history_turns)

    # Real question appended at the end
    real_question = (
        "Customer: I need to reschedule my ticket from Berlin to Munich "
        "for next Tuesday. Ticket reference BER-MCH-20241203-4421. "
        "What are my options?"
    )

    return f"{history}\n{real_question}"
