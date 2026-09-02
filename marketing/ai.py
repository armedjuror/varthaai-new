"""
Blog AI writing assistant — litellm-backed prompt building + streaming.

Deliberately separate from the `debugger` app's `claude-agent-sdk` integration
(debugger/agent.py): that SDK spawns an agentic `claude` CLI subprocess with
tool-use/MCP for autonomous codebase investigation, which is far heavier than
what a "help me write this blog" assistant needs. This module makes a single
plain chat-completion call per turn via litellm, so the model/provider can be
swapped per request without touching this code (see settings.AI_ASSIST_MODELS).

Streaming caveat (see deploy/BLOG_AI.md): gunicorn's sync workers can be
killed mid-stream by the arbiter timeout on a long generation. When that
happens this generator simply stops yielding — nothing here can catch a
SIGKILL. For ordinary (non-fatal) provider/network errors we DO catch and
still persist whatever text streamed before the failure, so an admin never
loses partial output to a dropped connection.
"""
import json

import litellm
from django.conf import settings

from marketing.models import BlogDraftMessage, BlogDraftSession

SSE_DONE = 'data: [DONE]\n\n'

_SYSTEM_PROMPTS = {
    BlogDraftMessage.Action.GENERATE: (
        "You are a copywriter for Varthaai, a peanut-butter-led food brand selling "
        "to both B2C and B2B customers. Write a complete blog post draft in "
        "Markdown: a compelling title on the first line as an H1, then the full "
        "body, then a 1-2 sentence excerpt clearly marked at the end under a "
        "'---\\nExcerpt:' separator. Match the requested tone, audience and "
        "target word count as closely as possible. If specific products are "
        "referenced, mention them naturally — do not invent product facts."
    ),
    BlogDraftMessage.Action.REWRITE: (
        "You are an editor improving an existing Varthaai blog draft. Rewrite the "
        "supplied content for clarity, grammar, tone and basic SEO, preserving its "
        "meaning and structure unless asked otherwise. Return only the revised "
        "Markdown body — no commentary."
    ),
    BlogDraftMessage.Action.SUGGEST_META: (
        "You are an SEO assistant for Varthaai's blog. Given the draft content, "
        "suggest: a title (<= 60 chars), an excerpt (<= 200 chars), a meta title "
        "(<= 60 chars) and a meta description (<= 155 chars). Return them as a "
        "small Markdown list labelled Title / Excerpt / Meta Title / Meta "
        "Description — nothing else."
    ),
    BlogDraftMessage.Action.OUTLINE: (
        "You are a content strategist for Varthaai's blog. Produce a Markdown "
        "heading outline (H2/H3 only, no body text) covering the topic, tailored "
        "to the requested audience and tone, that the admin will fill in by hand."
    ),
    BlogDraftMessage.Action.CHAT: (
        "You are a writing assistant helping a Varthaai admin refine a blog post. "
        "Respond conversationally to their feedback about the draft below."
    ),
}


def build_messages(session, action, admin_message, blog_content):
    """Assemble the litellm `messages` list for one turn: system prompt for the
    action + this session's prior turns + the new instruction."""
    system = _SYSTEM_PROMPTS.get(action, _SYSTEM_PROMPTS[BlogDraftMessage.Action.CHAT])
    context_bits = []
    if session.topic:
        context_bits.append(f'Topic: {session.topic}')
    if session.target_audience:
        context_bits.append(f'Target audience: {session.target_audience}')
    if session.tone:
        context_bits.append(f'Tone: {session.tone}')
    if session.word_count_target:
        context_bits.append(f'Target word count: {session.word_count_target}')
    if session.flavor_refs:
        context_bits.append(f'Mention these product ids naturally where relevant: {session.flavor_refs}')
    if context_bits:
        system += '\n\nSession context:\n' + '\n'.join(f'- {b}' for b in context_bits)

    messages = [{'role': 'system', 'content': system}]
    for m in session.messages.exclude(role=BlogDraftMessage.Role.SYSTEM).order_by('created_at', 'id'):
        messages.append({
            'role': 'user' if m.role == BlogDraftMessage.Role.ADMIN else 'assistant',
            'content': m.content,
        })

    turn = admin_message or ''
    if blog_content:
        turn += ('\n\n---\nCurrent draft content:\n' + blog_content) if turn else ('Current draft content:\n' + blog_content)
    if turn:
        messages.append({'role': 'user', 'content': turn})
    return messages


def stream_completion(session, action, admin_message, model_id, blog_content):
    """Generator: streams SSE chunks of the assistant's reply, then persists
    both turns (admin input + assistant output) with usage/cost."""
    model_id = model_id or settings.AI_ASSIST_DEFAULT_MODEL
    provider = model_id.split('/', 1)[0] if '/' in model_id else 'anthropic'

    if admin_message or blog_content:
        BlogDraftMessage.objects.create(
            session=session,
            role=BlogDraftMessage.Role.ADMIN,
            action=action,
            content=admin_message or '',
        )

    messages = build_messages(session, action, admin_message, blog_content)

    chunks = []
    full_text = []
    stream_error = ''
    try:
        response = litellm.completion(
            model=model_id,
            messages=messages,
            stream=True,
            api_key=settings.ANTHROPIC_API_KEY,
        )
        for chunk in response:
            chunks.append(chunk)
            delta = ''
            try:
                delta = chunk.choices[0].delta.content or ''
            except (AttributeError, IndexError):
                delta = ''
            if delta:
                full_text.append(delta)
                yield f'data: {json.dumps({"delta": delta})}\n\n'
    except Exception as exc:  # noqa: BLE001 — network/provider errors surface to the chat UI
        stream_error = str(exc)
        yield f'data: {json.dumps({"error": stream_error})}\n\n'

    assistant_text = ''.join(full_text)

    # Persist whatever streamed, even on a mid-stream failure (dropped
    # connection, provider error) — the admin should never lose partial
    # output to a truncated request; the UI still lands it as editable text.
    if assistant_text or stream_error:
        prompt_tokens = completion_tokens = None
        cost_usd = None
        if chunks:
            try:
                full_response = litellm.stream_chunk_builder(chunks, messages=messages)
                usage = getattr(full_response, 'usage', None)
                if usage:
                    prompt_tokens = getattr(usage, 'prompt_tokens', None)
                    completion_tokens = getattr(usage, 'completion_tokens', None)
                cost_usd = litellm.completion_cost(completion_response=full_response)
            except Exception:  # noqa: BLE001 — usage/cost bookkeeping must never break the reply
                pass
        BlogDraftMessage.objects.create(
            session=session,
            role=BlogDraftMessage.Role.ASSISTANT,
            action=action,
            content=assistant_text or f'[No content — {stream_error or "empty response"}]',
            provider=provider,
            model=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )
        session.save(update_fields=['updated_at'])
    yield SSE_DONE
