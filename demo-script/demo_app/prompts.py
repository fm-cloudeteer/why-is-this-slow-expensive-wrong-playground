"""
System prompt selector.

For most tenants, always returns the good prompt.
For tenant_b, returns the degraded prompt when the regression flag is active.

The contrast between good and degraded is deliberately extreme so the
quality score step-change is visually unambiguous in Langfuse.
"""

SYSTEM_PROMPTS = {
    "good": """\
You are a helpful customer support agent for a German railway operator.

Rules:
- Always respond in German.
- Always reference the ticket or booking number provided by the customer.
- Be concise: answer in 3-5 sentences.
- If you cannot resolve the issue directly, explain the next step clearly.
- Never invent train schedules, prices, or policies.
""",

    "degraded": "You are a helpful assistant.",
}


def get_system_prompt(tenant_id: str, regression_active: bool) -> str:
    if tenant_id == "tenant_b" and regression_active:
        return SYSTEM_PROMPTS["degraded"]
    return SYSTEM_PROMPTS["good"]
