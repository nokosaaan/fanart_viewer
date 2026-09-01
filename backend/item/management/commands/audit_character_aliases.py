"""Audits every CharacterGroup's alphabetized (romaji/English) alias
against Danbooru's REAL tag database — a manually-typed romanization
("sherry", "tono hanna") is a guess, and doesn't necessarily match
Danbooru's own (often idiosyncratically-spelled) tag for the same
character. A wrong alias silently breaks the whole point of adding it: the
tagger's character recognition/Danbooru reverse lookup only bridges to
this app's Japanese-named DB entries via an EXACT normalized-string match
(see item.views._match_tagger_characters) — a near-miss alias is just as
useless as no alias at all.

For each group's aliases that look like romaji/English (pure ASCII), this
checks item.danbooru_lookup.tag_exists() — an EXACT tag-name lookup, not a
substring/wildcard search (wildcard search on a common Western name like
"sherry*"/"hanna*" collides with dozens of unrelated characters across
totally different franchises — verified live). When an alias ISN'T a real
tag, this tries two increasingly-approximate ways to find the correct one:

1. find_tag_via_title_roster (preferred): resolves each of the group's
   titles to its Danbooru copyright wiki page and reads that page's OWN
   character roster (a title's wiki body reliably links its full cast —
   verified live), then fuzzy-matches this group's Japanese given name
   against just that small roster. Sidesteps the common-given-name
   collision problem entirely, since it's only ever comparing against the
   handful of characters actually IN that title, not searching globally.

2. find_tag_via_other_names (fallback, used only if 1 found nothing): a
   global wildcard search on Danbooru wiki other_names, filtered down to
   pages whose body text happens to mention one of the group's titles.
   Much more prone to false positives/negatives than the roster approach
   (a short/generic title string can coincidentally appear in an unrelated
   character's wiki body, or the group's own stored title text may not
   literally appear in Danbooru's English-language wiki prose at all) —
   kept only as a fallback for titles whose own wiki page couldn't be
   resolved by method 1.

Only a confident, unambiguous match from either method is proposed;
anything else is reported as needing a human decision.

Dry-run by default — prints every bad alias and its proposed fix (or "no
confident match", or "already correct") without touching the DB. Pass
--apply to actually replace bad aliases with their proposed fix (only the
UNAMBIGUOUS ones — anything flagged as needing manual review is never
auto-applied, no matter what).

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py audit_character_aliases
  docker compose -f docker-compose.prod.yml exec web python manage.py audit_character_aliases --apply
"""
import time

from django.core.management.base import BaseCommand

from item.danbooru_lookup import (
    _to_danbooru_tag, tag_exists, find_tag_via_other_names, find_tag_via_title_roster,
)
from item.models import CharacterGroup


def _is_ascii(s):
    return bool(s) and all(ord(c) < 128 for c in s)


class Command(BaseCommand):
    help = (
        "Audits every CharacterGroup's romaji/English alias against Danbooru's real tag "
        'database, proposing corrections for aliases that don\'t actually exist as a Danbooru tag.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                             help='Actually replace bad aliases with their proposed fix (only when the '
                                  'fix is unambiguous) instead of just reporting them.')

    def handle(self, *args, **options):
        apply_fixes = options['apply']
        groups = list(CharacterGroup.objects.all())
        if not groups:
            self.stdout.write('No CharacterGroups found.')
            return

        n_checked = n_ok = n_fixed = n_needs_review = 0
        t0 = time.time()

        for group in groups:
            chars = list(group.characters or [])
            ascii_aliases = [c for c in chars if _is_ascii(c)]
            if not ascii_aliases:
                continue

            japanese_names = [c for c in chars if not _is_ascii(c)]
            if not _is_ascii(group.name) and group.name not in japanese_names:
                japanese_names.append(group.name)

            for alias in ascii_aliases:
                n_checked += 1
                tag = _to_danbooru_tag(alias)
                if tag_exists(tag):
                    n_ok += 1
                    continue

                proposal = None
                method = None
                for jp_name in japanese_names:
                    proposal = find_tag_via_title_roster(jp_name, group.titles)
                    if proposal:
                        method = 'roster'
                        break
                if not proposal:
                    for jp_name in japanese_names:
                        proposal = find_tag_via_other_names(jp_name, group.titles)
                        if proposal:
                            method = 'other_names (less reliable — double-check this one)'
                            break

                if proposal:
                    n_fixed += 1
                    self.stdout.write(self.style.WARNING(
                        f'[{group.name}] "{alias}" is NOT a real Danbooru tag -> '
                        f'proposing "{proposal.replace("_", " ")}" (via {method})'
                    ))
                    if apply_fixes:
                        idx = chars.index(alias)
                        chars[idx] = proposal.replace('_', ' ')
                        group.characters = chars
                        group.save(update_fields=['characters'])
                        self.stdout.write(self.style.SUCCESS(f'  -> applied'))
                else:
                    n_needs_review += 1
                    self.stdout.write(self.style.ERROR(
                        f'[{group.name}] "{alias}" is NOT a real Danbooru tag — '
                        'no unambiguous replacement found, needs manual review '
                        f'(japanese names tried: {japanese_names or "none available"})'
                    ))

        self.stdout.write(
            f'\nChecked {n_checked} aliases across {len(groups)} groups in {time.time() - t0:.0f}s: '
            f'{n_ok} already correct, {n_fixed} {"fixed" if apply_fixes else "fixable"}, '
            f'{n_needs_review} need manual review.'
        )
        if not apply_fixes and n_fixed:
            self.stdout.write('Re-run with --apply to actually write the fixable ones.')
