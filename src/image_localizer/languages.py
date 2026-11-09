"""Fireplay business language aliases supplied by the user on 2026-11-06."""

LANGUAGE_ALIAS_GROUPS = {
    'en': ['en', 'us', 'us2'],
    'pt': ['pt', 'por', 'portuguese', 'portugal', 'br', 'bra', 'brazil', 'brasil', 'ptbr', 'pt-br'],
    'es': ['es', 'esp', 'spanish', 'spain', 'mx', 'mexico', 'cl', 'co', 'pe'],
    'ja': ['ja', 'jp', 'jpn', 'japanese', 'japan'],
    'de': ['de', 'deu', 'ger', 'german', 'germany'],
    'fr': ['fr', 'fra', 'fre', 'french', 'france'],
    'it': ['it', 'ita', 'italian', 'italy'],
    'id': ['id', 'ind', 'indonesian', 'indonesia'],
    'th': ['th', 'tha', 'thai', 'thailand'],
    'vi': ['vi', 'vie', 'vietnamese', 'vietnam', 'vn'],
    'ko': ['ko', 'kor', 'korean', 'kr', 'korea'],
    'zh': ['zh', 'cn', 'chs', 'chinese', 'china', 'hk'],
    'zh-TW': ['zh-tw', 'zhtw', 'cht', 'traditionalchinese', 'tw', 'taiwan'],
    'ru': ['ru', 'rus', 'russian', 'russia'],
    'tr': ['tr', 'tur', 'turkish', 'turkey'],
    'ar': ['ar', 'ara', 'arabic'],
    'hi': ['hi', 'hin', 'hindi', 'in', 'india'],
    'pl': ['pl', 'pol', 'polish', 'poland'],
    'nl': ['nl', 'dut', 'dutch', 'netherlands'],
    'fi': ['fi', 'fin', 'finnish', 'finland'],
    'sv': ['sv', 'swe', 'swedish', 'sweden', 'se'],
    'da': ['da', 'dan', 'danish', 'denmark', 'dk'],
    'ms': ['ms', 'may', 'malay', 'malaysia'],
    'ro': ['ro', 'ron', 'romanian', 'romania'],
    'bn': ['bn', 'ben', 'bengali', 'bd', 'bangladesh'],
    'fil': ['fil', 'tl', 'tgl', 'tagalog', 'filipino', 'ph', 'philippines'],
    'hu': ['hu', 'hun', 'hungarian', 'hungary'],
}

# These are business identifiers: in means Hindi here, irrespective of IANA aliases.
LANGUAGE_ALIASES = {
    alias: language
    for language, aliases in LANGUAGE_ALIAS_GROUPS.items()
    for alias in aliases
}
