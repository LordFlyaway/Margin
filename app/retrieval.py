"""Find the passages in your documents that answer a question (keyword + meaning, merged)."""
from __future__ import annotations

import re

import numpy as np

from . import db, llm

_STOP = set("a an and are as at be but by can do does for from how i in is it me of on or so that the their "
            "this to was what when where which who why will with would you your explain tell please".split())
_RRF_K = 60


def _fts_query(text: str) -> str:
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in _STOP and len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words[:24])


def _scope(course_id: int | None, document_id: int | None) -> tuple[str, list]:
    clause, args = "d.status='ready'", []
    if course_id:
        clause += " AND d.course_id=?"
        args.append(course_id)
    if document_id:
        clause += " AND d.id=?"
        args.append(document_id)
    return clause, args


def _keyword(query: str, clause: str, args: list, limit: int) -> list[int]:
    fts = _fts_query(query)
    if not fts:
        return []
    rows = db.q(f"""SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id=f.rowid
                    JOIN documents d ON d.id=c.document_id
                    WHERE chunks_fts MATCH ? AND {clause} ORDER BY bm25(chunks_fts) LIMIT ?""",
                fts, *args, limit)
    return [r["id"] for r in rows]


def _semantic(query: str, clause: str, args: list, limit: int) -> list[int]:
    rows = db.q(f"""SELECT c.id,c.embedding FROM chunks c JOIN documents d ON d.id=c.document_id
                    WHERE c.embedding IS NOT NULL AND {clause}""", *args)
    if not rows:
        return []
    vectors = llm.embed([query])
    if not vectors:
        return []
    q = np.asarray(vectors[0], dtype=np.float32)
    matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    scores = matrix @ q / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(q) + 1e-9)
    return [rows[i]["id"] for i in np.argsort(-scores)[:limit]]


def search(query: str, course_id: int | None = None, document_id: int | None = None, k: int = 6) -> list[dict]:
    clause, args = _scope(course_id, document_id)
    scores: dict[int, float] = {}
    for ranked in (_keyword(query, clause, args, 25), _semantic(query, clause, args, 25)):
        for rank, chunk_id in enumerate(ranked):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (_RRF_K + rank)
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    if not top:
        return []
    marks = ",".join("?" * len(top))
    rows = db.q(f"""SELECT c.id,c.page,c.text,c.document_id,d.title FROM chunks c
                    JOIN documents d ON d.id=c.document_id WHERE c.id IN ({marks})""", *top)
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in top if i in by_id]
