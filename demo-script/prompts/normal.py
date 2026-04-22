"""
Normal prompt templates — representative customer support queries
for a German railway operator ticketing system.
Keeps input tokens ~60-100 per request.
"""

import random

_QUERIES = [
    (
        "I need to reschedule my train ticket from Berlin to Munich "
        "for next Tuesday. Ticket reference BER-MCH-20241203-4421. "
        "What are my options?"
    ),
    (
        "My ICE train from Hamburg to Frankfurt was delayed by 47 minutes "
        "yesterday. Reference HH-FRA-20241202-8812. "
        "Can I claim a refund?"
    ),
    (
        "I accidentally bought a second-class ticket but I need first class "
        "for the Berlin-Cologne route on Friday. Ticket TKT-20241205-3391. "
        "How do I upgrade?"
    ),
    (
        "Is there a group discount available for 8 people travelling "
        "from Munich to Dresden on December 20th?"
    ),
    (
        "My BahnCard 50 expires next month. "
        "How do I renew it and will my saved payment method carry over?"
    ),
    (
        "The seat reservation on my ticket REF-20241201-7723 shows "
        "coach 12 seat 44 but the coach doesn't exist on this train. "
        "Can you help?"
    ),
    (
        "I need to add a bicycle reservation to my existing ticket "
        "MUC-BER-20241207-5512 for the 09:42 departure. Is this possible?"
    ),
    (
        "What is the luggage allowance for the Hamburg-Berlin ICE service? "
        "I have two large suitcases and a folding bike."
    ),
]

_rng = random.Random(42)  # fixed seed for reproducibility


def normal_prompt(tenant_id: str) -> str:
    """Returns a random normal-length customer support query."""
    return _rng.choice(_QUERIES)
