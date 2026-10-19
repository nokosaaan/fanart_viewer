"""Keeps every character in a CharacterGroup of its item's title.

A CharacterGroup is the classification bucket that scopes character-name
suggestions and the character picker to a title (see the model's docstring).
A character that sits in NO group ends up in the "未分類" (unclassified) list
of CharacterGroupManager.jsx, invisible to title-scoped pickers until a human
files it by hand. This module is the one place that decides where a character
goes, and every path that can introduce one (the edit form's save, the region
labeling save, "adopt the regions' characters") calls it, so no path leaves a
new character unclassified by default.

When it is NOT obvious where a character goes -- the item has several titles
that lead to different groups (two spellings of one title, a crossover) --
nothing is filed and the save response carries a `character_group_choice`; the
UI asks the person (lib/groupChoice.jsx) and POSTs the answer to the item's
assign_character_groups endpoint. A single title, or several titles that lead
to the same group, are still filed automatically.

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


def group_options(titles, groups):
    """The distinct places an item's new characters could be filed, one per
    different group its titles lead to (in title order):
    [{'title', 'group': CharacterGroup|None}, ...] -- `group` None means that
    title has no group yet (filing there would create one).

    Each title proposes its most specific group (see groups_for_title). Titles
    that lead to the same group are one option. One option means there's
    nothing to decide; several -- two spellings of one title, a crossover --
    means the characters could go to more than one place, which is a human's
    call (see ensure_characters_grouped)."""
    options, seen_groups, seen_new_titles = [], set(), set()
    for title in (t for t in (titles or []) if t):
        claimants = groups_for_title(title, groups)
        if claimants:
            group = claimants[0]
            if group.pk in seen_groups:
                continue
            seen_groups.add(group.pk)
            options.append({'title': title, 'group': group})
        elif title not in seen_new_titles:
            seen_new_titles.add(title)
            options.append({'title': title, 'group': None})
    return options


def pick_group_for_item(titles, item_characters, groups):
    """The one group to file into when there is NO choice to make, else None.

    That is: the item's titles lead to exactly one group (or the same group
    several times over). With two or more different destinations this returns
    None instead of guessing -- see group_options. (It used to pick the group
    already holding the most of the item's other characters; that is now only
    the suggested default shown to the person, see suggested_option.)"""
    options = group_options(titles, groups)
    if len(options) == 1 and options[0]['group'] is not None:
        return options[0]['group']
    return None


def suggested_option(options, item_characters):
    """Index of the option to pre-select when asking: the group that already
    holds the most of this item's other characters (evidence of where this
    item's characters are filed), then the earlier title."""
    others = set(item_characters or [])
    best, best_key = 0, None
    for i, opt in enumerate(options):
        held = len(others & set(opt['group'].characters or [])) if opt['group'] is not None else -1
        key = (held, -i)
        if best_key is None or key > best_key:
            best, best_key = i, key
    return best


def _link_legacy_title(group, titles):
    """A legacy group found only by its name: link it to that title now."""
    if not (group.titles or []):
        for t in titles:
            if group.name == t:
                group.titles = [t]
                return


def file_characters(group_or_title, characters, create_title=None):
    """File `characters` into a group; returns (group, created). Pass an
    existing CharacterGroup, or None plus `create_title` to get/create the
    group named after that title (linked to it)."""
    from .models import CharacterGroup

    created = False
    group = group_or_title
    if group is None:
        group, created = CharacterGroup.objects.get_or_create(
            name=create_title, defaults={'characters': [], 'titles': [create_title]})
        if not created and create_title not in (group.titles or []):
            group.titles = list(group.titles or []) + [create_title]
    group.characters = list(dict.fromkeys(list(group.characters or []) + list(characters)))
    group.save(update_fields=['characters', 'titles'])
    return group, created


def ensure_characters_grouped(item, only=None, title=None):
    """File the ungrouped characters of `item` into its title's group,
    creating the group when its title has none -- unless the item's titles
    lead to MORE THAN ONE possible destination, in which case nothing is
    filed for the characters in question and a choice is returned for the
    person to make instead of guessing.

    - A character already in ANY group is left exactly where it is: it may sit
      on a broader or different group on purpose (a franchise-wide character
      kept on a parent group), and moving it would undo a human's deliberate
      placement.
    - `title` restricts the decision to that one title (the review screen's
      per-title "file this title's characters" action: the person already
      chose the title, so there is nothing to ask).
    - When the destination is unambiguous, EVERY ungrouped character of the
      item is filed. When it isn't, only the characters in `only` (the ones
      this save introduced) are asked about, so a character the person
      deliberately left for later isn't asked again on every save; `only`
      None means all ungrouped ones.
    - With no titles there is nowhere to put them: nothing happens, and the
      next save that sets a title files them.
    - A legacy group matched only by its name gets that title written into
      its `titles` so the title-scoped pickers find it from now on.

    Returns {'group', 'assigned', 'created', 'pending'}: `group` None when
    nothing was filed; `pending` None or
    {'characters': [...], 'options': [{'title', 'group': CharacterGroup|None,
    'suggested': bool}, ...]}."""
    from .models import CharacterGroup

    empty = {'group': None, 'assigned': [], 'created': False, 'pending': None}
    titles = [title] if title else [t for t in (item.titles or []) if t]
    characters = [c for c in (item.characters or []) if c]
    if not titles or not characters:
        return empty

    groups = list(CharacterGroup.objects.all())
    grouped = {c for g in groups for c in (g.characters or [])}
    ungrouped = [c for c in characters if c not in grouped]
    if not ungrouped:
        return empty

    options = group_options(titles, groups)
    if not options:
        return empty

    if len(options) > 1:
        wanted = set(only) if only is not None else None
        asking = [c for c in ungrouped if wanted is None or c in wanted]
        if not asking:
            return empty
        pick = suggested_option(options, [c for c in characters if c in grouped])
        return {**empty, 'pending': {
            'characters': asking,
            'options': [{**opt, 'suggested': i == pick} for i, opt in enumerate(options)],
        }}

    option = options[0]
    group = option['group']
    if group is not None:
        _link_legacy_title(group, titles)
    group, created = file_characters(group, ungrouped, create_title=option['title'])
    logger.info('Filed %s into character group %r (item %s, created=%s)', ungrouped, group.name, item.pk, created)
    return {'group': group, 'assigned': ungrouped, 'created': created, 'pending': None}


def response_fields(result):
    """The keys update_fields/character_regions responses carry so the UI can
    tell the user where characters were filed, or ask where to file them."""
    if not result:
        return {}
    out = {}
    if result['group'] is not None:
        group = result['group']
        out['auto_assigned_to_character_group'] = {'id': group.id, 'name': group.name, 'characters': result['assigned']}
        if result['created']:
            out['auto_created_character_group'] = {'id': group.id, 'name': group.name}
    pending = result.get('pending')
    if pending:
        out['character_group_choice'] = {
            'characters': pending['characters'],
            'options': [
                {
                    'title': o['title'],
                    'group_id': o['group'].pk if o['group'] is not None else None,
                    'group_name': o['group'].name if o['group'] is not None else o['title'],
                    'create': o['group'] is None,
                    'suggested': o['suggested'],
                }
                for o in pending['options']
            ],
        }
    return out
