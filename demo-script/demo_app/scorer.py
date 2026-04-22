"""
Quality scorer — rule-based and deterministic.

Uses no external model or API call. Rules are tuned so that:
  - Good prompt responses score ~0.85-0.95
  - Degraded prompt responses score ~0.10-0.25

The step-change needs to be at least 0.30 points to read clearly
on a projected Grafana panel from 10 metres away.

Scoring rules (each deduction is cumulative):
  1. Response not in German          -0.35
  2. No ticket/reference number      -0.30
  3. Response too short (<15 words)  -0.20
  4. Contains refusal language       -0.15

Maximum score: 1.0
"""

import re


# German language signal — common German words that reliably
# appear in customer support responses
_GERMAN_MARKERS = [
    "sie", "ihr", "ihre", "bitte", "können", "würden", "haben",
    "ist", "sind", "für", "mit", "auf", "und", "oder", "nicht",
    "sehr", "gerne", "leider", "natürlich", "zunächst",
]

_REFUSAL_MARKERS = [
    "i cannot", "i'm unable", "i don't have access",
    "as an ai", "i am not able", "i can't help",
]

# Ticket reference pattern: letters+digits with hyphens
_TICKET_PATTERN = re.compile(r"[A-Z]{2,}-[A-Z0-9-]{4,}", re.IGNORECASE)


def score_response(response: str, tenant_id: str) -> float:
    """
    Returns a quality score in [0.0, 1.0].
    Only tenant_b gets the full rule-based check — other tenants
    score high by default since they always use the good prompt.
    """
    if tenant_id != "tenant_b":
        # Other tenants: score based only on length and refusal checks
        score = 1.0
        if len(response.split()) < 15:
            score -= 0.20
        lower = response.lower()
        if any(marker in lower for marker in _REFUSAL_MARKERS):
            score -= 0.15
        return max(0.0, round(score, 2))

    # tenant_b: full check
    score = 1.0
    lower = response.lower()
    words = lower.split()

    # Rule 1: German language check
    german_word_count = sum(1 for w in words if w.rstrip(".,!?") in _GERMAN_MARKERS)
    german_ratio = german_word_count / max(len(words), 1)
    if german_ratio < 0.05:  # less than 5% of words are recognisably German
        score -= 0.35

    # Rule 2: Ticket reference present
    if not _TICKET_PATTERN.search(response):
        score -= 0.30

    # Rule 3: Response length
    if len(words) < 15:
        score -= 0.20

    # Rule 4: Refusal language
    if any(marker in lower for marker in _REFUSAL_MARKERS):
        score -= 0.15

    return max(0.0, round(score, 2))
