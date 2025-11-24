#!/usr/bin/env python3
"""
Convert a Django `dumpdata` JSON (list of objects) into a manosaba-style JSON.

Usage:
  python3 scripts/convert_dump_to_manosaba.py /path/to/items-backup-2.json [out.json]

If `out.json` is omitted the script writes to stdout.

Mapping (defaults):
  key: `external_id` (string)
  value: {
    "LINK": fields.link,
    "ARTIST": fields.artist,
    "TAGS": fields.tags (list),
    "CHARACTERS": fields.characters (list),
    "TITLES": fields.titles (list),
    "SOURCE": fields.source (if present),
    "SITUATION": fields.situation (if present)
  }

The script is defensive: missing fields become empty lists or omitted keys.
"""

import json
import sys
from pathlib import Path


def ensure_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    # sometimes stored as string; try to split by commas
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # if it looks like JSON array, try to parse
        if s.startswith('[') and s.endswith(']'):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
        # fallback: split on commas
        return [p.strip() for p in s.split(',') if p.strip()]
    # otherwise try to coerce
    return [str(v)]


def convert(dump_objs):
    out = {}
    for obj in dump_objs:
        model = obj.get('model', '')
        if model.endswith('item') or model.endswith('item.item'):
            fields = obj.get('fields', {})
            ext = fields.get('external_id')
            if ext is None:
                # try pk fallback
                ext = obj.get('pk')
            if ext is None:
                continue
            key = str(ext)
            item = {}
            if 'link' in fields and fields.get('link'):
                item['LINK'] = fields.get('link')
            if 'artist' in fields and fields.get('artist'):
                item['ARTIST'] = fields.get('artist')
            # tags/characters/titles -> lists
            if 'tags' in fields:
                item['TAGS'] = ensure_list(fields.get('tags'))
            if 'characters' in fields:
                item['CHARACTERS'] = ensure_list(fields.get('characters'))
            if 'titles' in fields:
                item['TITLES'] = ensure_list(fields.get('titles'))
            # optional fields
            if 'source' in fields and fields.get('source'):
                item['SOURCE'] = fields.get('source')
            if 'situation' in fields and fields.get('situation'):
                item['SITUATION'] = fields.get('situation')

            out[key] = item
    return out


def main(argv):
    if len(argv) < 2:
        print('Usage: convert_dump_to_manosaba.py input.json [out.json]', file=sys.stderr)
        return 2
    inp = Path(argv[1])
    outp = None
    if len(argv) >= 3:
        outp = Path(argv[2])

    data = json.loads(inp.read_text(encoding='utf-8'))
    converted = convert(data)

    if outp:
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(converted, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Wrote {outp}', file=sys.stderr)
    else:
        sys.stdout.write(json.dumps(converted, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
