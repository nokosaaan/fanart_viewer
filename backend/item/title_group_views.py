"""Review API for how titles and CharacterGroups are linked -- the
human-in-the-loop counterpart to item.character_groups' automatic filing,
shaped like the character/title Danbooru link managers (list everything
actually in use with its status, suggest the likely fix, let a person apply
or undo it one row at a time).

Automatic filing (item.character_groups) can only act on links that exist. A
title with no group, a group never linked to its title, or two groups
claiming the same title all quietly leave characters ungrouped or filed
somewhere debatable; this is where a person sees which, and fixes it.

Status of a title (`status`):
- linked      exactly one group lists the title in its `titles`.
- multi       several groups list it; one is clearly the most specific (a
              title-specific child under a franchise-level group), and that
              is where new characters are filed.
- ambiguous   several groups list it and the top two are equally specific
              (two spellings of one title, two sibling groups) -- filing
              falls back to the group already holding the item's other
              characters, which a person should settle by unlinking one.
- legacy      no group lists it, but a group with empty `titles` is NAMED
              after it (never linked). Filing treats that group as the
              title's and writes the link the first time it files into it.
- unlinked    no group at all.
"""
import unicodedata
from collections import Counter, defaultdict

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .character_groups import _depth, ensure_characters_grouped, groups_for_title
from .models import CharacterGroup, Item

_SUGGESTION_LIMIT = 5


def _fold(s):
    return ''.join(ch for ch in unicodedata.normalize('NFKC', s or '').casefold() if ch.isalnum())


def _context():
    """Everything the listing needs, computed in one pass over the DB."""
    groups = list(CharacterGroup.objects.all())
    by_id = {g.pk: g for g in groups}
    grouped = {c for g in groups for c in (g.characters or [])}

    title_items = Counter()                    # title -> number of items
    title_chars = defaultdict(Counter)         # title -> {character: items carrying it}
    char_titles = defaultdict(Counter)         # character -> {title: items}
    for titles, characters in Item.objects.exclude(titles=[]).exclude(titles__isnull=True).values_list(
            'titles', 'characters'):
        titles = [t for t in (titles or []) if t]
        chars = [c for c in (characters or []) if c]
        for t in titles:
            title_items[t] += 1
            for c in chars:
                title_chars[t][c] += 1
                char_titles[c][t] += 1
    return groups, by_id, grouped, title_items, title_chars, char_titles


def _group_info(g, by_id):
    parent = by_id.get(g.parent_id)
    return {
        'id': g.pk, 'name': g.name, 'depth': _depth(g, by_id),
        'parent_id': g.parent_id, 'parent_name': parent.name if parent else None,
        'character_count': len(g.characters or []), 'titles': list(g.titles or []),
    }


def _title_row(title, ctx):
    groups, by_id, grouped, title_items, title_chars, _char_titles = ctx
    listed = [g for g in groups if title in (g.titles or [])]
    legacy = [] if listed else [g for g in groups if not (g.titles or []) and g.name == title]
    ordered = groups_for_title(title, groups)  # most specific first, legacy fallback included

    if len(listed) == 1:
        state = 'linked'
    elif len(listed) > 1:
        top, second = ordered[0], ordered[1]
        state = 'ambiguous' if _depth(top, by_id) == _depth(second, by_id) else 'multi'
    elif legacy:
        state = 'legacy'
    else:
        state = 'unlinked'

    chars = title_chars.get(title, Counter())
    ungrouped = sorted((c for c in chars if c not in grouped), key=lambda c: (-chars[c], c))

    claimant_ids = {g.pk for g in ordered}
    suggestions = []
    if state in ('unlinked', 'legacy', 'ambiguous') or ungrouped:
        folded = _fold(title)
        for g in groups:
            if g.pk in claimant_ids:
                continue
            overlap = sum(1 for c in (g.characters or []) if c in chars)
            name_hit = bool(folded) and (_fold(g.name) == folded or (len(folded) >= 3 and folded in _fold(g.name)))
            if overlap or name_hit:
                suggestions.append({**_group_info(g, by_id), 'overlap': overlap, 'name_match': name_hit})
        suggestions.sort(key=lambda s: (-s['name_match'], -s['overlap'], s['depth'], s['id']))
        suggestions = suggestions[:_SUGGESTION_LIMIT]

    return {
        'title_name': title,
        'item_count': title_items[title],
        'status': state,
        'claimants': [{**_group_info(g, by_id), 'via': 'titles' if g in listed else 'name'} for g in ordered],
        'target_group_id': ordered[0].pk if ordered else None,
        'ungrouped_characters': [{'name': c, 'items': chars[c]} for c in ungrouped],
        'suggestions': suggestions,
    }


