"""Read selected public development documentation from an Electron ASAR archive."""
import argparse
import json
from pathlib import Path
import struct

parser = argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
parser.add_argument('--read', nargs='*', default=[])
parser.add_argument('--export', type=Path)
parser.add_argument('--match', default='')
args = parser.parse_args()
with args.archive.open('rb') as stream:
    size, = struct.unpack('<I', stream.read(4))
    header_size, = struct.unpack('<I', stream.read(4))
    inner_size, = struct.unpack('<I', stream.read(4))
    json_size, = struct.unpack('<I', stream.read(4))
    if json_size > 32 * 1024 * 1024:
        raise ValueError('Unexpected archive header size')
    header = json.loads(stream.read(json_size))
    files = {}
    def walk(node, prefix=''):
        for name, child in node.get('files', {}).items():
            path = prefix + name
            if 'files' in child:
                walk(child, path + '/')
            else:
                files[path] = child
    walk(header)
    if not args.read:
        matches = [path for path in files if (args.match in path if args.match else ('plugin' in path.lower() or 'sdk' in path.lower()))
                   and ('node_modules' not in path or '/@deepseek-ai/dsh' in path) and path.endswith(('.md', '.ts', '.js', '.mjs', '.cjs', '.json', '.d.ts'))]
        print(json.dumps({'top_level': list(header.get('files', {})), 'matches': matches[:220],
                          'sample': list(files)[:50]}, ensure_ascii=False, indent=2))
    for path in args.read:
        info = files[path]
        if info.get('unpacked') or info.get('link') or int(info['size']) > 2 * 1024 * 1024:
            raise ValueError('Only bounded packed documentation/source files supported')
        stream.seek(8 + header_size + int(info['offset']))
        text = stream.read(int(info['size'])).decode('utf-8')
        if args.export:
            destination = args.export / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding='utf-8')
            print(str(destination))
        else:
            print(f'\n=== {path} ===\n{text}')
