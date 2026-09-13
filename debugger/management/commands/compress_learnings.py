"""
One-off, re-runnable command to rewrite existing DebugLearning rows from the
old long/bug-specific narrative style into short (2-3 sentence), PURE generic
heuristics — matching the style debugger/agent.py's mode == 'finalize' prompt
now drafts new closes in. This is a WRITE operation, so it ships as code for
an engineer to run manually post-deploy; the debugger agent itself is
read-only and cannot execute writes.

Usage:
  python manage.py compress_learnings              # rewrite all rows
  python manage.py compress_learnings --dry-run    # print old -> new only
  python manage.py compress_learnings --only 12    # spot-check a single row
"""
import json
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.claude_cli import NO_TOOLS, run_claude_cli
from debugger.models import DebugLearning

_SYSTEM_PROMPT = (
    'You compress a debugging "learning" entry into a short, PURE, GENERIC '
    'heuristic for a future debugging agent. Rules: 2-3 sentences MAX; zero '
    'reference to the originating incident (no specific request/thread '
    'details, no "Issue: X was broken because Y" narrative) unless the '
    'pattern is inherently about a named file/module; phrase it as a rule/ '
    'checklist that applies cold to a DIFFERENT bug of the same class. Also '
    'produce a short generic title for the bug/feature CLASS (not a summary '
    'of the original thread). Respond with ONLY a JSON object of the form '
    '{"title": "...", "content": "..."} and nothing else.'
)


class Command(BaseCommand):
    help = (
        'Rewrite existing DebugLearning rows into short (2-3 sentence), pure '
        'generic heuristics, stripping incident-specific narrative. '
        'Re-runnable; use --dry-run to preview first.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                             help='Print old -> new without writing anything.')
        parser.add_argument('--only', type=int, default=None,
                             help='Only process a single DebugLearning id (spot-check).')

    def handle(self, *args, **opts):
        if not shutil.which(settings.CLAUDE_CLI_BIN):
            raise CommandError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

        qs = DebugLearning.objects.all().order_by('id')
        if opts['only']:
            qs = qs.filter(id=opts['only'])
        if not qs.exists():
            self.stdout.write('No matching DebugLearning rows.')
            return

        for learning in qs:
            raw = _compress(learning)
            new_title, new_content = _parse(raw)
            self.stdout.write(f'[{learning.id}] OLD title: {learning.title}')
            self.stdout.write(f'[{learning.id}] OLD content: {learning.content[:200]}')
            if not (new_title and new_content):
                self.stderr.write(self.style.ERROR(
                    f'[{learning.id}] could not parse model output, skipping:\n{raw}'))
                self.stdout.write('-' * 60)
                continue
            self.stdout.write(self.style.SUCCESS(f'[{learning.id}] NEW title: {new_title}'))
            self.stdout.write(self.style.SUCCESS(f'[{learning.id}] NEW content: {new_content}'))
            self.stdout.write('-' * 60)

            if opts['dry_run']:
                continue
            learning.title = new_title
            learning.content = new_content
            learning.save()  # also refreshes search_vector via the model's save() override

        if opts['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — no rows were written.'))


def _compress(learning):
    user_prompt = f'Old title: {learning.title}\nOld content: {learning.content}'
    result = run_claude_cli(
        user_prompt,
        system_prompt=_SYSTEM_PROMPT,
        disallowed_tools=NO_TOOLS,
        model=settings.DEBUGGER_MODEL,
        max_turns=1,
    )
    return (result.get('text') or '').strip()


def _parse(raw):
    cleaned = raw.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`')
        if cleaned.lower().startswith('json'):
            cleaned = cleaned[4:]
    try:
        parsed = json.loads(cleaned.strip())
        return (parsed.get('title') or '').strip()[:200], (parsed.get('content') or '').strip()
    except Exception:
        return '', ''
