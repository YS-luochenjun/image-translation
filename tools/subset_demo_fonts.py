"""Build small, renamed OFL font subsets for the reproducible bilingual demo.

Development-only dependency: fonttools==4.60.1. Production requires Pillow only.
Input full fonts are supplied explicitly; this utility never downloads them.
"""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
import json
import shutil


def main():
    parser = ArgumentParser()
    parser.add_argument('--sc', required=True, type=Path)
    parser.add_argument('--jp', required=True, type=Path)
    parser.add_argument('--license', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    from fontTools import subset, __version__
    from fontTools.ttLib import TTFont
    if args.output.exists():
        raise ValueError('Font output already exists; choose another directory')
    for path in (args.sc, args.jp, args.license):
        if not path.is_file():
            raise ValueError(f'Missing explicit input: {path}')
    args.output.mkdir(parents=True)
    entries = []
    repertoire = {
        'SC': '轻触画框，看看你的真实个性。免费领优惠券今天就犒劳自己限时享用皇堡中份薯条饮料领取套餐需符合条件！®',
        'JP': '額縁をタップして、本当の性格を見てみよう。',
    }
    for language, source in [('SC', args.sc), ('JP', args.jp)]:
        font = TTFont(source, recalcTimestamp=False)
        options = subset.Options()
        options.layout_features = ['*']
        options.name_IDs = ['*']
        options.name_languages = ['*']
        worker = subset.Subsetter(options=options)
        codepoints = set(range(32, 127)) | {ord(character) for character in repertoire[language]}
        worker.populate(unicodes=codepoints)
        worker.subset(font)
        family = f'Localizer Demo Serif {language}'
        postscript = f'LocalizerDemoSerif{language}-Bold'
        changed_names = {1: family, 2: 'Bold', 3: f'{postscript}; demo subset', 4: family+' Bold',
                         6: postscript, 16: family, 17: 'Bold'}
        font['name'].names = [record for record in font['name'].names if record.nameID not in changed_names]
        for identity, value in changed_names.items():
            font['name'].setName(value, identity, 3, 1, 0x409)
            font['name'].setName(value, identity, 1, 0, 0)
        if 'CFF ' in font:
            font['CFF '].cff.fontNames[0] = postscript
            top = font['CFF '].cff.topDictIndex[0]
            top.FamilyName, top.FullName = family, family+' Bold'
        output = args.output / f'demo-serif-{language.lower()}.otf'
        font.save(output)
        entries.append({'path': output.name, 'source_sha256': sha256(source.read_bytes()).hexdigest(),
                        'sha256': sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size,
                        'codepoints': sorted(codepoints), 'family': family,
                        'upstream': 'https://github.com/notofonts/noto-cjk/tree/main/Serif'})
    shutil.copyfile(args.license, args.output/'OFL.txt')
    (args.output/'provenance.json').write_text(json.dumps({
        'fonttools_version': __version__, 'license': 'SIL OFL1.1; retained copyright metadata; renamed subset families',
        'scope': 'Demo repertoire only. Use a full, licensed font for new text; missing glyphs must fail.',
        'fonts': entries}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(entries, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
