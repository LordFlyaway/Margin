"""Local HTTP + websocket API for the UI. Binds to 127.0.0.1 only."""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import assistant, config, convert, db, events, ingest, llm, music, papers, sorting, startup, study
from .voice.engine import engine as voice

STATIC = Path(__file__).parent / "static"
_ORIGINS = {f"http://{config.HOST}:{config.PORT}", f"http://localhost:{config.PORT}"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    events.bind_loop(asyncio.get_running_loop())
    ingest.resume_unfinished()
    yield


app = FastAPI(title="Margin", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[config.HOST, "localhost", "testserver"])


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Stops websites open in a browser from driving this local API."""
    origin = request.headers.get("origin")
    if origin and origin not in _ORIGINS and request.method != "GET":
        return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
    response = await call_next(request)
    if not request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"  # always check for a newer version of the app's pages
    return response


@app.exception_handler(llm.LLMError)
async def _llm_error(_r, e: llm.LLMError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(ValueError)
async def _value_error(_r, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


def _need(row):
    if row is None:
        raise HTTPException(404, "That item no longer exists.")
    return row


# ---- State & settings ---------------------------------------------------

@app.get("/api/state")
def state():
    return {
        "settings": config.load_settings(),
        "claude_configured": config.claude_configured(),
        "claude_models": config.CLAUDE_MODELS,
        "openai_configured": config.configured("openai"),
        "openai_models": config.OPENAI_MODELS,
        "ollama": llm.ollama_status(),
        "voice": voice.status(),
        "start_with_windows": startup.enabled(),
    }


@app.put("/api/settings")
def put_settings(patch: dict = Body(...)):
    if "claude" in (patch.get("provider"), patch.get("ocr_provider")) and not config.claude_configured():
        raise HTTPException(400, "Add your Claude API key first.")
    if patch.get("provider") == "openai" and not config.configured("openai"):
        raise HTTPException(400, "Add your OpenAI API key first.")
    if patch.get("provider") not in (None, "claude", "openai", "ollama"):
        raise HTTPException(400, "Unknown AI provider.")
    if "start_with_windows" in patch:
        startup.set_enabled(bool(patch.pop("start_with_windows")))
    saved = config.save_settings(patch)
    events.changed("settings")
    return saved


class KeyIn(BaseModel):
    key: str = Field(min_length=10)


@app.put("/api/settings/claude-key")
def put_key(body: KeyIn):
    config.set_claude_key(body.key.strip())
    events.changed("settings")
    return {"ok": True}


@app.delete("/api/settings/claude-key")
def delete_key():
    config.set_claude_key(None)
    if not config.claude_configured():
        config.save_settings({"provider": "ollama", "ocr_provider": "ollama"})
    events.changed("settings")
    return {"ok": True}


@app.put("/api/settings/openai-key")
def put_openai_key(body: KeyIn):
    config.set_key("openai", body.key.strip())
    events.changed("settings")
    return {"ok": True}


@app.delete("/api/settings/openai-key")
def delete_openai_key():
    config.set_key("openai", None)
    if not config.configured("openai") and config.load_settings()["provider"] == "openai":
        config.save_settings({"provider": "ollama"})
    events.changed("settings")
    return {"ok": True}


@app.get("/api/openai/models")
def openai_models():
    return {"models": llm.openai_models()}


# ---- Courses and documents ----------------------------------------------

@app.get("/api/courses")
def courses():
    return db.q("""SELECT c.*, (SELECT COUNT(*) FROM documents d WHERE d.course_id=c.id) AS documents
                   FROM courses c WHERE c.name<>? OR documents>0 ORDER BY c.name""", sorting.UNSORTED)


class CourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@app.post("/api/courses")
def add_course(body: CourseIn):
    cid = db.run("INSERT INTO courses(name) VALUES(?)", body.name.strip())
    events.changed("courses")
    return {"id": cid}


@app.delete("/api/courses/{cid}")
def del_course(cid: int):
    for d in db.q("SELECT path FROM documents WHERE course_id=?", cid):
        Path(d["path"]).unlink(missing_ok=True)
    db.run("DELETE FROM courses WHERE id=?", cid)
    if config.load_settings()["focus_course_id"] == cid:
        config.save_settings({"focus_course_id": None})
    events.changed("courses")
    return {"ok": True}


@app.get("/api/documents")
def documents(course_id: int | None = None, kind: str = "notes"):
    sql = """SELECT d.*, c.name AS course FROM documents d JOIN courses c ON c.id=d.course_id WHERE d.kind=?"""
    if course_id:
        return db.q(sql + " AND d.course_id=? ORDER BY d.id DESC", kind, course_id)
    return db.q(sql + " ORDER BY d.id DESC", kind)


@app.post("/api/documents")
async def upload(course_id: int | None = Form(None), kind: str = Form("notes"), files: list[UploadFile] = File(...)):
    if kind not in ("notes", "paper"):
        raise HTTPException(400, "Unknown document type.")
    if kind == "paper" and course_id is None:
        raise HTTPException(400, "Choose the subject these past papers belong to.")
    auto_sort = course_id is None  # no subject chosen: the AI files it
    if auto_sort:
        course_id = sorting.unsorted_id()
    _need(db.one("SELECT id FROM courses WHERE id=?", course_id))
    made, rejected = [], []
    for f in files:
        name = Path(f.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in ingest.SUPPORTED:
            rejected.append(name)
            continue
        target = config.UPLOADS / f"{uuid.uuid4().hex}{ext}"
        target.write_bytes(await f.read())
        stem = Path(name).stem
        title = ingest.clean_paper_title(stem) if kind == "paper" else ingest.clean_title(stem)
        doc_id = db.run("INSERT INTO documents(course_id,title,filename,path,detail,kind) VALUES(?,?,?,?,?,?)",
                        course_id, title, name, str(target), "Waiting to start", kind)
        events.ingest_pool.submit(ingest.process, doc_id, auto_sort)
        made.append(doc_id)
    events.changed("courses")
    events.changed("documents")
    events.changed("papers")
    return {"ids": made, "rejected": rejected}


@app.patch("/api/documents/{doc_id}")
def update_document(doc_id: int, course_id: int | None = Body(None, embed=True),
                    title: str | None = Body(None, embed=True, max_length=120)):
    """Move a document to another subject and/or rename it."""
    doc = _need(db.one("SELECT id,title FROM documents WHERE id=?", doc_id))
    if course_id is not None:
        _need(db.one("SELECT id FROM courses WHERE id=?", course_id))
        sorting.move(doc_id, course_id)
    if title is not None and title.strip() and title.strip() != doc["title"]:
        new = title.strip()
        db.run("UPDATE documents SET title=? WHERE id=?", new, doc_id)
        # Notes and exams were named after the document, e.g. "Notes: <title>"
        for table in ("notes", "quizzes"):
            db.run(f"UPDATE {table} SET title=substr(title,1,instr(title,': '))||' '||? "
                   f"WHERE document_id=? AND substr(title,instr(title,': ')+2)=?", new, doc_id, doc["title"])
        for what in ("documents", "notes", "quizzes"):
            events.changed(what)
    return {"ok": True}


@app.delete("/api/documents/{doc_id}")
def del_document(doc_id: int):
    doc = _need(db.one("SELECT path,source_path FROM documents WHERE id=?", doc_id))
    Path(doc["path"]).unlink(missing_ok=True)
    if doc["source_path"]:
        Path(doc["source_path"]).unlink(missing_ok=True)
    db.run("DELETE FROM documents WHERE id=?", doc_id)
    events.changed("documents")
    events.changed("papers")
    return {"ok": True}


@app.get("/api/documents/{doc_id}/pages/{page}")
def page(doc_id: int, page: int):
    row = db.one("""SELECT p.page,p.text,d.title FROM pages p JOIN documents d ON d.id=p.document_id
                    WHERE p.document_id=? AND p.page=?""", doc_id, page)
    if row is None:  # the AI occasionally cites a page that doesn't exist
        raise HTTPException(404, f"Page {page} isn't in this document, so this citation is wrong.")
    return row


@app.get("/api/documents/{doc_id}/file")
def original(doc_id: int):
    doc = _need(db.one("SELECT path,filename FROM documents WHERE id=?", doc_id))
    return FileResponse(doc["path"], filename=doc["filename"], content_disposition_type="inline")


def _original_path(doc: dict) -> Path:
    path = Path(doc["path"])
    if not path.exists():
        raise HTTPException(404, "The original file is missing. Delete this document and upload it again.")
    return path


@app.get("/api/documents/{doc_id}/view")
def view_document(doc_id: int):
    """What the reader needs to show this document in the app."""
    doc = _need(db.one("SELECT id,title,path,status FROM documents WHERE id=?", doc_id))
    path = _original_path(doc)
    ext = path.suffix.lower()
    if ext in convert.SLIDES and doc["status"] != "processing" and convert.available():
        # Slides uploaded before Margin converted them: make the PDF now, once
        try:
            path = ingest.slides_to_pdf(doc_id)
            ext = ".pdf"
        except convert.ConvertError:
            pass  # show the slide text instead
    base = {"id": doc["id"], "title": doc["title"], "external": ext in (".docx", ".pptx", ".ppt", ".pdf")}
    if ext == ".pdf":
        return {**base, "kind": "pdf"}
    if ext == ".docx":
        import mammoth
        with path.open("rb") as f:
            return {**base, "kind": "word", "html": mammoth.convert_to_html(f).value}
    if ext in convert.SLIDES:
        return {**base, "kind": "slides",
                "pages": db.q("SELECT page,text FROM pages WHERE document_id=? ORDER BY page", doc_id)}
    if ext == ".md":
        return {**base, "kind": "markdown", "text": path.read_text("utf-8", errors="replace")}
    if ext == ".txt":
        return {**base, "kind": "text", "text": path.read_text("utf-8", errors="replace")}
    return {**base, "kind": "image"}


@app.post("/api/documents/{doc_id}/open")
def open_externally(doc_id: int):
    """Open the file in its usual Windows app (Word, PowerPoint, ...)."""
    doc = _need(db.one("SELECT path FROM documents WHERE id=?", doc_id))
    try:
        os.startfile(_original_path(doc))
    except OSError:
        raise HTTPException(400, "Windows has no app installed for this type of file.")
    return {"ok": True}


# ---- Notes --------------------------------------------------------------

@app.post("/api/documents/{doc_id}/notes")
def make_notes(doc_id: int, style: str = Body("detailed", embed=True)):
    if style not in study._STYLES:
        raise HTTPException(400, "Unknown note style.")
    return {"id": study.start_notes(doc_id, style)}


@app.get("/api/notes")
def notes(course_id: int | None = None):
    sql = "SELECT n.id,n.title,n.style,n.status,n.created_at,n.course_id,c.name AS course FROM notes n JOIN courses c ON c.id=n.course_id"
    if course_id:
        return db.q(sql + " WHERE n.course_id=? ORDER BY n.id DESC", course_id)
    return db.q(sql + " ORDER BY n.id DESC")


@app.get("/api/notes/{nid}")
def note(nid: int):
    return _need(db.one("SELECT * FROM notes WHERE id=?", nid))


@app.put("/api/notes/{nid}")
def edit_note(nid: int, body: str = Body(..., embed=True)):
    _need(db.one("SELECT id FROM notes WHERE id=?", nid))
    db.run("UPDATE notes SET body=? WHERE id=?", body, nid)
    return {"ok": True}


@app.delete("/api/notes/{nid}")
def del_note(nid: int):
    db.run("DELETE FROM notes WHERE id=?", nid)
    events.changed("notes")
    return {"ok": True}


# ---- Flashcards ---------------------------------------------------------

@app.post("/api/documents/{doc_id}/cards")
def make_cards(doc_id: int, count: int = Body(20, embed=True)):
    study.start_cards(doc_id, max(1, min(count, 200)))
    return {"ok": True}


def _course_filter(course_id: int | None) -> tuple[str, list]:
    return (" AND course_id=?", [course_id]) if course_id else ("", [])


@app.get("/api/cards/due")
def due(course_id: int | None = None):
    where, args = _course_filter(course_id)
    return db.q(f"SELECT * FROM cards WHERE due<=date('now','localtime'){where} ORDER BY due,RANDOM() LIMIT 100", *args)


@app.get("/api/cards/stats")
def card_stats(course_id: int | None = None):
    where, args = _course_filter(course_id)
    return db.one(f"""SELECT COUNT(*) AS total,
        COALESCE(SUM(due<=date('now','localtime')),0) AS due,
        COALESCE(SUM(interval_days>=21),0) AS mastered FROM cards WHERE 1=1{where}""", *args)


@app.post("/api/cards/{card_id}/review")
def review(card_id: int, grade: int = Body(..., embed=True, ge=0, le=3)):
    try:
        return study.review_card(card_id, grade)
    except KeyError:
        raise HTTPException(404, "That card no longer exists.")


# ---- Practice exams -----------------------------------------------------

@app.post("/api/documents/{doc_id}/quiz")
def make_quiz(doc_id: int, count: int = Body(10, embed=True)):
    return {"id": study.start_quiz(doc_id, max(1, min(count, 60)))}


@app.get("/api/quizzes")
def quizzes(course_id: int | None = None):
    where, args = "", []
    if course_id:
        where, args = " WHERE z.course_id=?", [course_id]
    return db.q(f"""SELECT z.*, c.name AS course, (SELECT COUNT(*) FROM questions q WHERE q.quiz_id=z.id) AS questions
                    FROM quizzes z JOIN courses c ON c.id=z.course_id{where} ORDER BY z.id DESC""", *args)


@app.get("/api/quizzes/{qid}")
def quiz(qid: int):
    z = _need(db.one("SELECT * FROM quizzes WHERE id=?", qid))
    qs = db.q("SELECT * FROM questions WHERE quiz_id=? ORDER BY id", qid)
    for q in qs:
        q["options"] = json.loads(q["options"] or "[]")
        if z["status"] != "graded":  # answers stay on this side until it's submitted
            for hidden in ("answer_index", "model_answer", "explanation"):
                q.pop(hidden)
    return {**z, "items": qs}


@app.post("/api/quizzes/{qid}/submit")
def submit(qid: int, answers: dict[str, str] = Body(..., embed=True)):
    z = _need(db.one("SELECT status FROM quizzes WHERE id=?", qid))
    if z["status"] != "ready":
        raise HTTPException(400, "This practice exam can't be submitted right now.")
    study.submit_quiz(qid, answers)
    return {"ok": True}


@app.delete("/api/quizzes/{qid}")
def del_quiz(qid: int):
    db.run("DELETE FROM quizzes WHERE id=?", qid)
    events.changed("quizzes")
    return {"ok": True}


@app.get("/api/weak-topics")
def weak(course_id: int | None = None):
    return study.weak_topics(course_id)


# ---- Exam plans ---------------------------------------------------------

class ExamIn(BaseModel):
    course_id: int
    name: str = Field(min_length=1, max_length=120)
    date: str
    minutes_per_day: int = Field(ge=15, le=720)
    topics: list[str] = []


@app.post("/api/exams")
def add_exam(body: ExamIn):
    topics = [t.strip() for t in body.topics if t.strip()]
    return {"id": study.start_exam(body.course_id, body.name.strip(), body.date, body.minutes_per_day, topics)}


@app.get("/api/exams")
def exams():
    rows = db.q("""SELECT e.*, c.name AS course,
                   (SELECT COUNT(*) FROM tasks t WHERE t.exam_id=e.id) AS total,
                   (SELECT COUNT(*) FROM tasks t WHERE t.exam_id=e.id AND t.done=1) AS done
                   FROM exams e JOIN courses c ON c.id=e.course_id ORDER BY e.date""")
    for r in rows:
        r["topics"] = json.loads(r["topics"])
        r["days_left"] = (date.fromisoformat(r["date"]) - date.today()).days
        r["tasks"] = db.q("SELECT * FROM tasks WHERE exam_id=? ORDER BY date,id", r["id"])
    return rows


@app.post("/api/exams/{eid}/rebuild")
def rebuild(eid: int):
    _need(db.one("SELECT id FROM exams WHERE id=?", eid))
    study.rebuild_exam(eid)
    return {"ok": True}


@app.delete("/api/exams/{eid}")
def del_exam(eid: int):
    db.run("DELETE FROM exams WHERE id=?", eid)
    events.changed("exams")
    return {"ok": True}


@app.patch("/api/tasks/{tid}")
def tick(tid: int, done: bool = Body(..., embed=True)):
    db.run("UPDATE tasks SET done=?, done_at=CASE WHEN ? THEN datetime('now','localtime') END WHERE id=?",
           int(done), int(done), tid)
    events.changed("exams")
    return {"ok": True}


@app.get("/api/today")
def today():
    d = date.today().isoformat()
    tasks = db.q("""SELECT t.*, e.name AS exam, c.name AS course FROM tasks t JOIN exams e ON e.id=t.exam_id
                    JOIN courses c ON c.id=e.course_id
                    WHERE t.date<=? AND t.done=0 AND e.date>=? ORDER BY t.date,t.id""", d, d)
    completed = db.q("""SELECT t.*, e.name AS exam, c.name AS course FROM tasks t JOIN exams e ON e.id=t.exam_id
                        JOIN courses c ON c.id=e.course_id
                        WHERE t.done=1 AND (date(t.done_at)=? OR (t.done_at IS NULL AND t.date=?))
                        ORDER BY t.done_at,t.id""", d, d)
    next_exam = db.one("SELECT e.*, c.name AS course FROM exams e JOIN courses c ON c.id=e.course_id WHERE e.date>=? ORDER BY e.date LIMIT 1", d)
    if next_exam:
        next_exam["days_left"] = (date.fromisoformat(next_exam["date"]) - date.today()).days
    return {"tasks": tasks, "completed": completed, "cards": card_stats(None), "weak": study.weak_topics()[:5], "next_exam": next_exam,
            "documents": db.one("SELECT COUNT(*) AS n FROM documents")["n"]}


# ---- Assistant ----------------------------------------------------------

ATTACHMENTS = config.DATA / "attachments"
_ATTACH_TYPES = (ingest.SUPPORTED - {".ppt"}) | {".gif", ".bmp"}  # an old .ppt needs PowerPoint to read
_ATTACH_MAX = 25 * 1024 * 1024
_ATTACH_FILES = 6


@app.get("/api/chat")
def chat():
    rows = list(reversed(db.q("SELECT * FROM messages ORDER BY id DESC LIMIT 60")))
    for r in rows:
        r["sources"] = json.loads(r["sources"])
        r["attachments"] = json.loads(r.get("attachments") or "[]")
    return rows


@app.post("/api/chat")
async def send(request: Request):
    """JSON {text}, or a form with text and up to six attached files or pictures."""
    if request.headers.get("content-type", "").startswith("multipart/"):
        form = await request.form()
        text = str(form.get("text") or "")
        uploads = [f for f in form.getlist("files") if hasattr(f, "filename") and f.filename]
    else:
        text, uploads = str((await request.json()).get("text") or ""), []
    if not text.strip() and not uploads:
        raise HTTPException(400, "Type a message or attach a file.")
    if len(uploads) > _ATTACH_FILES:
        raise HTTPException(400, f"Attach up to {_ATTACH_FILES} files at a time.")
    ATTACHMENTS.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in uploads:
        ext = Path(f.filename).suffix.lower()
        if ext not in _ATTACH_TYPES:
            raise HTTPException(400, f"{f.filename}: Margin can read PDF, Word, PowerPoint, text and picture files.")
        data = await f.read()
        if len(data) > _ATTACH_MAX:
            raise HTTPException(400, f"{f.filename} is over 25 MB.")
        file = f"{uuid.uuid4().hex}{ext}"
        (ATTACHMENTS / file).write_bytes(data)
        saved.append({"name": Path(f.filename).name[:120], "file": file, "path": str(ATTACHMENTS / file)})
    events.chat_pool.submit(assistant.ask, text, "text", None, saved)
    return {"ok": True}


@app.get("/api/attachments/{file}")
def attachment(file: str):
    path = ATTACHMENTS / Path(file).name
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z]{2,4}", file) or not path.exists():
        raise HTTPException(404, "That attachment no longer exists.")
    return FileResponse(path)


@app.delete("/api/chat")
def clear_chat():
    db.run("DELETE FROM messages")
    for f in ATTACHMENTS.glob("*") if ATTACHMENTS.exists() else []:
        f.unlink(missing_ok=True)
    events.changed("chat")
    return {"ok": True}


# ---- Papers -------------------------------------------------------------

@app.get("/api/papers")
def papers_list(course_id: int):
    past = db.q("""SELECT id,title,status,detail,page_count FROM documents
                   WHERE course_id=? AND kind='paper' ORDER BY title""", course_id)
    made = db.q("SELECT id,title,status,detail,created_at,body FROM papers WHERE course_id=? ORDER BY id DESC", course_id)
    for p in made:
        body = json.loads(p.pop("body") or "{}")
        p["total_marks"], p["exam_name"] = body.get("total_marks"), body.get("exam_name")
    return {"past": past, "made": made}


class PaperIn(BaseModel):
    course_id: int
    document_ids: list[int] = []


@app.post("/api/papers")
def papers_make(body: PaperIn):
    return {"id": papers.start(body.course_id, body.document_ids or None)}


@app.get("/api/papers/{pid}")
def paper(pid: int):
    row = _need(db.one("SELECT p.*, c.name AS course FROM papers p JOIN courses c ON c.id=p.course_id WHERE p.id=?", pid))
    row["body"] = json.loads(row["body"] or "{}")
    return row


@app.delete("/api/papers/{pid}")
def paper_delete(pid: int):
    db.run("DELETE FROM papers WHERE id=?", pid)
    events.changed("papers")
    return {"ok": True}


# ---- Music --------------------------------------------------------------

@app.get("/api/music")
def music_list():
    return db.q("SELECT * FROM music ORDER BY id DESC")


@app.post("/api/music")
def music_add(link: str = Body(..., embed=True)):
    parsed = music.parse_link(link)
    if not parsed:
        raise HTTPException(400, "That isn't a Spotify link. In Spotify choose Share, then Copy link.")
    kind, spotify_id = parsed
    info = music.lookup(kind, spotify_id)
    db.run("INSERT OR IGNORE INTO music(kind,spotify_id,title,thumbnail) VALUES(?,?,?,?)",
           kind, spotify_id, info["title"], info["thumbnail"])
    events.changed("music")
    return {"ok": True}


@app.delete("/api/music/{mid}")
def music_remove(mid: int):
    db.run("DELETE FROM music WHERE id=?", mid)
    events.changed("music")
    return {"ok": True}


@app.post("/api/music/{mid}/open")
def music_open(mid: int):
    row = _need(db.one("SELECT kind,spotify_id FROM music WHERE id=?", mid))
    try:
        os.startfile(f"spotify:{row['kind']}:{row['spotify_id']}")
    except OSError:
        raise HTTPException(400, "Spotify doesn't seem to be installed.")
    return {"ok": True}


@app.get("/api/music/now")
def music_now():
    return music.now_playing()


@app.get("/api/music/art")
def music_art(k: str | None = None):
    art = music.album_art(k)
    if art is None:
        raise HTTPException(404, "No album art right now.")
    data, content_type = art
    return Response(data, media_type=content_type if content_type.startswith("image/") else "image/png",
                    headers={"Cache-Control": "max-age=3600"})


@app.put("/api/music/volume")
def music_volume(value: int = Body(..., embed=True, ge=0, le=100)):
    level = music.set_volume(value)
    if level is None:
        raise HTTPException(400, "Spotify isn't playing sound yet. Start a song first.")
    return {"volume": level}


@app.post("/api/music/control")
def music_control(action: str = Body(..., embed=True)):
    if action not in ("toggle", "play", "pause", "next", "previous"):
        raise HTTPException(400, "Unknown music control.")
    if not music.control(action):
        raise HTTPException(400, "Open Spotify on this PC first.")
    return {"ok": True}


@app.post("/api/window/show")
def show_window():
    events.publish({"type": "window.show"})
    return {"ok": True}


@app.post("/api/voice/trigger")
def trigger():
    voice.trigger()
    return voice.status()


@app.post("/api/voice/mute")
def mute(muted: bool = Body(..., embed=True)):
    voice.set_muted(muted)
    return voice.status()


@app.websocket("/ws")
async def ws(socket: WebSocket):
    if socket.headers.get("origin") not in _ORIGINS:
        await socket.close(code=1008)
        return
    await socket.accept()
    q = events.add_client()
    try:
        while True:
            await socket.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        events.remove_client(q)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
