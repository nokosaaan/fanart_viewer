"""Populates CharacterDanbooruLink: for every distinct character name in
this app's own DB, tries to resolve the matching Danbooru character tag
via item.danbooru_lookup.find_tag_via_title_roster, using each item's own
`titles` as the expected-title cross-check that keeps the fuzzy name match
from colliding across unrelated franchises (see that function's docstring).

This is a ONE-TIME (well, re-run-as-needed) batch job against Danbooru's
public API — separate from the suggestion pipeline itself, which only
ever READS the resulting table (see views._match_tagger_characters). Only
ever stores what the resolver found; a wrong auto-applied link is worse
than an absent one, so nothing here is a guess.

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py link_danbooru_characters
  docker compose -f docker-compose.prod.yml exec web python manage.py link_danbooru_characters --force
  docker compose -f docker-compose.prod.yml exec web python manage.py link_danbooru_characters --only 博麗霊夢,霧雨魔理沙
"""
import time
from collections import defaultdict

from django.core.management.base import BaseCommand

from item import danbooru_lookup
from item.models import CharacterDanbooruLink, Item

# Tag-collision dedup itself now lives in danbooru_lookup.dedupe_tag_collisions
# (shared with the frontend's own per-character resolve action, see
# item.views.CharacterDanbooruLinkViewSet) — this just adapts its return
# value to this command's stdout/style logging.
def _dedupe_by_tag(stdout, style):
    demotions = danbooru_lookup.dedupe_tag_collisions()
    for d in demotions:
        stdout.write(style.WARNING(
            f"  tag collision on {d['tag']!r}: keeping {d['winner']!r}, demoting {d['demoted']}"
        ))
    return sum(len(d['demoted']) for d in demotions)

_REQUEST_DELAY = 1.0  # seconds between characters — several Danbooru API calls each; be polite to a public API


class Command(BaseCommand):
    help = (
        "Resolves each of this app's own character names to a Danbooru character tag via "
        "danbooru_lookup.find_tag_via_title_roster, storing the result in CharacterDanbooruLink for "
        'views._match_tagger_characters to use at suggestion time.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                             help='Re-resolve characters that already have a CharacterDanbooruLink row '
                                  '(default: skip already-attempted characters, resolved or not).')
        parser.add_argument('--only', type=str, default='',
                             help='Comma-separated character names to (re-)resolve, instead of every '
                                  'distinct character in the DB — for retrying a specific name after fixing '
                                  'its titles, without re-querying everything else.')
        parser.add_argument('--delay', type=float, default=_REQUEST_DELAY,
                             help=f'Seconds to sleep between characters (default {_REQUEST_DELAY}) — each '
                                  'involves several Danbooru API requests (one per candidate title).')

    def handle(self, *args, **options):
        only = {c.strip() for c in options['only'].split(',') if c.strip()}

        titles_by_char = defaultdict(set)
        for item in Item.objects.exclude(characters=[]).exclude(characters__isnull=True).only(
            'characters', 'titles',
        ).iterator():
            chars = [c for c in (item.characters or []) if c]
            titles = [t for t in (item.titles or []) if t]
            for c in chars:
                titles_by_char[c].update(titles)

        if only:
            missing = only - set(titles_by_char)
            if missing:
                self.stderr.write(self.style.WARNING(
                    f"--only names not found in the DB (skipping): {sorted(missing)}"
                ))
            names = sorted(only & set(titles_by_char))
        else:
            names = sorted(titles_by_char)

        if not options['force']:
            already = set(CharacterDanbooruLink.objects.filter(character_name__in=names)
                          .values_list('character_name', flat=True))
            skipped = len(already)
            names = [n for n in names if n not in already]
            if skipped:
                self.stdout.write(f'Skipping {skipped} character(s) already attempted (use --force to redo).')

        self.stdout.write(f'Resolving {len(names)} character(s) against Danbooru...\n')

        resolved, unresolved, no_titles = [], [], []
        for i, name in enumerate(names):
            expected_titles = sorted(titles_by_char[name])
            if not expected_titles:
                no_titles.append(name)
                CharacterDanbooruLink.objects.update_or_create(
                    character_name=name,
                    defaults={'danbooru_tag': None, 'resolved_via': '', 'match_score': None,
                              'debug_info': {'reason': 'no known titles for this character'}},
                )
                self.stdout.write(f'  [{i+1}/{len(names)}] {name}: skipped (no titles on any item)')
                continue

            link = danbooru_lookup.resolve_character_link(name, expected_titles=expected_titles)
            tag, score = link.danbooru_tag, link.match_score
            if tag:
                resolved.append((name, tag, score))
                self.stdout.write(self.style.SUCCESS(
                    f'  [{i+1}/{len(names)}] {name} -> {tag} (score={score:.2f}, titles={expected_titles})'
                ))
            else:
                unresolved.append(name)
                self.stdout.write(f'  [{i+1}/{len(names)}] {name}: no confident match (titles={expected_titles})')

            if i + 1 < len(names):
                time.sleep(options['delay'])

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {len(resolved)} resolved, {len(unresolved)} unresolved, '
            f'{len(no_titles)} skipped (no titles).'
        ))

        self.stdout.write('\nChecking for tag collisions across ALL links (not just this run)...')
        n_demoted = _dedupe_by_tag(self.stdout, self.style)
        if n_demoted:
            self.stdout.write(self.style.WARNING(f'{n_demoted} link(s) demoted back to unresolved — see above.'))
        else:
            self.stdout.write('No collisions found.')

        if resolved:
            self.stdout.write('\nResolved links (review before trusting — see CharacterDanbooruLink.debug_info):')
            for name, tag, score in sorted(resolved, key=lambda t: -(t[2] or 0)):
                still_linked = CharacterDanbooruLink.objects.filter(character_name=name, danbooru_tag=tag).exists()
                marker = '' if still_linked else '  [DEMOTED — see collision warning above]'
                self.stdout.write(f'  {name:20s} -> {tag:30s} score={score:.2f}{marker}')
