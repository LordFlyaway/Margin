"""Turn uploaded files into per-page text, then searchable chunks."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path

import numpy as np

from . import convert, db, events, llm, sorting

SUPPORTED = {".pdf", ".pptx", ".ppt", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp"}
_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
_SCANNED_BELOW = 5  # a PDF page with almost no text gets read as an image. Short title slides are fine as they are
_CHUNK, _OVERLAP = 900, 150

_OCR_SYSTEM = "You transcribe photos and scans of study material accurately."
_OCR_PROMPT = (
    "Transcribe all the text in this image in reading order. Keep headings and lists. "
    "Write maths in LaTeX between $ signs. Describe any diagram in one bracketed line. "
    "Output only the transcription."
)


def _b64_jpeg(img) -> str:
    img = img.convert("RGB")
    img.thumbnail((1800, 1800))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _read_image_page(img) -> str:
    return llm.read_image(_OCR_PROMPT, _OCR_SYSTEM, _b64_jpeg(img))


def extract_pages(path: Path, progress=lambda s: None, known: dict[int, str] | None = None,
                  save=lambda page, text: None) -> list[tuple[int, str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path, progress, known or {}, save)
    if ext == ".pptx":
        return _pptx(path)
    if ext == ".docx":
        return _docx(path)
    if ext in _IMAGE:
        from PIL import Image
        progress("Reading the photo")
        with Image.open(path) as img:
            return [(1, _read_image_page(img))]
    text = path.read_text("utf-8", errors="replace")
    return _paginate(text)


def _pdf(path: Path, progress, known: dict[int, str], save) -> list[tuple[int, str]]:
    import fitz
    from PIL import Image
    out = []
    with fitz.open(path) as doc:
        total = doc.page_count
        for i, page in enumerate(doc, start=1):
            if i in known:  # read before Margin was closed
                out.append((i, known[i]))
                continue
            text = page.get_text("text").strip()
            if len(text) < _SCANNED_BELOW and (page.get_images() or page.get_drawings()):
                llm.yield_to_questions()
                progress(f"Reading scanned page {i} of {total}")
                pix = page.get_pixmap(dpi=170)
                text = _read_image_page(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
            elif i % 25 == 0:
                progress(f"Reading page {i} of {total}")
            if text.strip():
                out.append((i, text.strip()))
                save(i, text.strip())
    return out


def _pptx(path: Path) -> list[tuple[int, str]]:
    from pptx import Presentation
    out = []

    def shape_text(shape) -> list[str]:
        found = []
        if shape.shape_type == 6:  # group
            for s in shape.shapes:
                found += shape_text(s)
        if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            found.append(shape.text_frame.text.strip())
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                found.append(" | ".join(c.text.strip() for c in row.cells))
        return found

    for i, slide in enumerate(Presentation(str(path)).slides, start=1):
        parts = [t for s in slide.shapes for t in shape_text(s)]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            speaker = slide.notes_slide.notes_text_frame.text.strip()
            if speaker:
                parts.append("Speaker notes: " + speaker)
        if parts:
            out.append((i, "\n".join(parts)))
    return out


def _docx(path: Path) -> list[tuple[int, str]]:
    from docx import Document
    d = Document(str(path))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        parts += [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
    return _paginate("\n\n".join(parts))


def _paginate(text: str, size: int = 3000) -> list[tuple[int, str]]:
    """Word and text files have no fixed pages, so make stable ones to cite."""
    pages, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > size:
            pages.append(cur.strip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        pages.append(cur.strip())
    return list(enumerate(pages, start=1))


_SECTION = re.compile(r"(?<![a-z])(module|unit|chapter|lecture|week)[\s_\-.]*([0-9]{1,3}|[ivx]{1,5})(?![a-z0-9])", re.I)
_ROMAN = {"i": 1, "v": 5, "x": 10}


def _roman(text: str) -> int:
    values = [_ROMAN[c] for c in text.lower()]
    return sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))


def clean_title(stem: str) -> str:
    """Short display name: "FALLSEM2025-26_..._Module-I" becomes "Module 1"."""
    m = _SECTION.search(stem)
    if m:
        word, number = m.group(1).capitalize(), m.group(2)
        return f"{word} {number if number.isdigit() else _roman(number)}"
    title = re.sub(r"\s*\(\d+\)$", "", stem)  # "(1)" added by Windows for duplicate downloads
    return re.sub(r"[_]+", " ", title).strip() or stem


def clean_paper_title(stem: str) -> str:
    """Past papers keep their names (they say which exam and year), just tidied."""
    title = re.sub(r"\s*\(\d+\)$", "", stem)
    return re.sub(r"[_]+", " ", title).strip() or stem


def chunk_text(text: str, size: int = _CHUNK, overlap: int = _OVERLAP) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Prefer to break on a paragraph, then a sentence.
            window = text[start + size // 2:end]
            cut = max(window.rfind("\n"), window.rfind(". "))
            if cut != -1:
                end = start + size // 2 + cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def slides_to_pdf(doc_id: int) -> Path:
    """Replace a slide deck with a PDF of it; the deck is kept as the source."""
    doc = db.one("SELECT path,filename FROM documents WHERE id=?", doc_id)
    source = Path(doc["path"])
    pdf = convert.to_pdf(source)
    db.run("UPDATE documents SET path=?, source_path=?, filename=? WHERE id=?",
           str(pdf), str(source), Path(doc["filename"]).stem + ".pdf", doc_id)
    events.changed("documents")
    return pdf


def process(doc_id: int, auto_sort: bool = False) -> None:
    doc = db.one("SELECT * FROM documents WHERE id=?", doc_id)
    if not doc:
        return

    def progress(detail: str) -> None:
        db.run("UPDATE documents SET detail=? WHERE id=?", detail, doc_id)
        events.changed("documents")

    try:
        known = {r["page"]: r["text"] for r in db.q("SELECT page,text FROM pages WHERE document_id=?", doc_id)}

        def save(page: int, text: str) -> None:
            db.run("INSERT OR REPLACE INTO pages(document_id,page,text) VALUES(?,?,?)", doc_id, page, text)

        path = Path(doc["path"])
        read_from = Path(doc["source_path"]) if doc.get("source_path") else path
        if path.suffix.lower() in convert.SLIDES:
            progress("Turning the slides into a PDF")
            try:
                pdf = slides_to_pdf(doc_id)
                # python-pptx reads .pptx text and speaker notes; an old .ppt is read from its PDF
                read_from = path if path.suffix.lower() == ".pptx" else pdf
            except convert.ConvertError:
                if path.suffix.lower() == ".ppt":
                    raise  # no other way to read an old .ppt
                # .pptx without PowerPoint: keep the deck, the reader shows its text
        pages = extract_pages(read_from, progress, known, save)
        if not pages:
            raise llm.LLMError("No readable text was found in this file.")
        db.many("INSERT OR REPLACE INTO pages(document_id,page,text) VALUES(?,?,?)",
                [(doc_id, p, t) for p, t in pages])
        db.run("DELETE FROM chunks WHERE document_id=?", doc_id)
        rows = [(doc_id, p, c) for p, t in pages for c in chunk_text(t)]
        db.many("INSERT INTO chunks(document_id,page,text) VALUES(?,?,?)", rows)
        progress("Indexing for search")
        _embed_document(doc_id)
        if auto_sort:
            progress("Working out the subject")
            sorting.sort_document(doc_id)
        db.run("UPDATE documents SET status='ready', detail=NULL, page_count=? WHERE id=?", len(pages), doc_id)
        events.changed("documents")
    except Exception as e:
        db.run("UPDATE documents SET status='failed', detail=? WHERE id=?", str(e) or "Couldn't read this file.", doc_id)
        events.changed("documents")


def resume_unfinished() -> None:
    """Carry on with anything Margin was still reading when it closed."""
    rows = db.q("""SELECT d.id, c.name FROM documents d JOIN courses c ON c.id=d.course_id
                   WHERE d.status='processing' ORDER BY d.id""")
    for r in rows:
        events.ingest_pool.submit(process, r["id"], r["name"] == sorting.UNSORTED)


def _embed_document(doc_id: int, batch: int = 32) -> None:
    rows = db.q("SELECT id,text FROM chunks WHERE document_id=? AND embedding IS NULL", doc_id)
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        vectors = llm.embed([r["text"] for r in part])
        if not vectors:
            return  # Ollama is off; keyword search still covers this document
        db.many("UPDATE chunks SET embedding=? WHERE id=?",
                [(np.asarray(v, dtype=np.float32).tobytes(), r["id"]) for v, r in zip(vectors, part)])
