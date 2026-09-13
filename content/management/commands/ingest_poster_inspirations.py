"""
One-time (then incremental) ingestion of past posters into
PosterInspiration rows, auto-captioned via Claude vision — a headless
`claude -p` turn (core.claude_cli.run_claude_cli) with ONLY the Read tool
allowed, same non-agentic-in-spirit pattern as
content.agents.designer.generate_poster_brief, but vision needs the Read
tool rather than an inline base64 image block: `claude -p` prompts are plain
text, there is no equivalent of the Anthropic API's `{"type": "image", ...}`
content block, so the image has to be read off disk by the CLI itself. No
service in this codebase authenticates with ANTHROPIC_API_KEY — see
core/claude_cli.py. See poster-generation-plan.md §4.2.

Usage:
    python manage.py ingest_poster_inspirations --brand 1 --dir ~/posters --format 1:1

Point --dir at a folder of images, optionally organized into subfolders —
the subfolder name becomes topic_category (e.g. posters/festival/*.jpg ->
topic_category='festival'), matching the hero-command's --axis trick this
was adapted from. Flat folders get topic_category='general'. Re-running is
idempotent — rows are keyed on (brand, source_label), where source_label
is the filename stem.
"""
import json
import shutil
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from core.claude_cli import NO_TOOLS, run_claude_cli

from content.agents._util import strip_code_fence
from content.models import PosterFormat, PosterInspiration
from core.models import Brand

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}

CAPTION_SYSTEM_PROMPT = """You are describing a poster image for a design \
reference library. Another AI will later read your description (without \
seeing the image) to imitate this poster's composition and style for a \
DIFFERENT topic — so be concrete about layout, not just vibes.

Respond with ONLY a JSON object, no other text, with exactly these keys:
{
  "description": "What's concretely in the image — layout, text placement, \
subject, composition — concrete enough that a designer could recreate the \
composition without seeing the image.",
  "design_language": "Why it works — typography choices, mood, color \
usage, composition principles.",
  "tags": ["3-6 short freeform tags, e.g. 'style:bold_type', 'mood:festive'"],
  "has_logo": true or false — is there a visible brand logo in the image?,
  "score": integer 0-100, your estimate of this poster's design quality.
}"""

# Only Read is granted; everything else NO_TOOLS names is explicitly denied
# too (belt-and-braces alongside allowed_tools=['Read'] itself).
_READ_ONLY_DISALLOWED = [t for t in NO_TOOLS if t != 'Read']


class Command(BaseCommand):
    help = "Ingest a folder of past posters into PosterInspiration rows, auto-captioned via Claude vision."

    def add_arguments(self, parser):
        parser.add_argument('--brand', type=int, required=True, help='Brand id these posters belong to.')
        parser.add_argument('--dir', type=str, required=True, help='Folder of poster images.')
        parser.add_argument(
            '--format', type=str, default=PosterFormat.SQUARE,
            choices=[c[0] for c in PosterFormat.choices],
            help='Aspect ratio these images were published in (all images in one run share a format).',
        )

    def handle(self, *args, **options):
        if not shutil.which(settings.CLAUDE_CLI_BIN):
            raise CommandError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

        brand = Brand.objects.filter(id=options['brand']).first()
        if not brand:
            raise CommandError(f'Brand {options["brand"]} not found.')

        root = Path(options['dir']).expanduser()
        if not root.is_dir():
            raise CommandError(f'{root} is not a directory.')

        image_paths = sorted(p for p in root.rglob('*') if p.suffix.lower() in IMAGE_SUFFIXES)
        if not image_paths:
            self.stdout.write(self.style.WARNING(f'No images found under {root}.'))
            return

        created = updated = failed = 0

        for path in image_paths:
            category = path.parent.name if path.parent != root else 'general'
            try:
                caption = self._caption(path)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'{path.name}: captioning failed — {exc}'))
                failed += 1
                continue

            defaults = {
                'description': caption.get('description', ''),
                'design_language': caption.get('design_language', ''),
                'topic_category': category,
                'format': options['format'],
                'tags': caption.get('tags') or [],
                'has_logo': caption.get('has_logo'),
                'score': caption.get('score', 80),
                'is_active': True,
            }

            obj, was_created = PosterInspiration.objects.get_or_create(
                brand=brand, source_label=path.stem, defaults=defaults,
            )
            if not was_created:
                for key, value in defaults.items():
                    setattr(obj, key, value)
            if not obj.image:
                with open(path, 'rb') as f:
                    obj.image.save(path.name, File(f), save=False)
            obj.save()

            created += int(was_created)
            updated += int(not was_created)
            self.stdout.write(f'{"created" if was_created else "updated"}: {path.name} -> {category}')

        self.stdout.write(self.style.SUCCESS(
            f'Done. {created} created, {updated} updated, {failed} failed.'))

    def _caption(self, path):
        prompt = (
            f'Read the image file at {path.resolve()} and describe it per '
            'the system instructions.'
        )
        result = run_claude_cli(
            prompt,
            system_prompt=CAPTION_SYSTEM_PROMPT,
            allowed_tools=['Read'],
            disallowed_tools=_READ_ONLY_DISALLOWED,
            model=settings.CONTENT_TEXT_MODEL,
            # One turn to call Read, one to produce the final JSON — give a
            # little headroom over the bare minimum.
            max_turns=4,
        )
        if result.get('is_error'):
            raise RuntimeError(
                f'captioning failed (subtype={result.get("subtype")}): '
                f'{result.get("text") or "(no text)"}')
        text = (result.get('text') or '').strip()
        try:
            return json.loads(strip_code_fence(text))
        except json.JSONDecodeError:
            # Still not parseable — keep the raw text as a description rather
            # than losing the call entirely; still counts as ingested.
            return {'description': text, 'design_language': '', 'tags': [], 'has_logo': None, 'score': 80}
