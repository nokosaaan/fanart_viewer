"""Keeps every character in a CharacterGroup of its item's title.

A CharacterGroup is the classification bucket that scopes character-name
suggestions and the character picker to a title (see the model's docstring).
A character that sits in NO group ends up in the "未分類" (unclassified) list
of CharacterGroupManager.jsx, invisible to title-scoped pickers until a human
files it by hand. This module is the one place that decides where a character
goes, and every path that can introduce one (the edit form's save, the region
labeling save, "adopt the regions' characters") calls it, so no path leaves a
new character unclassified by default.

Why the earlier rule left characters behind (measured on a real archive: 7 of
680 characters ungrouped, 70 titles claimed by 2+ groups, 15 titles in use
with no group at all): it only acted when an item's titles resolved to
EXACTLY ONE group, matched exactly on group.titles. So it skipped
- a title claimed by both a franchise-level group and a title-specific child
  (the usual shape of the hierarchy) -- ambiguous to it, obvious to a human;
- an item with two spellings of one title ("blue archive" and
  "ブルーアーカイブ -Blue Archive-") that sit in two groups;
- a title with no group yet;
- a legacy group whose `titles` was never filled in (named after the title,
  but not linked to it);
- an item whose title was typed AFTER its characters (nothing re-ran then).
"""
import logging

logger = logging.getLogger(__name__)


def _depth(group, by_id):
    """Number of ancestors (a root is 0). Bounded against a cyclic `parent`
    chain, which the serializer rejects at write time but the ORM does not."""
    depth, node, seen = 0, group, {group.pk}
    while node.parent_id is not None and node.parent_id in by_id and node.parent_id not in seen:
        seen.add(node.parent_id)
        node = by_id[node.parent_id]
        depth += 1
    return depth


def groups_for_title(title, groups):
    """Groups that belong to `title`, most specific first.

    A group belongs to a title when the title is in its `titles` list; a
    legacy group that was never linked (empty `titles`) belongs to the title
    it is NAMED after. Among several claimants the deepest in the hierarchy
    wins (a title-specific child over the franchise-level group that also
    lists it), then the group with no children, then the oldest."""
    by_id = {g.pk: g for g in groups}
    claimants = [g for g in groups if title in (g.titles or [])]
    if not claimants:
        claimants = [g for g in groups if not (g.titles or []) and g.name == title]
    parent_ids = {g.parent_id for g in groups if g.parent_id is not None}
    return sorted(claimants, key=lambda g: (-_depth(g, by_id), g.pk in parent_ids, g.pk))


def pick_group_for_item(titles, item_characters, groups):
    """The one group an item's new characters belong in, or None if the item
    has no titles or none of them has a group.

    Each title proposes its most specific group. If the titles agree, that's
    the answer. If they don't (an item carrying two spellings of one title,
    or a genuine crossover), the group that already holds the most of this
    item's other characters wins -- evidence that this is where this item's
    characters are being filed -- then the earlier title, which is the
    title the person listed first."""
    others = set(item_characters or [])
    proposals = []
    for position, title in enumerate(t for t in (titles or []) if t):
        claimants = groups_for_title(title, groups)
        if claimants:
            proposals.append((position, claimants[0]))
    if not proposals:
        return None
    best = max(
        proposals,
        key=lambda pg: (len(others & set(pg[1].characters or [])), -pg[0]),
    )
    return best[1]


def ensure_characters_grouped(item, only=None):
    """File every ungrouped character of `item` into its title's group,
    creating the group when its title has none.

    - A character that is already in ANY group is left exactly where it is:
      it may sit on a broader or different group on purpose (a franchise-
      wide character kept on a parent group), and moving it would undo a
      human's deliberate placement.
    - `only` limits which characters are considered (the ones a save just
      introduced); by default every ungrouped character of the item, which is
      what re-running after a title is added needs.
    - With no titles there is nowhere to put them: nothing happens, and the
      next save that sets a title files them.
    - A legacy group matched only by its name gets that title written into
      its `titles` so the title-scoped pickers find it from now on.

    Returns {'group': CharacterGroup|None, 'assigned': [names],
    'created': bool}; `group` is None when nothing was assigned."""
    from .models import CharacterGroup

    empty = {'group': None, 'assigned': [], 'created': False}
    titles = [t for t in (item.titles or []) if t]
    characters = [c for c in (item.characters or []) if c]
    if not titles or not characters:
        return empty

    groups = list(CharacterGroup.objects.all())
    grouped = {c for g in groups for c in (g.characters or [])}
    wanted = set(only) if only is not None else None
    to_file = [c for c in characters if c not in grouped and (wanted is None or c in wanted)]
    if not to_file:
        return empty

    group = pick_group_for_item(titles, [c for c in characters if c in grouped], groups)
    created = False
    if group is None:
        group, created = CharacterGroup.objects.get_or_create(
            name=titles[0], defaults={'characters': [], 'titles': [titles[0]]},
        )
        if not created and titles[0] not in (group.titles or []):
            group.titles = list(group.titles or []) + [titles[0]]
    else:
        # legacy group found only by its name: link it to the title now
        for t in titles:
            if not (group.titles or []) and group.name == t:
                group.titles = [t]
                break

    group.characters = list(dict.fromkeys(list(group.characters or []) + to_file))
    group.save(update_fields=['characters', 'titles'])
    logger.info('Filed %s into character group %r (item %s, created=%s)', to_file, group.name, item.pk, created)
    return {'group': group, 'assigned': to_file, 'created': created}


def response_fields(result):
    """The keys update_fields/character_regions responses carry so the UI can
    tell the user where characters were filed."""
    if not result or result['group'] is None:
        return {}
    group = result['group']
    info = {'id': group.id, 'name': group.name, 'characters': result['assigned']}
    out = {'auto_assigned_to_character_group': info}
    if result['created']:
        out['auto_created_character_group'] = {'id': group.id, 'name': group.name}
    return out
