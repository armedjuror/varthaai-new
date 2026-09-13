"""Shared helpers for content/agents/* and the ingestion management command."""


def strip_code_fence(text):
    """Claude sometimes wraps JSON in a ```json ... ``` fence despite being
    told not to (confirmed live — see content-generator-plan.md's Track B
    notes) — strip it before parsing rather than treating a fenced-but-valid
    response as unparseable."""
    stripped = (text or '').strip()
    if stripped.startswith('```'):
        stripped = stripped.split('\n', 1)[1] if '\n' in stripped else ''
        if stripped.endswith('```'):
            stripped = stripped[:-3]
    return stripped.strip()
