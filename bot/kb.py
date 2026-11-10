"""База знаний: markdown-файлы из kb/. Маленькая база целиком идёт в промпт,
большая — режется на разделы (по заголовкам ##), и в промпт попадают самые подходящие к вопросу."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class Section:
    source: str
    title: str
    text: str

    def render(self) -> str:
        return f"### {self.title}\n{self.text.strip()}"


def _stem(word: str) -> str:
    # грубая «основа» для русского: отрезаем окончание, чтобы «экрана» и «экран» совпадали
    word = word.lower().replace("ё", "е")
    return word[:5]


def terms(text: str) -> set[str]:
    return _terms(text)


def _terms(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text) if len(w) > 2}


class KnowledgeBase:
    def __init__(self, sections: list[Section], max_chars: int = 6000):
        self.sections = sections
        self.max_chars = max_chars

    @staticmethod
    def load(directory: Path, max_chars: int = 6000) -> "KnowledgeBase":
        sections: list[Section] = []
        for path in sorted(directory.glob("*.md")):
            sections.extend(split_sections(path.read_text(encoding="utf-8"), path.stem))
        if not sections:
            raise ValueError(f"в {directory} нет ни одного раздела базы знаний")
        return KnowledgeBase(sections, max_chars)

    @property
    def total_chars(self) -> int:
        return sum(len(s.render()) for s in self.sections)

    def find(self, title_part: str) -> Section | None:
        part = title_part.lower()
        return next((s for s in self.sections if part in s.title.lower()), None)

    def context_for(self, question: str) -> str:
        """Текст базы для промпта: вся база, если влезает, иначе самые релевантные разделы."""
        if self.total_chars <= self.max_chars:
            return "\n\n".join(s.render() for s in self.sections)
        q = _terms(question)
        ranked = sorted(
            self.sections,
            key=lambda s: len(q & _terms(s.title + " " + s.text)) * 2 + len(q & _terms(s.title)),
            reverse=True,
        )
        picked, used = [], 0
        for s in ranked:
            size = len(s.render()) + 2
            if used + size > self.max_chars:
                continue
            picked.append(s)
            used += size
        picked.sort(key=self.sections.index)  # исходный порядок читается лучше
        return "\n\n".join(s.render() for s in picked)


def split_sections(markdown: str, source: str) -> list[Section]:
    sections: list[Section] = []
    title, lines = source, []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if "".join(lines).strip():
                sections.append(Section(source, title, "\n".join(lines)))
            title, lines = line[3:].strip(), []
        elif line.startswith("# "):
            continue  # заголовок файла не нужен
        else:
            lines.append(line)
    if "".join(lines).strip():
        sections.append(Section(source, title, "\n".join(lines)))
    return sections
