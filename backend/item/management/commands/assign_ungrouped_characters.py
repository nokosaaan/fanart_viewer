"""Files every character that sits in no CharacterGroup into its title's
group -- the one-time catch-up for characters that were added before
item.character_groups existed (or by a path that didn't file them).

New characters are filed automatically at save time from now on; this
command repairs what is already in the DB:

1. Legacy groups. A group whose `titles` was never filled in but is NAMED
   after a title in use gets linked to that title -- provided no other group
   already claims it, so an umbrella group (anime, game, ...) is never
   mistaken for a title.
2. Ungrouped characters. For each one, every item that carries it votes for
   the group its title resolves to (the most specific group claiming the
   title; the item's other, already-grouped characters break ties between
   two spellings of a title -- see item.character_groups.pick_group_for_item).
   The character goes to the group with the most votes. A title with no group
   gets one, named after the title. A character that appears only on items
   with no title is left alone and reported: there's nowhere to file it yet.

Dry run by default -- prints what it would do and changes nothing. Pass
--apply to write it.

Usage:
  python manage.py assign_ungrouped_characters            # report only
  python manage.py assign_ungrouped_characters --apply
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from item.character_groups import groups_for_title, pick_group_for_item
from item.models import CharacterGroup, Item


class Command(BaseCommand):
    help = 'Files characters that are in no CharacterGroup into their title\'s group (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes (default: report only).')

    def handle(self, *args, **options):
        apply = options['apply']
        groups = list(CharacterGroup.objects.all())
        items = list(Item.objects.exclude(characters=[]).exclude(characters__isnull=True)
                     .only('id', 'titles', 'characters'))
        titles_in_use = {t for it in items for t in (it.titles or []) if t}
        self.stdout.write(self.style.WARNING('--apply not given: nothing will be saved.') if not apply else 'Applying.')

        # 1. legacy groups: name == a title in use, titles empty, nobody else claims it
        claimed = {t for g in groups for t in (g.titles or [])}
        linked = 0
        for g in groups:
            if (g.titles or []) or g.name not in titles_in_use or g.name in claimed:
                continue
            self.stdout.write(f'  link legacy group {g.name!r} -> title {g.name!r}')
            if apply:
                g.titles = [g.name]
                g.save(update_fields=['titles'])
            claimed.add(g.name)
            linked += 1

        # 2. ungrouped characters
        grouped = {c for g in groups for c in (g.characters or [])}
        users = defaultdict(list)
        for it in items:
            for c in (it.characters or []):
                if c and c not in grouped:
                    users[c].append(it)

        by_pk = {g.pk: g for g in groups}
        assigned = created = skipped = 0
        new_groups = {}  # title -> CharacterGroup created this run (so two characters share it)
        for char, its in sorted(users.items()):
            votes = Counter()
            for it in its:
                g = pick_group_for_item(it.titles, [c for c in (it.characters or []) if c in grouped], groups)
                if g is not None:
                    votes[g.pk] += 1
            if votes:
                top = votes.most_common()
                best_pk = top[0][0]
                note = ' (tie with %s)' % ', '.join(by_pk[pk].name for pk, v in top[1:] if v == top[0][1]) \
                    if len(top) > 1 and top[1][1] == top[0][1] else ''
                group = by_pk[best_pk]
                self.stdout.write(f'  {char!r} -> group {group.name!r}{note}')
            else:
                title_counts = Counter(t for it in its for t in (it.titles or [])[:1] if t)
                if not title_counts:
                    self.stdout.write(f'  {char!r}: no item carrying it has a title -- left alone')
                    skipped += 1
                    continue
                title = title_counts.most_common(1)[0][0]
                group = new_groups.get(title)
                if group is None:
                    self.stdout.write(f'  {char!r} -> NEW group {title!r} (its title had no group)')
                    if apply:
                        group, _ = CharacterGroup.objects.get_or_create(
                            name=title, defaults={'characters': [], 'titles': [title]})
                    else:
                        group = CharacterGroup(name=title, characters=[], titles=[title])
                    new_groups[title] = group
                    created += 1
                else:
                    self.stdout.write(f'  {char!r} -> group {title!r} (created above)')
            if apply:
                group.characters = list(dict.fromkeys(list(group.characters or []) + [char]))
                group.save(update_fields=['characters'])
            else:
                group.characters = list(dict.fromkeys(list(group.characters or []) + [char]))
            assigned += 1

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(users)} ungrouped character(s): {assigned} filed, {skipped} left alone (no title); '
            f'{created} new group(s); {linked} legacy group(s) linked'
            + ('' if apply else ' -- dry run, nothing saved. Re-run with --apply.')
        ))
