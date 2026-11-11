"""Stage the native bundle in its own package directory, without profile edits."""
from pathlib import Path
import json
import shutil

root = Path(__file__).resolve().parents[1]
source = root / 'plugins' / 'image-review-desk'
target = root / 'plugins' / 'image-review-workbench'
if target.exists():
    raise SystemExit('Target already exists; inspect it before replacing any files.')
manifest = json.loads((source / 'package.json').read_text(encoding='utf-8'))
assert manifest['name'] == '@local/image-review-workbench'
files = ['package.json', 'native-host.js', 'host-adapter.js', 'native-verify.js',
         'client.js', 'cordis.patch.yml', 'icon.svg', 'locale/en.json', 'locale/zh.json']
for relative in files:
    src = source / relative
    dst = target / relative
    assert src.is_file() and not src.is_symlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
print(json.dumps({'package': manifest['name'], 'directory': str(target), 'files': len(files)}))
