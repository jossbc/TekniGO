"""Conservative comparison of OCR text; never proves document authenticity."""
from collections import Counter
import re
import unicodedata


def normalize_name(value: str) -> list[str]:
    plain = ''.join(char for char in unicodedata.normalize('NFKD', value.upper()) if not unicodedata.combining(char))
    return re.findall(r'[A-Z]+', plain)


def compare_document_text(
    lines: list[tuple[str, float]], *, dni: str, nombre_completo: str,
) -> tuple[bool | None, bool | None]:
    # Missing/low confidence text is uncertain, not evidence of a different person.
    reliable = [text for text, score in lines if score >= 0.90]
    candidates = set()
    for line in reliable:
        for match in re.finditer(r'(?<!\d)(?<!\d[ -])\d(?:[ -]?\d){12}(?![ -]?\d)', line):
            candidates.add(re.sub(r'\D', '', match.group()))
    if len(candidates) != 1:
        return None, None
    if dni not in candidates:
        return True, False
    expected = normalize_name(nombre_completo)
    actual = Counter(normalize_name(' '.join(reliable)))
    if len(expected) < 2 or Counter(expected) - actual:
        return True, None
    return True, True
