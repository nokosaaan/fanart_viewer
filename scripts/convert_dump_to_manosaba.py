#!/usr/bin/env python3
"""
Convert a Django `dumpdata` JSON (list of objects) into a manosaba-style JSON.

Usage:
    python3 scripts/convert_dump_to_manosaba.py /path/to/items-backup-2.json [out_dir]

If `out_dir` is omitted the script writes per-source JSON files into
`<input_stem>_by_source/` next to the input file. If `out_dir` is a path
to an existing directory it will be used. If `out_dir` is a path that does
not exist it will be created and used as the output directory.

Mapping (defaults):
  key: `external_id` (string)
  value: {
    "LINK": fields.link,
    "ARTIST": fields.artist,
    "TAGS": fields.tags (list),
    "CHARACTER": fields.characters (list),
    "TITLE": fields.titles (list),
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
    # Group output by source value. Return a dict: source_name -> {key: item}
    grouped = {}
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
                item['CHARACTER'] = ensure_list(fields.get('characters'))
            if 'titles' in fields:
                item['TITLE'] = ensure_list(fields.get('titles'))
            # optional fields
            src = None
            if 'source' in fields and fields.get('source'):
                src = fields.get('source')
                item['SOURCE'] = src
            if 'situation' in fields and fields.get('situation'):
                item['SITUATION'] = fields.get('situation')

            if not src:
                src = 'unknown'

            grouped.setdefault(str(src), {})[key] = item

    return grouped


def main(argv):
    if len(argv) < 2:
        print('Usage: convert_dump_to_manosaba.py input.json [out.json]', file=sys.stderr)
        return 2
    inp = Path(argv[1])
    outp = None
    if len(argv) >= 3:
        outp = Path(argv[2])

    data = json.loads(inp.read_text(encoding='utf-8'))
    grouped = convert(data)

    # determine output directory
    if outp is None:
        out_dir = inp.parent / (inp.stem + '_by_source')
    else:
        # If path exists and is dir, use it. If it has no suffix assume dir. Otherwise use parent dir.
        if outp.exists() and outp.is_dir():
            out_dir = outp
        elif outp.suffix == '':
            out_dir = outp
        else:
            out_dir = outp.parent

    out_dir.mkdir(parents=True, exist_ok=True)

    # safe filename helper
    import re

    def safe_name(s):
        # convert to str, replace unsafe chars with underscore, limit length
        n = str(s)
        n = n.strip()
        if not n:
            n = 'unknown'
        # replace path separators and control chars
        n = re.sub(r'[\s/\\]+', '_', n)
        # allow only a limited charset
        n = re.sub(r'[^0-9A-Za-z._-]', '_', n)
        if len(n) > 100:
            n = n[:100]
        return n

    written = []
    for src, mapping in grouped.items():
        fname = safe_name(src) + '.json'
        target = out_dir / fname
        target.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding='utf-8')
        written.append(target)

    for p in written:
        print(f'Wrote {p}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
