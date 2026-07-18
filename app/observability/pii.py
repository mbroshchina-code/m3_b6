"""Модуль маскирования персональных данных (PII) и хэширования промптов.

Адаптирован на основе эталонного референса регулярных выражений наставника.
"""

import hashlib
import re

# ТСтрогий порядок и оптимальные регулярные выражения
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Email ловим впереди, он надёжно отличим от чисел
    (re.compile(r"[\w.\-]+@[\w.\-]+\.\w+"), "[EMAIL]"),
    # Card (16 цифр подряд) ловим вторым, чтобы не поломать структуру
    (re.compile(r"\b\d{16}\b"), "[CARD]"),
    # Телефонный regex вырезает оставшиеся номера
    (
        re.compile(
            r"\+?\d{1,3}[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
        ),
        "[PHONE]",
    ),
]


def redact_pii(text: str) -> str:
    """Маскирует чувствительные данные (email, карты, телефоны) с помощью регулярных выражений."""
    if not text:
        return text
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def prompt_hash(text: str) -> str:
    """Генерирует SHA-256 хэш от сырой строки промпта для вывода в структурированный JSON-лог."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
