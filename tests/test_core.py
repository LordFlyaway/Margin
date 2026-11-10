import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ["MARGIN_DATA"] = tempfile.mkdtemp(prefix="margin-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app import db, events, ingest, llm, planning, retrieval, study
from app.voice.engine import speakable, split_sentences


@pytest.fixture(scope="module")
def client():
    from app.server import app
    with TestClient(app) as c:
        yield c


def _card(**k):
    return planning.CardState(**{"ease": 2.5, "interval_days": 0, "reps": 0, "lapses": 0, **k})


def test_review_intervals_grow_and_again_resets():
    first = planning.review(_card(), planning.GOOD)
    second = planning.review(first, planning.GOOD)
    third = planning.review(second, planning.GOOD)
    assert (first.interval_days, second.interval_days) == (1, 4)
    assert third.interval_days == 10
    lapsed = planning.review(third, planning.AGAIN)
    assert lapsed.interval_days == 0 and lapsed.reps == 0 and lapsed.lapses == 1


def test_ease_never_drops_below_floor():
    c = _card(ease=1.3, reps=3, interval_days=5)
    assert planning.review(c, planning.HARD).ease == 1.3


def test_plan_fits_budget_and_ends_with_mock():
    today = date(2026, 11, 1)
    tasks = planning.build_plan([f"T{i}" for i in range(6)], today + timedelta(days=14), today, 90)
    by_day = {}
    for t in tasks:
        by_day[t["date"]] = by_day.get(t["date"], 0) + t["minutes"]
    assert all(v <= 90 for v in by_day.values())
    assert tasks[-1]["kind"] == "mock" and tasks[-1]["date"] == "2026-11-14"
    assert sum(t["kind"] == "learn" for t in tasks) == 6
    assert sum(t["kind"] == "review" for t in tasks) >= 6


def test_plan_puts_weak_topics_first_and_longer():
    today = date(2026, 11, 1)
    tasks = planning.build_plan(["A", "B"], today + timedelta(days=10), today, 120, weak={"B"})
    learn = [t for t in tasks if t["kind"] == "learn"]
    assert learn[0]["topic"] == "B" and learn[0]["minutes"] == 60 and learn[1]["minutes"] == 45


def test_plan_squeezes_when_time_is_short():
    today = date(2026, 11, 1)
    tasks = planning.build_plan([f"T{i}" for i in range(4)], today + timedelta(days=2), today, 60)
    assert sum(t["kind"] == "learn" for t in tasks) == 4
    assert sum(t["minutes"] for t in tasks if t["kind"] == "learn") <= 60


def test_plan_rejects_past_exam():
    with pytest.raises(ValueError):
        planning.build_plan(["A"], date(2026, 1, 1), date(2026, 1, 1), 60)


def test_chunks_cover_text_and_respect_size():
    text = ". ".join(f"Sentence number {i} about the kernel" for i in range(200))
    chunks = ingest.chunk_text(text)
    assert all(len(c) <= 900 for c in chunks)
    assert "number 0" in chunks[0] and "number 199" in chunks[-1]


def test_split_sentences_streaming():
    done, rest = split_sentences("Paging maps memory. It uses fram")
    assert done == ["Paging maps memory."] and rest == "It uses fram"


def test_speakable_strips_citations_and_markdown():
    assert speakable("**TLB** caches pages [p. 12].") == "TLB caches pages ."


def test_silence_after_trigger_returns_to_idle():
    import numpy as np
    from types import SimpleNamespace as NS
    from app.voice.engine import FRAME, NO_SPEECH_S, RATE, VoiceEngine
    import torch
    v = VoiceEngine(ask=lambda *a: {})
    v._vad = lambda *_a: torch.tensor(0.0)
    v._vad.reset_states = lambda: None
    v._wake = NS(predict=lambda _f: {}, reset=lambda: None)
    v._interrupt = lambda: None
    v.state = "idle"
    v.trigger()
    silent = np.zeros(FRAME, dtype=np.int16)
    v.feed(silent)
    assert v.state == "listening"
    for _ in range(int(NO_SPEECH_S * RATE / FRAME) + 2):
        v.feed(silent)
    assert v.state == "idle"


def test_question_validation():
    good = {"kind": "mcq", "prompt": "Q", "options": ["a", "b"], "answer_index": 1, "model_answer": "b"}
    assert study.valid_question(good)
    assert not study.valid_question({**good, "answer_index": 2})
    assert not study.valid_question({"kind": "short", "prompt": "Q", "options": [], "answer_index": -1, "model_answer": " "})


def _ready_doc(client, name, pages):
    cid = client.post("/api/courses", json={"name": name}).json()["id"]
    did = db.run("INSERT INTO documents(course_id,title,filename,path,status) VALUES(?,?,?,?, 'ready')",
                 cid, name, f"{name}.txt", "missing")
    db.many("INSERT INTO pages VALUES(?,?,?)", [(did, i + 1, t) for i, t in enumerate(pages)])
    db.many("INSERT INTO chunks(document_id,page,text) VALUES(?,?,?)", [(did, i + 1, t) for i, t in enumerate(pages)])
    return cid, did


def test_upload_reads_text_and_finds_it(client, monkeypatch):
    monkeypatch.setattr(llm, "embed", lambda _t: None)
    cid = client.post("/api/courses", json={"name": "OS"}).json()["id"]
    r = client.post("/api/documents", data={"course_id": cid},
                    files=[("files", ("paging.txt", b"A translation lookaside buffer caches page table entries.", "text/plain")),
                           ("files", ("virus.exe", b"x", "application/octet-stream"))])
    assert r.json()["rejected"] == ["virus.exe"]
    events.pool.submit(lambda: None).result()
    for _ in range(50):
        doc = client.get(f"/api/documents?course_id={cid}").json()[0]
        if doc["status"] != "processing":
            break
        import time; time.sleep(0.05)
    assert doc["status"] == "ready", doc
    hits = retrieval.search("what does the translation lookaside buffer do", cid)
    assert hits and hits[0]["page"] == 1


def test_sections_tag_pages_and_respect_limit(client):
    _, did = _ready_doc(client, "Long", ["x" * 500, "y" * 500, "z" * 1500])
    parts = study.sections(did, limit=800)
    assert all(len(p) <= 800 for p in parts)
    assert "[p. 2]" in "".join(parts) and "[p. 3]" in parts[-1]


def test_quiz_hides_answers_until_graded(client, monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda p, s, schema, effort="high":
                        {"score": 0.5, "feedback": "Partly right."} if schema is study._GRADE_SCHEMA else
                        {"questions": [
                            {"kind": "mcq", "prompt": "2+2", "options": ["3", "4"], "answer_index": 1, "model_answer": "4",
                             "explanation": "Sum", "topic": "Arithmetic", "page": 1},
                            {"kind": "short", "prompt": "Define sum", "options": [], "answer_index": -1, "model_answer": "Addition",
                             "explanation": "Def", "topic": "Arithmetic", "page": 1}]})
    cid, did = _ready_doc(client, "Maths", ["two plus two"])
    qid = client.post(f"/api/documents/{did}/quiz", json={"count": 2}).json()["id"]
    events.pool.submit(lambda: None).result()
    _wait(lambda: client.get(f"/api/quizzes/{qid}").json()["status"] == "ready")
    quiz = client.get(f"/api/quizzes/{qid}").json()
    assert "answer_index" not in quiz["items"][0] and "model_answer" not in quiz["items"][1]
    a, b = quiz["items"]
    client.post(f"/api/quizzes/{qid}/submit", json={"answers": {str(a["id"]): "1", str(b["id"]): "adding"}})
    _wait(lambda: client.get(f"/api/quizzes/{qid}").json()["status"] == "graded")
    graded = client.get(f"/api/quizzes/{qid}").json()
    assert graded["score"] == 0.75 and graded["items"][0]["answer_index"] == 1
    assert client.get(f"/api/weak-topics?course_id={cid}").json() == []


def _uploaded(client, monkeypatch, name, data):
    monkeypatch.setattr(llm, "embed", lambda _t: None)
    cid = client.post("/api/courses", json={"name": f"View {name}"}).json()["id"]
    doc_id = client.post("/api/documents", data={"course_id": cid}, files=[("files", (name, data))]).json()["ids"][0]
    _wait(lambda: db.one("SELECT status FROM documents WHERE id=?", doc_id)["status"] != "processing")
    return doc_id


def test_word_file_opens_as_html(client, monkeypatch):
    import io
    from docx import Document
    d = Document()
    d.add_heading("Deadlocks", 1)
    d.add_paragraph("Four conditions are required.")
    buf = io.BytesIO()
    d.save(buf)
    doc_id = _uploaded(client, monkeypatch, "os.docx", buf.getvalue())
    v = client.get(f"/api/documents/{doc_id}/view").json()
    assert v["kind"] == "word" and "<h1>" in v["html"] and "Four conditions" in v["html"]


def test_pdf_opens_inline(client, monkeypatch):
    import fitz
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Round robin scheduling uses a time quantum for every process.")
    doc_id = _uploaded(client, monkeypatch, "cpu.pdf", pdf.tobytes())
    assert client.get(f"/api/documents/{doc_id}/view").json()["kind"] == "pdf"
    r = client.get(f"/api/documents/{doc_id}/file")
    assert r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"].startswith("inline")


def test_open_externally_uses_stored_file(client, monkeypatch):
    import os as _os
    opened = []
    monkeypatch.setattr(_os, "startfile", lambda p: opened.append(str(p)))
    doc_id = _uploaded(client, monkeypatch, "plain.txt", b"text")
    assert client.post(f"/api/documents/{doc_id}/open").status_code == 200
    assert opened == [db.one("SELECT path FROM documents WHERE id=?", doc_id)["path"]]


def test_missing_original_is_explained(client):
    _, did = _ready_doc(client, "Gone", ["text"])
    r = client.get(f"/api/documents/{did}/view")
    assert r.status_code == 404 and "missing" in r.json()["detail"]


@pytest.mark.parametrize("link,expected", [
    ("https://open.spotify.com/playlist/37i9dQZF1DX8Uebhn9wzrS?si=abc", ("playlist", "37i9dQZF1DX8Uebhn9wzrS")),
    ("https://open.spotify.com/intl-en/track/4uLU6hMCjMI75M1A2tKUQC", ("track", "4uLU6hMCjMI75M1A2tKUQC")),
    ("spotify:album:1DFixLWuPkv3KT3TnV35m3", ("album", "1DFixLWuPkv3KT3TnV35m3")),
    ("https://example.com/playlist/37i9dQZF1DX8Uebhn9wzrS", None),
    ("", None),
])
def test_spotify_links(link, expected):
    from app import music
    assert music.parse_link(link) == expected


def test_music_add_open_and_duplicates(client, monkeypatch):
    import os as _os
    from app import music
    monkeypatch.setattr(music, "lookup", lambda k, i: {"title": "Focus", "thumbnail": None})
    opened = []
    monkeypatch.setattr(_os, "startfile", lambda p: opened.append(p))
    link = "https://open.spotify.com/playlist/37i9dQZF1DX8Uebhn9wzrS"
    assert client.post("/api/music", json={"link": "not a link"}).status_code == 400
    client.post("/api/music", json={"link": link})
    client.post("/api/music", json={"link": link})
    items = client.get("/api/music").json()
    assert len(items) == 1
    client.post(f"/api/music/{items[0]['id']}/open")
    assert opened == ["spotify:playlist:37i9dQZF1DX8Uebhn9wzrS"]


def _action(**k):
    return {"name": "make_notes", "subject": "", "documents": [], "style": "detailed", "count": 0, "value": 0,
            "page": "", "date": "", **k}


@pytest.mark.parametrize("text,name,value", [
    ("pause the music", "music_pause", 0), ("pause", "music_pause", 0), ("stop the song", "music_pause", 0),
    ("play the music", "music_play", 0), ("resume", "music_play", 0),
    ("skip this song", "music_next", 0), ("next track", "music_next", 0), ("skip", "music_next", 0),
    ("go back a song", "music_previous", 0), ("previous song", "music_previous", 0),
    ("turn the music down a bit", "volume_down", 0), ("make it quieter", "volume_down", 0),
    ("turn the volume up", "volume_up", 0), ("louder", "volume_up", 0),
    ("set the volume to 40", "set_volume", 40), ("volume 25%", "set_volume", 25),
    ("make notes for module 3", None, None), ("what is a deadlock", None, None),
    ("play the next module notes", None, None), ("open the last module", None, None),
    ("It's low Send the volume of the salt to 20.", "set_volume", 20), ("turn the volume to 60 please", "set_volume", 60),
    ("change volume 15", "set_volume", 15),
    ("mute the song", "music_mute", 0), ("Mute the soul", "music_mute", 0), ("mute", "music_mute", 0),
    ("unmute the music", "music_unmute", 0), ("mute the mic", None, None),
    ("increase the volume by a bit", "volume_up", 0), ("lower the music", "volume_down", 0),
])
def test_quick_music_commands(text, name, value):
    from app import actions
    got = actions.quick(text)
    assert (got[0]["name"], got[0]["value"]) == (name, value) if name else got is None


def test_questions_skip_the_action_planner(monkeypatch):
    from app import actions
    monkeypatch.setattr(actions, "plan", lambda t: (_ for _ in ()).throw(AssertionError("planner called")))
    assert actions.try_handle("what is a deadlock?") is None


def test_make_notes_for_a_whole_subject_skips_existing(client, monkeypatch):
    from app import actions
    cid, d1 = _ready_doc(client, "Actions OS", ["paging"])
    d2 = db.run("INSERT INTO documents(course_id,title,filename,path,status) VALUES(?,?,?,?,'ready')", cid, "Module 2", "m2", "x")
    db.run("UPDATE documents SET title='Module 1' WHERE id=?", d1)
    db.run("INSERT INTO notes(course_id,document_id,title,style,status) VALUES(?,?,?,?,'ready')", cid, d2, "Notes: Module 2", "detailed")
    started = []
    monkeypatch.setattr(study, "start_notes", lambda doc, style: started.append((doc, style)))
    monkeypatch.setattr(actions, "plan", lambda t: [_action(subject="actions os")])
    reply = actions.try_handle("make notes for every Actions OS module")
    assert started == [(d1, "detailed")]
    assert "Module 1" in reply and "Module 2 already has full notes" in reply


def test_named_documents_and_unknown_subject(client, monkeypatch):
    from app import actions
    cid, d1 = _ready_doc(client, "Pick OS", ["x"])
    db.run("UPDATE documents SET title='Module 3' WHERE id=?", d1)
    started = []
    monkeypatch.setattr(study, "start_cards", lambda doc, n: started.append((doc, n)))
    monkeypatch.setattr(actions, "plan", lambda t: [_action(name="make_flashcards", subject="Pick OS", documents=["module 3"])])
    assert "20 flashcards" in actions.try_handle("make flashcards for module 3 of pick os")
    assert started == [(d1, 20)]
    monkeypatch.setattr(actions, "plan", lambda t: [_action(subject="Biology")])
    assert "couldn't find a subject called Biology" in actions.try_handle("make notes for biology")


def test_volume_and_music_actions(monkeypatch):
    from app import actions, music
    level = {"v": 50}
    monkeypatch.setattr(music, "get_volume", lambda: level["v"])
    monkeypatch.setattr(music, "set_volume", lambda v: level.update(v=max(0, min(100, v))) or level["v"])
    monkeypatch.setattr(actions, "plan", lambda t: [_action(name="volume_down")])
    assert actions.try_handle("turn the music down") == "Music volume is now 35%."
    monkeypatch.setattr(actions, "plan", lambda t: [_action(name="set_volume", value=80)])
    assert actions.try_handle("set volume to 80") == "Music volume is now 80%."
    monkeypatch.setattr(actions, "plan", lambda t: [_action(name="music_mute")])
    assert actions.try_handle("mute the song").startswith("Music muted") and level["v"] == 0
    monkeypatch.setattr(actions, "plan", lambda t: [_action(name="music_unmute")])
    assert actions.try_handle("unmute the music") == "Music unmuted, volume 80%."
    monkeypatch.setattr(actions, "plan", lambda t: [_action(name="music_mute")])
    actions.try_handle("mute the song")
    monkeypatch.setattr(actions, "plan", lambda t: [_action(name="volume_up", value=5)])
    assert actions.try_handle("turn it up a bit") == "Music volume is now 80%."  # back where it was, not 5%
    monkeypatch.setattr(music, "control", lambda a: False)
    monkeypatch.setattr(actions, "plan", lambda t: [_action(name="music_pause")])
    assert "Open Spotify" in actions.try_handle("pause the music")


def test_instruction_reply_goes_through_chat(client, monkeypatch):
    from app import actions, assistant
    monkeypatch.setattr(actions, "try_handle", lambda t: "Paused.")
    spoken = []
    result = assistant.ask("pause the music", "voice", spoken.append)
    assert result["content"] == "Paused." and spoken == ["Paused."]
    assert db.one("SELECT content FROM messages WHERE id=?", result["id"])["content"] == "Paused."


def test_volume_endpoint(client, monkeypatch):
    from app import music
    set_to = []
    monkeypatch.setattr(music, "set_volume", lambda v: (set_to.append(v), v)[1])
    assert client.put("/api/music/volume", json={"value": 35}).json() == {"volume": 35}
    assert client.put("/api/music/volume", json={"value": 150}).status_code == 422
    monkeypatch.setattr(music, "set_volume", lambda v: None)
    assert client.put("/api/music/volume", json={"value": 20}).status_code == 400
    assert set_to == [35]


def test_song_position_counts_forward_while_playing():
    from datetime import datetime, timedelta, timezone
    from app import music
    t = datetime(2026, 11, 10, tzinfo=timezone.utc)
    assert music.position_now(75, 289, t, True, t + timedelta(seconds=5)) == 80
    assert music.position_now(75, 289, t, False, t + timedelta(seconds=5)) == 75
    assert music.position_now(285, 289, t, True, t + timedelta(seconds=30)) == 289


@pytest.mark.parametrize("stem,expected", [
    ("FALLSEM2026-27_VL_BCSE303L_00100_TH_2026-09-17_Module-4 (1)", "Module 4"),
    ("FALLSEM2025-26_VL_BCSE303L_00100_TH_2025-07-29_Module-I", "Module 1"),
    ("FALLSEM2025-26_VL_BCSE303L_00100_TH_2025-08-11_Module-III", "Module 3"),
    ("MODULE 6", "Module 6"),
    ("Module 5 ppt (2)", "Module 5"),
    ("unit_2_notes", "Unit 2"),
    ("Paging basics (1)", "Paging basics"),
    ("my_scanned_notes", "my scanned notes"),
])
def test_clean_titles(stem, expected):
    assert ingest.clean_title(stem) == expected


def test_rename_updates_notes_named_after_it(client):
    _, did = _ready_doc(client, "FALLSEM_Module-2", ["t"])
    db.run("INSERT INTO notes(course_id,document_id,title,style) VALUES((SELECT course_id FROM documents WHERE id=?),?,?,'summary')",
           did, did, "Summary: FALLSEM_Module-2")
    assert client.patch(f"/api/documents/{did}", json={"title": "Module 2"}).status_code == 200
    assert db.one("SELECT title FROM documents WHERE id=?", did)["title"] == "Module 2"
    assert db.one("SELECT title FROM notes WHERE document_id=?", did)["title"] == "Summary: Module 2"


def test_ticked_task_moves_to_done_today_and_back(client):
    cid = client.post("/api/courses", json={"name": "Today test"}).json()["id"]
    today = date.today()
    eid = db.run("INSERT INTO exams(course_id,name,date,minutes_per_day,status) VALUES(?,?,?,?,'ready')",
                 cid, "Final", (today + timedelta(days=5)).isoformat(), 60)
    tid = db.run("INSERT INTO tasks(exam_id,date,topic,kind,minutes) VALUES(?,?,?,?,?)",
                 eid, today.isoformat(), "Module 1", "learn", 45)
    ids = lambda key: [t["id"] for t in client.get("/api/today").json()[key]]
    assert tid in ids("tasks") and tid not in ids("completed")
    client.patch(f"/api/tasks/{tid}", json={"done": True})
    assert tid not in ids("tasks") and tid in ids("completed")
    client.patch(f"/api/tasks/{tid}", json={"done": False})
    assert tid in ids("tasks") and tid not in ids("completed")


def test_second_launch_asks_for_the_window(client):
    seen = []
    events.listen(seen.append)
    assert client.post("/api/window/show").status_code == 200
    assert {"type": "window.show"} in seen


def test_gpu_modes_place_work_correctly():
    from app import config
    config.save_settings({"gpu_mode": "balanced"})
    assert llm.placement("reader")["num_gpu"] == 0 and llm.placement("search")["num_gpu"] == 0
    assert "num_gpu" not in llm.placement("thinking")
    config.save_settings({"gpu_mode": "light"})
    assert llm.placement("reader")["num_gpu"] == 0 and llm.placement("thinking")["num_gpu"] > 0
    config.save_settings({"gpu_mode": "full"})
    assert llm.placement("reader") == {} and llm.placement("thinking") == {}
    config.save_settings({"gpu_mode": "balanced"})


def test_reading_pauses_for_a_question():
    import threading
    released = threading.Event()

    def reader():
        llm.yield_to_questions()
        released.set()

    with llm.question():
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        assert not released.wait(0.2)
    assert released.wait(1)


def test_scans_use_the_reader_model_not_the_thinking_model(monkeypatch):
    from app import config
    used = {}

    def fake(messages, system, schema, model=None, keep_alive="30m", read_timeout=600, max_tokens=None, role="thinking"):
        used.update(model=model, prompt=messages[0]["content"], system=system)
        yield "page text"

    config.save_settings({"provider": "ollama", "ocr_provider": "ollama", "ollama_ocr_model": "glm-ocr:q8_0"})
    monkeypatch.setattr(llm, "_ollama_stream", fake)
    monkeypatch.setattr(llm, "ollama_status", lambda: {"running": True, "models": ["glm-ocr:q8_0"]})
    assert llm.read_image("p", "s", "QUJD") == "page text"
    assert used == {"model": "glm-ocr:q8_0", "prompt": "Text Recognition:", "system": None}

    monkeypatch.setattr(llm, "ollama_status", lambda: {"running": True, "models": []})
    llm.read_image("p", "s", "QUJD")
    assert used["model"] == config.load_settings()["ollama_model"] and used["system"] == "s"


def test_interrupted_pdf_resumes_without_rereading_pages(client, monkeypatch):
    import fitz
    pdf = fitz.open()
    for _ in range(2):
        pdf.new_page().insert_image(fitz.Rect(0, 0, 200, 200), stream=_png())
    reads = []
    monkeypatch.setattr(llm, "embed", lambda _t: None)
    monkeypatch.setattr(llm, "read_image", lambda *a: (reads.append(1), "read by ocr text")[1])
    cid = client.post("/api/courses", json={"name": "Resume"}).json()["id"]
    path = Path(os.environ["MARGIN_DATA"]) / "resume.pdf"
    pdf.save(path)
    did = db.run("INSERT INTO documents(course_id,title,filename,path) VALUES(?,?,?,?)", cid, "R", "r.pdf", str(path))
    db.run("INSERT INTO pages VALUES(?,?,?)", did, 1, "already read")
    ingest.resume_unfinished()
    _wait(lambda: db.one("SELECT status FROM documents WHERE id=?", did)["status"] == "ready")
    assert len(reads) == 1
    assert db.one("SELECT text FROM pages WHERE document_id=? AND page=1", did)["text"] == "already read"


def _png():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(b, "PNG")
    return b.getvalue()


def _upload_unsorted(client, name, text):
    r = client.post("/api/documents", files=[("files", (name, text.encode(), "text/plain"))])
    doc_id = r.json()["ids"][0]
    _wait(lambda: db.one("SELECT status FROM documents WHERE id=?", doc_id)["status"] != "processing")
    return db.one("SELECT d.*, c.name AS course FROM documents d JOIN courses c ON c.id=d.course_id WHERE d.id=?", doc_id)


def _answer(monkeypatch, subject):
    monkeypatch.setattr(llm, "embed", lambda _t: None)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"subject": subject})


