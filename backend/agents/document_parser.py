"""
AGENT #1 — DocumentParser
Reads PDF/EPUB/TXT and extracts only chapter content for downstream audio.
"""
import re
from pathlib import Path
from typing import List, Optional

from loguru import logger

try:
    from ..models.schemas import Chapter
except ImportError:
    from models.schemas import Chapter


CHAPTER_TITLE_RE = re.compile(
    r"^(?:chương|chapter|hồi|phần|quyển)\s*[\divxlcdm一二三四五六七八九十百千\w\-_: ]+",
    re.IGNORECASE,
)


class DocumentParser:
    """Parses source files into clean chapters and speech segments."""

    def parse(
        self,
        file_path: str,
        start_chapter: Optional[int] = None,
        end_chapter: Optional[int] = None,
    ) -> List[Chapter]:
        path = Path(file_path)
        suffix = path.suffix.lower()

        logger.info(f"[DocParser] Parsing {suffix} file: {path.name}")

        if suffix == ".pdf":
            return self._parse_pdf(path)
        if suffix == ".epub":
            return self._parse_epub(path, start_chapter=start_chapter, end_chapter=end_chapter)
        if suffix == ".txt":
            return self._parse_txt(path)
        raise ValueError(f"Unsupported file type: {suffix}")

    def _parse_pdf(self, path: Path) -> List[Chapter]:
        try:
            import fitz
        except ImportError as exc:
            raise ImportError("PyMuPDF not installed. Run: pip install PyMuPDF") from exc

        doc = fitz.open(str(path))
        raw_text = "".join(page.get_text() for page in doc)
        doc.close()
        return self._split_into_chapters(raw_text)

    def _parse_epub(
        self,
        path: Path,
        start_chapter: Optional[int] = None,
        end_chapter: Optional[int] = None,
    ) -> List[Chapter]:
        try:
            import ebooklib
            from ebooklib import epub
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ImportError("ebooklib/beautifulsoup4 not installed.") from exc

        book = epub.read_epub(str(path))
        chapters: List[Chapter] = []
        chapter_idx = 0
        seen_real_chapter = False
        start_idx = max(0, (start_chapter or 1) - 1)
        end_idx = (end_chapter if end_chapter is not None else 999999)

        for item in book.get_items():
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue

            content = item.get_content().decode("utf-8", errors="ignore")
            soup = BeautifulSoup(content, "lxml")
            title = self._clean_text(self._extract_title(soup) or "")
            text = self._clean_text(soup.get_text(separator="\n"))
            text = self._trim_non_story_content(text)

            if len(text) < 180:
                continue

            looks_like_chapter = self._looks_like_chapter_title(title) or self._has_embedded_chapter_heading(text)
            if not seen_real_chapter and not looks_like_chapter:
                logger.info(f"[DocParser] Skipping front matter: {title[:60] or item.get_name()}")
                continue

            seen_real_chapter = True
            if self._has_embedded_chapter_heading(text):
                split_chapters = self._split_into_chapters(text)
                if split_chapters:
                    for chapter in split_chapters:
                        chapter.index = chapter_idx
                        if start_idx <= chapter_idx < end_idx:
                            chapters.append(chapter)
                        chapter_idx += 1
                        if chapter_idx >= end_idx:
                            return chapters
                    continue
                else:
                    # It had chapter headings but _split_into_chapters returned empty (e.g. TOC file)
                    logger.info(f"[DocParser] Skipping TOC-like file: {title[:60]}")
                    continue

            chapter_title = title if self._looks_like_chapter_title(title) else f"Chương {chapter_idx + 1}"
            if start_idx <= chapter_idx < end_idx:
                chapters.append(
                    Chapter(index=chapter_idx, title=chapter_title, raw_text=text)
                )
            chapter_idx += 1
            logger.info(f"[DocParser] Found chapter: {chapter_title} ({len(text)} chars)")
            if chapter_idx >= end_idx:
                break

        return chapters

    def _parse_txt(self, path: Path) -> List[Chapter]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return self._split_into_chapters(text)

    def _split_into_chapters(self, raw_text: str) -> List[Chapter]:
        raw_text = self._clean_text(raw_text)
        chapter_pattern = re.compile(
            r"""(?:^|\n)
            (?:
                (?:Chương|CHƯƠNG|Chapter|CHAPTER|Hồi|HỒI|Phần|PHẦN|Quyển|QUYỂN)\s*[\d\wIVXLCDM一二三四五六七八九十百千\-_:. ]+[^\n]* |
                (?:第\s*[\d零一二三四五六七八九十百千]+\s*[章回節]) |
                (?:【\d+】[^\n]*)
            )""",
            re.VERBOSE | re.MULTILINE,
        )
        splits = list(chapter_pattern.finditer(raw_text))

        if not splits:
            logger.warning("[DocParser] No chapter headings found, treating as single chapter")
            return [Chapter(index=0, title="Toàn bộ tác phẩm", raw_text=self._trim_non_story_content(raw_text))]

        # Filter out Table of Contents (TOC) entries
        is_short = []
        for i in range(len(splits)):
            end = splits[i + 1].start() if i + 1 < len(splits) else len(raw_text)
            is_short.append((end - splits[i].end()) < 300)

        valid_splits = []
        i = 0
        while i < len(splits):
            if is_short[i]:
                j = i
                while j < len(splits) and is_short[j]:
                    j += 1
                # If there are >= 2 short matches in a row, it's a TOC block.
                # The match at `j` (if exists) is the last TOC entry containing the intro text.
                if (j - i) >= 2:
                    i = j + 1  # Skip the entire TOC block including the last entry
                    continue
                else:
                    valid_splits.append(splits[i])
                    i += 1
            else:
                valid_splits.append(splits[i])
                i += 1

        if not valid_splits:
            return []

        chapters: List[Chapter] = []
        for i, match in enumerate(valid_splits):
            start = match.start()
            end = valid_splits[i + 1].start() if i + 1 < len(valid_splits) else len(raw_text)
            chunk = raw_text[start:end].strip()
            title_line = self._clean_text(match.group().strip())
            body = chunk[len(match.group().strip()):].strip()
            body = self._trim_non_story_content(body)
            if len(body) < 80:
                continue

            chapters.append(Chapter(index=len(chapters), title=title_line, raw_text=body))
            logger.info(f"[DocParser] Chapter {len(chapters)-1}: {title_line[:50]}... ({len(body)} chars)")

        return chapters

    def _clean_text(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\f", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.M)
        text = re.sub(r"https?://\S+", "", text)
        return text.strip()

    def _trim_non_story_content(self, text: str) -> str:
        lines = [line.rstrip() for line in text.splitlines()]
        while lines and self._is_metadata_line(lines[0].strip()):
            lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        return "\n".join(lines).strip()

    def _is_metadata_line(self, line: str) -> bool:
        lowered = line.lower()
        if not lowered:
            return True
        metadata_markers = [
            "tác giả:",
            "nguồn:",
            "dịch giả:",
            "biên tập:",
            "văn án",
            "giới thiệu",
            "mục lục",
            "truyện được",
            "ebook",
            "©",
        ]
        return any(lowered.startswith(marker) for marker in metadata_markers)

    def _extract_title(self, soup) -> Optional[str]:
        for tag in ["h1", "h2", "h3", "title"]:
            el = soup.find(tag)
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)
        return None

    def _looks_like_chapter_title(self, title: str) -> bool:
        return bool(title and CHAPTER_TITLE_RE.match(title.strip()))

    def _has_embedded_chapter_heading(self, text: str) -> bool:
        first_lines = "\n".join(text.splitlines()[:10])
        return bool(re.search(CHAPTER_TITLE_RE, first_lines))

    def split_into_segments(self, chapter: Chapter, max_chars: int = 260) -> Chapter:
        try:
            from ..models.schemas import TextSegment, CharacterType
        except ImportError:
            from models.schemas import TextSegment, CharacterType

        paragraph_candidates = [
            piece.strip()
            for piece in re.split(r"\n{2,}", chapter.raw_text)
            if piece.strip()
        ]

        segments = []
        for paragraph in paragraph_candidates:
            segments.extend(self._paragraph_to_segments(paragraph, max_chars))

        chapter.segments = [
            TextSegment(
                index=i,
                text=text,
                is_dialog=self._is_dialog_segment(text),
                character_type=CharacterType.NARRATOR if not self._is_dialog_segment(text) else CharacterType.MALE_YOUNG,
            )
            for i, text in enumerate(segments)
        ]
        return chapter

    def _paragraph_to_segments(self, paragraph: str, max_chars: int) -> List[str]:
        paragraph = self._clean_dialog_prefix(paragraph)
        dialog_turns = self._extract_dialog_turns(paragraph)
        if len(dialog_turns) >= 2:
            return [turn.strip() for turn in dialog_turns if turn.strip()]

        paragraph = re.sub(r'([.!?…]["”»]?)\s+(?=[“"«])', r"\1\n", paragraph)
        paragraph = re.sub(r'(["”»][^"\n]{0,80}[.!?…])\s+(?=[“"«])', r"\1\n", paragraph)
        pieces = re.split(r"\n+|(?<=:)\s+(?=[“\"«])", paragraph)
        segments: List[str] = []
        buffer = ""

        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            candidate = f"{buffer} {piece}".strip() if buffer else piece
            if buffer and len(candidate) > max_chars:
                segments.append(buffer.strip())
                buffer = piece
            else:
                buffer = candidate

        if buffer:
            segments.append(buffer.strip())
        return segments

    def _extract_dialog_turns(self, paragraph: str) -> List[str]:
        pattern = re.compile(
            r'([“"«][^“”"«»]+[”"»](?:[^“”"«»\n]{0,70}?(?:nói|hỏi|đáp|quát|thét|mắng|cười|thì thầm|gằn giọng)[^“”"«»\n]{0,40})?)'
        )
        return pattern.findall(paragraph)

    def _clean_dialog_prefix(self, text: str) -> str:
        return re.sub(r"^[\-–—]\s*", "", text)

    def _is_dialog_segment(self, text: str) -> bool:
        stripped = text.strip()
        return bool(
            stripped.startswith(("\"", "“", "”", "«", "-", "–", "—"))
            or re.match(r"^[A-ZÀ-Ỵ][^:]{0,30}:\s", stripped)
        )
