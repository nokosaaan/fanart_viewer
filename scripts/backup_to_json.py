#!/usr/bin/env python3
"""
Convert a Django `dumpdata` JSON (list of objects) into a manosaba-style JSON mapping.

Usage:
  python3 scripts/backup_to_json.py items-backup.json out.json "タイトル文字列"

Behavior:
 - Reads `items-backup.json` produced by `manage.py dumpdata item` (a JSON list of
   {"model":"...","pk":...,"fields":{...}} objects).
 - For each object, extracts fields into manosaba format: `LINK`, `ARTIST`, `TAGS`,
   `CHARACTERS`, `TITLES`, `SOURCE`, `SITUATION`.
 - Uses `external_id` from `fields` as the output key when present; otherwise falls back
   to the object's `pk` as a string.
 - If a title filter (third arg) is provided, only objects whose titles contain the
   filter string (case-insensitive substring) are included.
 - Writes the result to the specified output file as pretty UTF-8 JSON.
"""

import json
import sys
from pathlib import Path
import argparse


def as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if s.startswith('[') and s.endswith(']'):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
        return [p.strip() for p in s.split(',') if p.strip()]
    return [str(v)]


def extract_manosaba_from_dump_item(obj):
    # obj is expected to be a dict with keys: 'model', 'pk', 'fields'
    fields = obj.get('fields', {})
    out = {}
    if 'link' in fields:
        out['LINK'] = fields['link']
    if 'artist' in fields:
        out['ARTIST'] = fields['artist']
    # tags/characters/titles might be lists or strings; normalize to lists
    if 'tags' in fields:
        out['TAGS'] = as_list(fields['tags'])
    if 'characters' in fields:
        out['CHARACTERS'] = as_list(fields['characters'])
    if 'titles' in fields:
        out['TITLES'] = as_list(fields['titles'])
    if 'title' in fields and 'TITLES' not in out:
        out['TITLES'] = as_list(fields['title'])
    if 'source' in fields:
        out['SOURCE'] = fields['source']
    if 'situation' in fields:
        out['SITUATION'] = fields['situation']
    return out


def matches_title_filter(manosaba_entry, title_filter):
    if not title_filter:
        return True
    tf = normalize_str(title_filter)
    titles = manosaba_entry.get('TITLES') or []
    for t in titles:
        if tf in normalize_str(str(t)):
            return True
    return False


def main(argv):
    parser = argparse.ArgumentParser(description='Convert Django dumpdata item JSON to manosaba JSON with optional title filter')
    parser.add_argument('dumpfile', help='Django dump JSON file (list of objects)')
    parser.add_argument('out', help='output manosaba-style JSON file')
    parser.add_argument('title_filter', nargs='*', help='(optional) title substring to filter by (can be given without quotes as multiple args)')
    args = parser.parse_args(argv[1:])

    dump_p = Path(args.dumpfile)
    out_p = Path(args.out)
    # Allow title filter to be provided without quoting by accepting multiple args
    title_filter = None
    if args.title_filter:
        # join remaining args with spaces to reconstruct the intended title
        title_filter = ' '.join(args.title_filter)


def normalize_str(s: str) -> str:
    if s is None:
        return ''
    # normalize: lowercase, replace underscores with spaces, collapse whitespace
    out = s.lower().replace('_', ' ')
    out = ' '.join(out.split())
    return out

    if not dump_p.exists():
        print(f'Input dump file not found: {dump_p}', file=sys.stderr)
        return 2

    data = json.loads(dump_p.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        print('Expected dump JSON to be a list of objects (dumpdata output).', file=sys.stderr)
        return 2

    result = {}
    added = 0
    for obj in data:
        # only process item models (defensive)
        model = obj.get('model', '')
        if not model.lower().endswith('item') and 'item' not in model.lower():
            continue
        manosaba_entry = extract_manosaba_from_dump_item(obj)
        if not matches_title_filter(manosaba_entry, title_filter):
            continue
        fields = obj.get('fields', {})
        key = None
        if 'external_id' in fields and fields.get('external_id'):
            key = str(fields.get('external_id'))
        else:
            key = str(obj.get('pk'))
        result[key] = manosaba_entry
        added += 1

    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'Wrote {out_p} (entries={added})', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))