def _group_row(g, ctx):
    """A group with no title linked: which titles do its characters actually
    appear under?"""
    groups, by_id, _grouped, title_items, _title_chars, char_titles = ctx
    votes = Counter()
    for c in (g.characters or []):
        for t, n in char_titles.get(c, {}).items():
            votes[t] += n
    folded = _fold(g.name)
    suggested = [{'title_name': t, 'votes': n, 'items': title_items[t]} for t, n in votes.most_common(_SUGGESTION_LIMIT)]
    for t in title_items:
        if _fold(t) == folded and all(s['title_name'] != t for s in suggested):
            suggested.insert(0, {'title_name': t, 'votes': 0, 'items': title_items[t], 'name_match': True})
    child_count = sum(1 for o in groups if o.parent_id == g.pk)
    return {**_group_info(g, by_id), 'child_count': child_count, 'suggested_titles': suggested[:_SUGGESTION_LIMIT]}


class TitleGroupLinkViewSet(viewsets.ViewSet):
    """See the module docstring."""

    def list(self, request):
        ctx = _context()
        groups = ctx[0]
        rows = [_title_row(t, ctx) for t in sorted(ctx[3])]
        unlinked_groups = [_group_row(g, ctx) for g in groups if not (g.titles or [])]
        return Response({
            'titles': rows,
            'unlinked_groups': unlinked_groups,
            'ungrouped_character_count': len({c for r in rows for c in (u['name'] for u in r['ungrouped_characters'])}),
        })

    @staticmethod
    def _body(request):
        title = (request.data.get('title_name') or '').strip()
        group_id = request.data.get('group_id')
        return title, group_id

    @action(detail=False, methods=['post'], url_path='link')
    def link(self, request):
        """Body: {title_name, group_id} -- list the title on the group."""
        title, group_id = self._body(request)
        if not title or group_id is None:
            return Response({'detail': 'title_name and group_id required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            group = CharacterGroup.objects.get(pk=group_id)
        except CharacterGroup.DoesNotExist:
            return Response({'detail': 'group not found'}, status=status.HTTP_404_NOT_FOUND)
        if title not in (group.titles or []):
            group.titles = list(group.titles or []) + [title]
            group.save(update_fields=['titles'])
        return Response({'status': 'ok', 'group_id': group.pk, 'titles': group.titles})

    @action(detail=False, methods=['post'], url_path='unlink')
    def unlink(self, request):
        """Body: {title_name, group_id} -- remove the title from the group."""
        title, group_id = self._body(request)
        if not title or group_id is None:
            return Response({'detail': 'title_name and group_id required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            group = CharacterGroup.objects.get(pk=group_id)
        except CharacterGroup.DoesNotExist:
            return Response({'detail': 'group not found'}, status=status.HTTP_404_NOT_FOUND)
        group.titles = [t for t in (group.titles or []) if t != title]
        group.save(update_fields=['titles'])
        return Response({'status': 'ok', 'group_id': group.pk, 'titles': group.titles})

    @action(detail=False, methods=['post'], url_path='create_group')
    def create_group(self, request):
        """Body: {title_name, parent_id?} -- a group named after the title,
        linked to it. If a group with that name already exists it is linked
        instead of duplicated."""
        title = (request.data.get('title_name') or '').strip()
        if not title:
            return Response({'detail': 'title_name required'}, status=status.HTTP_400_BAD_REQUEST)
        parent = None
        parent_id = request.data.get('parent_id')
        if parent_id is not None:
            try:
                parent = CharacterGroup.objects.get(pk=parent_id)
            except CharacterGroup.DoesNotExist:
                return Response({'detail': 'parent group not found'}, status=status.HTTP_404_NOT_FOUND)
        group, created = CharacterGroup.objects.get_or_create(
            name=title, defaults={'characters': [], 'titles': [title], 'parent': parent})
        if not created and title not in (group.titles or []):
            group.titles = list(group.titles or []) + [title]
            group.save(update_fields=['titles'])
        return Response({'status': 'ok', 'group_id': group.pk, 'created': created})

    @action(detail=False, methods=['post'], url_path='assign_characters')
    def assign_characters(self, request):
        """Body: {title_name} -- file this title's ungrouped characters into
        its group (see item.character_groups.ensure_characters_grouped), for
        every item carrying the title. Already-grouped characters stay where
        they are."""
        title = (request.data.get('title_name') or '').strip()
        if not title:
            return Response({'detail': 'title_name required'}, status=status.HTTP_400_BAD_REQUEST)
        assigned, group_names = [], set()
        for item in Item.objects.exclude(titles=[]).exclude(titles__isnull=True).only('id', 'titles', 'characters'):
            if title not in (item.titles or []):
                continue
            result = ensure_characters_grouped(item)
            if result['group'] is not None:
                assigned += result['assigned']
                group_names.add(result['group'].name)
        return Response({'status': 'ok', 'assigned': assigned, 'groups': sorted(group_names)})