def test_auto_sort_creates_subject_then_reuses_it(client, monkeypatch):
    _answer(monkeypatch, "Thermodynamics")
    assert _upload_unsorted(client, "a.txt", "entropy")["course"] == "Thermodynamics"
    _answer(monkeypatch, " thermodynamics notes. ")
    assert _upload_unsorted(client, "b.txt", "enthalpy")["course"] == "Thermodynamics"
    assert len(db.q("SELECT id FROM courses WHERE name LIKE 'thermo%'")) == 1


def test_auto_sort_keeps_doc_when_unsure_or_failing(client, monkeypatch):
    from app import sorting
    _answer(monkeypatch, "Unknown")
    assert _upload_unsorted(client, "c.txt", "mystery")["course"] == sorting.UNSORTED

    def broken(*a, **k):
        raise llm.LLMError("Ollama isn't running.")
    monkeypatch.setattr(llm, "complete_json", broken)
    doc = _upload_unsorted(client, "d.txt", "still readable")
    assert doc["course"] == sorting.UNSORTED and doc["status"] == "ready"


def test_empty_unsorted_is_hidden(client):
    from app import sorting
    uid = sorting.unsorted_id()
    db.run("DELETE FROM documents WHERE course_id=?", uid)
    assert sorting.UNSORTED not in [c["name"] for c in client.get("/api/courses").json()]


def test_moving_document_brings_its_study_material(client):
    cid, did = _ready_doc(client, "From", ["text"])
    target = client.post("/api/courses", json={"name": "To"}).json()["id"]
    db.run("INSERT INTO cards(course_id,document_id,front,back) VALUES(?,?,'f','b')", cid, did)
    db.run("INSERT INTO notes(course_id,document_id,title,style) VALUES(?,?,'n','summary')", cid, did)
    assert client.patch(f"/api/documents/{did}", json={"course_id": target}).status_code == 200
    assert db.one("SELECT course_id FROM cards WHERE document_id=?", did)["course_id"] == target
    assert db.one("SELECT course_id FROM notes WHERE document_id=?", did)["course_id"] == target


def _wait(cond, tries=100):
    import time
    for _ in range(tries):
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_foreign_origin_is_refused(client):
    r = client.post("/api/courses", json={"name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_foreign_host_is_refused(client):
    assert client.get("/api/courses", headers={"host": "evil.example"}).status_code == 400


def test_claude_needs_key(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "claude_configured", lambda: False)
    assert client.put("/api/settings", json={"provider": "claude"}).status_code == 400
