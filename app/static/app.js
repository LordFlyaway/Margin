import { api, connect, esc, md } from "./common.js";

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const S = {
  state: null, courses: [], route: "today",
  library: { course: null },
  papers: { course: null, open: null, answers: false },
  notes: { open: null, editing: false },
  cards: { queue: null, i: 0, flipped: false },
  quiz: { open: null, answers: {} },
};

function toast(message, tone = "info") {
  const el = document.createElement("div");
  el.className = `toast ${tone}`;
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), tone === "error" ? 8000 : 4000);
}
const guard = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, "error"); } };

const when = (iso) => new Date(iso + "T00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
const todayISO = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const KIND = { learn: "Learn", review: "Review", mock: "Mock exam" };

// ---- Views --------------------------------------------------------------

const VIEWS = {
  async today() {
    const t = await api("/api/today");
    const heading = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
    let lead = "Upload your notes in the Library to get started.";
    if (t.next_exam) lead = `<b>${plural(t.next_exam.days_left, "day")}</b> until ${esc(t.next_exam.name)}.`;
    else if (t.documents) lead = "No exams yet. Add one in Exam plan to get a revision schedule.";
    const overdue = (x) => x.date < todayISO() ? ` · from ${when(x.date)}` : "";
    const task = (x, done) => `<li class="${done ? "done" : ""}"><label class="task-row">
          <input type="checkbox" data-act="tick" data-id="${x.id}" ${done ? "checked" : ""} aria-label="${done ? "Mark not done" : "Mark done"}: ${esc(x.topic)}">
          <div class="grow"><div class="title">${esc(x.topic)}</div>
          <div class="meta">${KIND[x.kind]} · ${x.minutes} min · ${esc(x.exam)}${done ? "" : overdue(x)}</div></div></label></li>`;
    const total = t.tasks.length + t.completed.length;
    const minutesDone = t.completed.reduce((s, x) => s + x.minutes, 0);
    return `
      <div class="page-head"><div><h1>${esc(heading)}</h1><p class="lead">${lead}</p></div></div>
      <div class="section"><h2>Study today${total ? ` <span class="meta">${t.completed.length} of ${total} done</span>` : ""}</h2>
      ${t.tasks.length ? `<ul class="list">${t.tasks.map((x) => task(x, false)).join("")}</ul>`
        : total ? `<p class="hand" style="font-size:1.3rem">all done for today</p>` : `<p class="empty">Nothing scheduled today.</p>`}
      </div>
      ${t.completed.length ? `<div class="section"><h2>Done today <span class="meta">${minutesDone} min studied</span></h2>
        <ul class="list">${t.completed.map((x) => task(x, true)).join("")}</ul></div>` : ""}
      <div class="section"><h2>Flashcards</h2>
        <p>${t.cards.due ? `${plural(t.cards.due, "card")} due. <a href="#cards">Review now</a>` : `<span class="muted">No cards due.</span>`}</p>
      </div>
      ${t.weak.length ? `<div class="section"><h2>Your weak topics</h2><ul class="list">${t.weak.map((w) => `<li><div class="grow title">${esc(w.topic)}</div>
          <span class="meta">${Math.round(w.score * 100)}% across ${plural(w.attempts, "question")}</span>
          <button class="ghost small" data-act="ask-about" data-topic="${esc(w.topic)}">Explain this</button></li>`).join("")}</ul></div>` : ""}`;
  },

  async library() {
    S.courses = await api("/api/courses");
    const picked = S.courses.find((c) => c.id === S.library.course);
    if (!picked) S.library.course = null;
    const add = `<form class="row" data-form="course"><input name="name" placeholder="Subject name, e.g. Operating Systems" required aria-label="Subject name" style="min-width:280px"><button class="ghost">Add subject</button></form>`;
    const docs = (await api(picked ? `/api/documents?course_id=${picked.id}` : "/api/documents"))
      .sort((a, b) => a.course.localeCompare(b.course) || a.title.localeCompare(b.title, undefined, { numeric: true }));
    const dropText = picked ? `Drop files here to add them to ${esc(picked.name)}, or` : "Drop your files here and Margin will sort them into subjects, or";
    const none = picked ? "No documents in this subject yet." : "Nothing here yet. Drop in your notes above.";
    return `
      <div class="page-head"><div><h1>Library</h1><p class="sub">PDFs, slides, Word files, text and photos of handwritten notes.</p></div>${add}</div>
      <div class="tabs"><button data-act="pick-course" data-id="" aria-pressed="${!picked}">All subjects</button>${S.courses.map((c) => `<button data-act="pick-course" data-id="${c.id}" aria-pressed="${c.id === S.library.course}">${esc(c.name)}</button>`).join("")}</div>
      <div class="drop" id="drop" data-course="${picked ? picked.id : ""}">${dropText} <label>choose files<input id="file" type="file" multiple hidden accept=".pdf,.pptx,.docx,.txt,.md,.png,.jpg,.jpeg,.webp"></label></div>
      ${docs.length ? `<ul class="list">${docs.map(docRow).join("")}</ul>` : `<p class="empty">${none}</p>`}
      ${picked ? `<p style="margin-top:30px"><button class="ghost small danger" data-act="del-course" data-id="${picked.id}">Delete this subject</button></p>` : ""}`;
  },

  async papers() {
    const P = S.papers;
    if (P.open) return paperSheet(await api(`/api/papers/${P.open}`));
    const courses = (await api("/api/courses")).filter((c) => c.name !== "Unsorted");
    if (!courses.length) return `<div class="page-head"><div><h1>Papers</h1></div></div>
      <div class="empty"><span class="hand">add a subject first</span>Papers are kept per subject. Add one in the <a href="#library">Library</a>.</div>`;
    if (!courses.some((c) => c.id === P.course)) P.course = courses[0].id;
    const course = courses.find((c) => c.id === P.course);
    const [{ past, made }, docs] = await Promise.all([api(`/api/papers?course_id=${P.course}`), api(`/api/documents?course_id=${P.course}`)]);
    const ready = docs.filter((d) => d.status === "ready").sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true }));
    const readyPast = past.filter((p) => p.status === "ready").length;
    const status = (x) => x.status === "failed" ? `<span class="fail">${esc(x.detail || "Didn't finish.")}</span>`
      : x.status === "ready" ? "" : esc(x.detail || "Working…");
    return `<div class="page-head"><div><h1>Papers</h1><p class="sub">Add past papers, then make new ones in the same format from your study material.</p></div></div>
      <div class="tabs">${courses.map((c) => `<button data-act="papers-course" data-id="${c.id}" aria-pressed="${c.id === P.course}">${esc(c.name)}</button>`).join("")}</div>

      <section class="section"><h2>Make a new paper</h2>
        ${ready.length ? `<form data-form="paper" class="paper-form">
          <fieldset><legend>Cover these modules</legend><div class="checks">${ready.map((d) =>
            `<label class="toggle"><input type="checkbox" name="doc" value="${d.id}" checked> ${esc(d.title)}</label>`).join("")}</div></fieldset>
          <p class="hint">${readyPast ? `Follows the format of your ${readyPast} past paper${readyPast === 1 ? "" : "s"}: same sections, marks and style, with new questions.`
            : "No past papers yet, so it uses a standard format: Part A, 10 short questions of 2 marks; Part B, answer any 5 of 7 questions of 10 marks."}</p>
          <button class="primary">Make paper</button></form>`
        : `<p class="empty">Upload ${esc(course.name)} study material in the <a href="#library">Library</a> first.</p>`}
      </section>

      <section class="section"><h2>Your practice papers</h2>
        ${made.length ? `<ul class="list">${made.map((p) => `<li><div class="grow">
          ${p.status === "ready" ? `<button class="link-title" data-act="paper-open" data-id="${p.id}">${esc(p.title)}</button>` : `<span class="title">${esc(p.title)}</span>`}
          <div class="meta">${p.status === "ready" ? `${esc(p.exam_name || "")} · ${p.total_marks} marks` : status(p)}</div></div>
          <button class="ghost small danger" data-act="paper-del" data-id="${p.id}" aria-label="Delete ${esc(p.title)}">Delete</button></li>`).join("")}</ul>`
        : `<p class="empty">None yet.</p>`}
      </section>

      <section class="section"><h2>Past papers</h2>
        <div class="drop" id="drop" data-course="${P.course}" data-kind="paper">Drop ${esc(course.name)} past papers here (PDFs, scans or photos), or <label>choose files<input id="file" type="file" multiple hidden accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp"></label></div>
        ${past.length ? `<ul class="list">${past.map((p) => `<li><div class="grow"><a class="link-title" href="#reader/${p.id}">${esc(p.title)}</a>
          <div class="meta">${p.status === "ready" ? `${p.page_count} page${p.page_count === 1 ? "" : "s"}` : status(p)}</div></div>
          <button class="ghost small danger" data-act="del-doc" data-id="${p.id}" aria-label="Delete ${esc(p.title)}">Delete</button></li>`).join("")}</ul>` : ""}
      </section>`;
  },

  async music() {
    const items = await api("/api/music");
    return `<div class="page-head"><div><h1>Music</h1><p class="sub">Save your study playlists from Spotify. Whatever Spotify is playing shows in the player on the left.</p></div></div>
      <form class="row section" data-form="music"><input name="link" required placeholder="Paste a Spotify link to a song, album or playlist" aria-label="Spotify link" style="flex:1;min-width:280px"><button class="primary">Add</button></form>
      ${items.length ? `<ul class="list section">${items.map((m) => `<li class="doc-row">
        ${m.thumbnail ? `<img class="cover" src="${esc(m.thumbnail)}" alt="">` : `<span class="cover"></span>`}
        <div class="grow"><div class="title">${esc(m.title)}</div><div class="meta">${esc(m.kind)}</div></div>
        <div class="row"><button class="primary small" data-act="music-open" data-id="${m.id}">Open in Spotify</button>
        <button class="ghost small" data-act="music-here" data-kind="${esc(m.kind)}" data-sid="${esc(m.spotify_id)}">Play here</button>
        <button class="ghost small danger" data-act="music-del" data-id="${m.id}" aria-label="Remove ${esc(m.title)}">Remove</button></div></li>`).join("")}</ul>
        <p class="hint section">Open in Spotify plays full tracks in the app. Play here keeps a small player beside your notes, but it may only play 30-second previews.</p>`
      : `<div class="empty"><span class="hand">nothing saved yet</span>In Spotify, choose Share, then Copy link, and paste it above.</div>`}`;
  },

  async reader() {
    const { id, page } = S.reader;
    const v = await api(`/api/documents/${id}/view`);
    const file = `/api/documents/${id}/file`;
    const label = { word: "Open in Word", slides: "Open in PowerPoint", pdf: "Open in PDF app" }[v.kind];
    const head = `<div class="page-head"><div><a class="ghost small back" href="#library">Library</a><h1 style="margin-top:12px">${esc(v.title)}</h1></div>
      ${v.external ? `<button class="ghost" data-act="open-external" data-id="${id}">${label}</button>` : ""}</div>`;
    const body = {
      pdf: () => `<iframe class="reader-frame" title="${esc(v.title)}" src="${file}${page ? `#page=${page}` : ""}"></iframe>`,
      image: () => `<img class="reader-img" src="${file}" alt="${esc(v.title)}">`,
      word: () => `<article class="prose doc-html">${DOMPurify.sanitize(v.html)}</article>`,
      markdown: () => `<article class="prose doc-html">${md(v.text)}</article>`,
      text: () => `<pre class="doc-text">${esc(v.text)}</pre>`,
      slides: () => v.pages.map((s) => `<section class="slide" id="slide-${s.page}"><div class="q-n">slide ${s.page}</div><pre class="doc-text">${esc(s.text)}</pre></section>`).join(""),
    }[v.kind]();
    return head + body;
  },

  async notes() {
    if (S.notes.open) {
      if (S.notes.editing && $(".editor")) return null;
      const n = await api(`/api/notes/${S.notes.open}`);
      S.notes.current = n;
      if (S.notes.editing) return `<div class="page-head"><h1>Edit notes</h1><div class="row"><button class="ghost" data-act="note-cancel">Cancel</button><button class="primary" data-act="note-save">Save</button></div></div>
        <textarea class="editor" id="note-body" aria-label="Notes in Markdown">${esc(n.body)}</textarea>`;
      return `<div class="page-head"><div><button class="ghost small" data-act="note-back">All notes</button><h1 style="margin-top:12px">${esc(n.title)}</h1></div>
        <div class="row"><button class="ghost" data-act="note-edit" ${n.status === "writing" ? "disabled" : ""}>Edit</button><button class="ghost danger" data-act="note-del">Delete</button></div></div>
        ${n.status === "failed" ? `<p class="fail">These notes didn't finish. Delete them and generate again.</p>` : ""}
        <article class="prose" id="note-article" data-doc="${n.document_id ?? ""}">${md(n.body)}</article>
        ${n.status === "writing" ? `<p class="typing">writing…</p>` : ""}`;
    }
    const rows = await api("/api/notes");
    if (!rows.length) return `<div class="page-head"><div><h1>Notes</h1></div></div>
      <div class="empty"><span class="hand">nothing written yet</span>Open the <a href="#library">Library</a> and choose Make notes on a document.</div>`;
    const groups = new Map();
    [...rows].sort((a, b) => a.course.localeCompare(b.course)).forEach((n) => {
      if (!groups.has(n.course_id)) groups.set(n.course_id, { name: n.course, notes: [] });
      groups.get(n.course_id).notes.push(n);
    });
    if (!groups.has(S.notes.course)) S.notes.course = null;
    const shown = S.notes.course ? [[S.notes.course, groups.get(S.notes.course)]] : [...groups];
    const row = (n) => `<li><div class="grow"><button class="link-title" data-act="note-open" data-id="${n.id}">${esc(n.title)}</button>
        <div class="meta">${{ detailed: "Full notes", summary: "Summary", cheatsheet: "Cheat sheet" }[n.style] || ""}${n.status === "writing" ? " · writing…" : ""}${n.status === "failed" ? " · didn't finish" : ""}</div></div></li>`;
    return `<div class="page-head"><div><h1>Notes</h1></div></div>
      <div class="tabs"><button data-act="notes-course" data-id="" aria-pressed="${!S.notes.course}">All subjects</button>${[...groups].map(([id, g]) =>
        `<button data-act="notes-course" data-id="${id}" aria-pressed="${id === S.notes.course}">${esc(g.name)} <span class="meta">${g.notes.length}</span></button>`).join("")}</div>
      ${shown.map(([, g]) => `<section class="subject"><h2>${esc(g.name)}</h2><ul class="list">${g.notes.map(row).join("")}</ul></section>`).join("")}`;
  },

  async cards() {
    const stats = await api("/api/cards/stats");
    const c = S.cards;
    if (c.queue && c.i < c.queue.length) {
      const card = c.queue[c.i];
      return `<div class="page-head"><h1>Flashcards</h1><div class="muted">${c.i + 1} of ${c.queue.length}</div></div>
      <div class="deck">
        <div class="card-stage ${c.flipped ? "flipped" : ""}" data-act="flip" role="button" tabindex="0" aria-label="${c.flipped ? "Show the question" : "Show the answer"}">
          <div class="index-card">
            <div class="face"><span class="tag tag-side">front</span><h3 class="card-head">Question ${c.i + 1}</h3><div class="card-body prose">${md(card.front)}</div></div>
            <div class="face back"><span class="tag tag-side">back</span><h3 class="card-head">Answer</h3><div class="card-body prose">${md(card.back)}</div></div>
          </div>
          <button type="button" class="tag tag-flip" data-act="flip" tabindex="-1" aria-hidden="true">flip</button>
        </div>
        ${c.flipped ? `<div class="grades">${[["Again", 0], ["Hard", 1], ["Good", 2], ["Easy", 3]].map(([l, g]) =>
          `<button class="ghost" data-act="grade" data-g="${g}">${l}<span class="key">press ${g + 1}</span></button>`).join("")}</div>`
        : `<p class="muted">Click the card or press space to flip it</p>`}
      </div>`;
    }
    S.cards.queue = null;
    return `<div class="page-head"><h1>Flashcards</h1></div>
      <div class="stats"><div><b>${stats.due}</b>due today</div><div><b>${stats.total}</b>total</div><div><b>${stats.mastered}</b>mastered</div></div>
      <p style="margin-top:26px">${stats.due ? `<button class="primary" data-act="start-cards">Review ${plural(stats.due, "card")}</button>`
        : stats.total ? `<span class="hand" style="font-size:1.3rem">all caught up</span>` : `Open the <a href="#library">Library</a> and choose Flashcards on a document.`}</p>`;
  },

  async practice() {
    if (S.quiz.open) {
      if (!$(".q") || S.quiz.status !== "ready") {
        const z = await api(`/api/quizzes/${S.quiz.open}`);
        S.quiz.status = z.status;
        return z.status === "graded" ? quizResult(z) : z.status === "grading" ? `<p class="typing">marking your answers…</p>` : quizForm(z);
      }
      return null;
    }
    const [rows, weak] = await Promise.all([api("/api/quizzes"), api("/api/weak-topics")]);
    return `<div class="page-head"><div><h1>Practice exams</h1></div></div>
      ${rows.length ? `<ul class="list">${rows.map((z) => `<li><div class="grow">
        ${["ready", "graded", "grading"].includes(z.status) ? `<button class="link-title" data-act="quiz-open" data-id="${z.id}">${esc(z.title)}</button>` : `<span class="title">${esc(z.title)}</span>`}
        <div class="meta">${esc(z.course)} · ${z.status === "writing" ? "writing questions…" : z.status === "failed" ? "didn't finish" : plural(z.questions, "question")}</div></div>
        ${z.score != null ? `<span class="hand wrong" style="font-size:1.3rem">${Math.round(z.score * 100)}%</span>` : ""}
        <button class="ghost small danger" data-act="quiz-del" data-id="${z.id}">Delete</button></li>`).join("")}</ul>`
      : `<div class="empty"><span class="hand">no exams written yet</span>Open the <a href="#library">Library</a> and choose Practice exam on a document.</div>`}
      ${weak.length ? `<div class="section"><h2>Weak topics</h2><ul class="list">${weak.map((w) => `<li><div class="grow title">${esc(w.topic)}</div><span class="meta">${Math.round(w.score * 100)}%</span></li>`).join("")}</ul></div>` : ""}`;
  },

  async plan() {
    const [exams, courses] = await Promise.all([api("/api/exams"), api("/api/courses")]);
    S.courses = courses;
    const form = courses.length ? `<form class="form-grid" data-form="exam">
      <label class="field">Exam name<input name="name" required placeholder="Midterm 2"></label>
      <label class="field">Course<select name="course_id">${courses.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></label>
      <label class="field">Exam date<input type="date" name="date" min="${todayISO()}" required></label>
      <label class="field">Minutes a day<input type="number" name="minutes_per_day" min="15" max="720" step="15" value="120" required></label>
      <label class="field wide">Syllabus topics <span class="hint">One per line. Leave empty and the AI will work them out from your notes.</span><textarea name="topics" rows="3"></textarea></label>
      <div><button class="primary">Build my plan</button></div></form>` : `<p class="empty">Add a course in the <a href="#library">Library</a> first.</p>`;
    const list = exams.map(examBlock).join("");
    return `<div class="page-head"><div><h1>Exam plan</h1></div></div>${list}
      <div class="section"><h2>Add an exam</h2>${form}</div>`;
  },

  async chat() {
    // The assistant panel fills the page here (see route), so the page itself stays empty.
    return "";
  },

  async settings() {
    const s = await api("/api/state");
    S.state = s;
    const st = s.settings;
    const local = s.ollama.models.filter((m) => !/embed|ocr/.test(m));
    const readers = s.ollama.models.filter((m) => /ocr|vl|vision|llava/.test(m));
    const seg = (act, value, label, extra = []) => `<div class="seg" role="group" aria-label="${label}">
        <button data-act="${act}" data-v="claude" aria-pressed="${value === "claude"}">Claude</button>
        ${extra.map(([v, l]) => `<button data-act="${act}" data-v="${v}" aria-pressed="${value === v}">${l}</button>`).join("")}
        <button data-act="${act}" data-v="ollama" aria-pressed="${value === "ollama"}">Local</button></div>`;
    if (s.openai_configured && !S.openaiModels) {
      S.openaiModels = [];
      api("/api/openai/models").then((r) => { S.openaiModels = r.models; if (S.route === "settings" && r.models.length) render(); }).catch(() => {});
    }
    const gptModels = [...new Set([st.openai_model, ...(S.openaiModels?.length ? S.openaiModels : s.openai_models)])];
    return `<div class="page-head"><h1>Settings</h1></div>
    <div class="setting"><h2>Thinking</h2>
      ${seg("provider", st.provider, "Model for notes and answers", [["openai", "ChatGPT"]])}
      <p class="hint" style="margin:0">Writes your notes, flashcards and exams, and answers your doubts and chats. Claude and ChatGPT give the best results and can look at pictures you attach; you pay per use. Local is free and stays on this PC.</p>
    </div>
    <div class="setting"><h2>Reading scanned pages</h2>
      ${seg("ocr-provider", st.ocr_provider, "Model for reading scans")}
      <p class="hint" style="margin:0">Reads slides, scans and photos that have no selectable text. Claude is best for handwriting.</p>
      ${st.ocr_provider === "claude"
        ? `<label class="field">Claude model<select data-setting="claude_ocr_model">${s.claude_models.map((m) => `<option value="${m.id}" ${m.id === st.claude_ocr_model ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></label>`
        : s.ollama.running ? `<label class="field">Local model<select data-setting="ollama_ocr_model">${[...new Set([st.ollama_ocr_model, ...readers])].map((m) => `<option ${m === st.ollama_ocr_model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>
          ${s.ollama.models.includes(st.ollama_ocr_model) ? "" : `<span class="hint">${esc(st.ollama_ocr_model)} isn't downloaded, so your thinking model reads scans for now.</span>`}</label>` : ""}
    </div>
    <div class="setting"><h2>Graphics card</h2>
      <label class="field">How hard local models work it
        <select data-setting="gpu_mode">${[["balanced", "Balanced: only answers use the GPU, background reading uses the CPU"],
          ["light", "Light: answers share the work with the CPU too, slower but cooler"],
          ["full", "Full: everything on the GPU, fastest"]].map(([v, l]) => `<option value="${v}" ${v === st.gpu_mode ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    </div>
    <div class="setting"><h2>Claude</h2>
      <form class="row" data-form="key"><input name="key" type="password" autocomplete="off" required placeholder="${s.claude_configured ? "Key saved. Paste a new one to replace it" : "Paste your API key"}" aria-label="Claude API key" style="flex:1;min-width:240px"><button class="ghost">Save key</button>
      ${s.claude_configured ? `<button type="button" class="ghost danger" data-act="del-key">Remove</button>` : ""}</form>
      <span class="hint">Get a key at console.anthropic.com. It's stored in Windows Credential Manager.</span>
      <label class="field">Model<select data-setting="claude_model">${s.claude_models.map((m) => `<option value="${m.id}" ${m.id === st.claude_model ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></label>
    </div>
    <div class="setting"><h2>ChatGPT</h2>
      <form class="row" data-form="openai-key"><input name="key" type="password" autocomplete="off" required placeholder="${s.openai_configured ? "Key saved. Paste a new one to replace it" : "Paste your OpenAI API key"}" aria-label="OpenAI API key" style="flex:1;min-width:240px"><button class="ghost">Save key</button>
      ${s.openai_configured ? `<button type="button" class="ghost danger" data-act="del-openai-key">Remove</button>` : ""}</form>
      <span class="hint">Get a key at platform.openai.com and add some credit there. A ChatGPT Plus subscription doesn't include API use. The key is stored in Windows Credential Manager.</span>
      <label class="field">Model<select data-setting="openai_model">${gptModels.map((m) => `<option ${m === st.openai_model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>
        <span class="hint">${S.openaiModels?.length ? "These are the models your key can use." : "Save your key to see every model your account can use."} The mini models are much cheaper and fine for most doubts.</span></label>
    </div>
    <div class="setting"><h2>Local</h2>
      ${s.ollama.running ? `<label class="field">Model<select data-setting="ollama_model">${[...new Set([st.ollama_model, ...local])].map((m) => `<option ${m === st.ollama_model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>
        <span class="hint">Your 8 GB graphics card runs models up to about 7B well. Vision models can also read scans.</span></label>`
      : `<p class="fail" style="margin:0">Ollama isn't running. Open it to use local models.</p>`}
    </div>
    <div class="setting"><h2>Voice</h2>
      <label class="toggle"><input type="checkbox" data-setting="wake_enabled" ${st.wake_enabled ? "checked" : ""}> Listen for “hey Jarvis”</label>
      <label class="toggle"><input type="checkbox" data-setting="speak_answers" ${st.speak_answers ? "checked" : ""}> Speak answers out loud</label>
      <label class="field">Wake word sensitivity <span class="hint">Move right if it triggers by accident, left if it misses you.</span>
        <input type="range" min="0.2" max="0.9" step="0.05" value="${st.wake_threshold}" data-setting="wake_threshold"></label>
      <span class="hint">The microphone is analysed on this PC. Only your transcribed question goes to the AI you selected above.</span>
    </div>
    <div class="setting"><h2>Startup</h2>
      <label class="toggle"><input type="checkbox" data-setting="start_with_windows" ${s.start_with_windows ? "checked" : ""}> Start Margin in the tray when Windows starts</label>
    </div>`;
  },
};

// ---- Mini player (whatever Spotify is playing) ---------------------------

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const PLAY_ICON = "M8 5v14l11-7z", PAUSE_ICON = "M7 5h4v14H7zM13 5h4v14h-4z";
const mini = { now: null, at: 0, art: null, volumeAt: 0, volumeTimer: null };

$("#mini-volume").addEventListener("input", (e) => {
  const value = +e.target.value;
  e.target.style.setProperty("--fill", `${value}%`);
  mini.volumeAt = Date.now();
  clearTimeout(mini.volumeTimer);  // send once the slider settles, not on every pixel
  mini.volumeTimer = setTimeout(guard(() => api("/api/music/volume", { method: "PUT", json: { value } })), 120);
});

function drawMini() {
  const n = mini.now;
  if (!n) return;
  const pos = Math.min(n.duration || Infinity, n.position + (n.playing ? (Date.now() - mini.at) / 1000 : 0));
  const pct = n.duration ? (pos / n.duration) * 100 : 0;
  $("#mini-fill").style.width = `${pct}%`;
  $("#mini-bar").setAttribute("aria-valuenow", Math.round(pct));
  $("#mini-pos").textContent = clock(pos);
  $("#mini-len").textContent = clock(n.duration || 0);
}

async function refreshMini() {
  let n;
  try { n = await api("/api/music/now"); } catch { return; }
  $("#mini").hidden = !n.available || !n.title;
  if (!n.available) { mini.now = null; return; }
  mini.now = n; mini.at = Date.now();
  $("#mini-title").textContent = n.title;
  $("#mini-artist").textContent = n.artist;
  $("#mini-icon").setAttribute("d", n.playing ? PAUSE_ICON : PLAY_ICON);
  $("#mini-play").setAttribute("aria-label", n.playing ? "Pause" : "Play");
  const vol = $("#mini-volume");
  $("#mini-volume-row").hidden = n.volume == null;
  if (n.volume != null && document.activeElement !== vol && Date.now() - mini.volumeAt > 3000) {
    vol.value = n.volume;
    vol.style.setProperty("--fill", `${n.volume}%`);
  }
  if (n.art !== mini.art) {
    mini.art = n.art;
    const img = $("#mini-art");
    img.hidden = !n.art;
    if (n.art) { img.src = `/api/music/art?k=${n.art}`; img.alt = `${n.album || n.title} cover`; }
  }
  drawMini();
}

document.querySelectorAll("[data-mini]").forEach((b) => {
  b.onclick = guard(async () => {
    await api("/api/music/control", { method: "POST", json: { action: b.dataset.mini } });
    setTimeout(refreshMini, 400);
  });
});
setInterval(refreshMini, 3000);
setInterval(drawMini, 500);
refreshMini();

function paperSheet(p) {
  const b = p.body;
  if (p.status !== "ready") return `<p><button class="ghost small" data-act="paper-back">All papers</button></p><p class="${p.status === "failed" ? "fail" : "typing"}">${esc(p.detail || "writing…")}</p>`;
  const hours = b.duration_minutes >= 60 ? `${+(b.duration_minutes / 60).toFixed(1)} hour${b.duration_minutes === 60 ? "" : "s"}` : `${b.duration_minutes} minutes`;
  let n = 0;
  const sections = b.sections.map((s) => {
    const all = s.answer >= s.questions.length;
    const rule = all ? `Answer all · ${s.questions.length} × ${s.marks} = ${s.questions.length * s.marks} marks`
      : `Answer any ${s.answer} of ${s.questions.length} · ${s.answer} × ${s.marks} = ${s.answer * s.marks} marks`;
    return `<section class="exam-part"><h2>${esc(s.name)}</h2><p class="exam-rule">${rule}</p>
      ${s.instructions ? `<p class="exam-instr">${esc(s.instructions)}</p>` : ""}
      <ol class="exam-qs" start="${n + 1}">${s.questions.map((q) => { n++; return `<li>
        <div class="exam-q"><div class="prose">${md(q.text)}</div><span class="exam-marks">[${q.marks}]</span></div>
        ${q.options.length ? `<ol class="exam-opts" type="a">${q.options.map((o) => `<li>${md(o)}</li>`).join("")}</ol>` : ""}
        <div class="exam-answer"><div class="exam-answer-label">Marking scheme${q.module ? ` · ${esc(q.module)}` : ""}${q.topic ? ` · ${esc(q.topic)}` : ""}</div><div class="prose">${md(q.answer)}</div></div>
      </li>`; }).join("")}</ol></section>`;
  }).join("");
  return `<div class="paper-actions"><button class="ghost small" data-act="paper-back">All papers</button>
      <div class="row"><label class="toggle"><input type="checkbox" data-act="paper-answers" ${S.papers.answers ? "checked" : ""}> Show answers</label>
      <button class="ghost" data-act="paper-print">Print</button></div></div>
    <article class="exam-sheet ${S.papers.answers ? "show-answers" : ""}">
      <header class="exam-head"><div class="exam-subject">${esc(b.subject)}</div><h1>${esc(b.exam_name)}</h1>
        <div class="exam-meta"><span>Time: ${hours}</span><span>${esc(p.title)}</span><span>Maximum marks: ${b.total_marks}</span></div></header>
      ${b.instructions ? `<p class="exam-instr">${esc(b.instructions)}</p>` : ""}
      ${sections}
      <footer class="exam-foot">Covers ${esc(b.covers.join(", "))}${b.based_on.length ? ` · format from ${esc(b.based_on.join(", "))}` : " · standard format"}</footer>
    </article>`;
}

function docRow(d) {
  const ready = d.status === "ready";
  const sub = d.status === "failed" ? `<span class="fail">${esc(d.detail)}</span>`
    : ready ? `${plural(d.page_count, "page")}` : esc(d.detail || "Processing");
  const move = ready ? ` · <label class="move">Subject <select data-move="${d.id}">${S.courses.map((c) =>
    `<option value="${c.id}" ${c.id === d.course_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>` : "";
  return `<li class="doc-row"><div class="grow"><a class="link-title" href="#reader/${d.id}">${esc(d.title)}</a><div class="meta">${sub}${move}</div></div>
    <div class="row">
    <a class="ghost small" href="#reader/${d.id}" aria-label="Open ${esc(d.title)}">Open</a>
    <button class="ghost small" data-act="rename-doc" data-id="${d.id}" data-title="${esc(d.title)}" aria-label="Rename ${esc(d.title)}">Rename</button>
    ${ready ? `<select aria-label="Note style for ${esc(d.title)}" data-note-style="${d.id}">
        <option value="detailed">Full notes</option><option value="summary">Summary</option><option value="cheatsheet">Cheat sheet</option></select>
      <button class="ghost small" data-act="make-notes" data-id="${d.id}">Make notes</button>
      <button class="ghost small" data-act="make-cards" data-id="${d.id}">Flashcards</button>
      <button class="ghost small" data-act="make-quiz" data-id="${d.id}">Practice exam</button>` : ""}
    <button class="ghost small danger" data-act="del-doc" data-id="${d.id}" aria-label="Delete ${esc(d.title)}">Delete</button></div></li>`;
}

function quizForm(z) {
  const body = z.items.map((q, i) => `<div class="q"><div class="q-n">question ${i + 1}</div><div class="prose">${md(q.prompt)}</div>
    ${q.kind === "mcq" ? q.options.map((o, j) => `<label class="option"><input type="radio" name="q${q.id}" value="${j}" data-answer="${q.id}" ${S.quiz.answers[q.id] === String(j) ? "checked" : ""}><span>${md(o)}</span></label>`).join("")
      : `<textarea rows="4" data-answer="${q.id}" aria-label="Your answer">${esc(S.quiz.answers[q.id] || "")}</textarea>`}</div>`).join("");
  return `<div class="page-head"><div><button class="ghost small" data-act="quiz-back">All exams</button><h1 style="margin-top:12px">${esc(z.title)}</h1></div></div>
    ${body}<p style="margin-top:24px"><button class="primary" data-act="quiz-submit">Hand in</button></p>`;
}

function quizResult(z) {
  const body = z.items.map((q, i) => {
    const right = q.score >= 0.75, part = !right && q.score > 0;
    const given = q.kind === "mcq" ? (q.user_answer !== "" ? q.options[+q.user_answer] : "No answer") : q.user_answer || "No answer";
    const model = q.kind === "mcq" ? q.options[q.answer_index] : q.model_answer;
    return `<div class="q"><div class="q-n">question ${i + 1} · ${esc(q.topic)} · <span class="${right ? "right" : "wrong"}">${right ? "correct" : part ? `${Math.round(q.score * 100)}%` : "wrong"}</span></div>
      <div class="prose">${md(q.prompt)}</div>
      <p class="muted">You wrote: ${esc(given)}</p>
      <div class="answer-box prose" data-doc="${q.document_id ?? ""}"><div>${md(model)}</div><p>${md(q.feedback || q.explanation)}${q.page ? ` [p. ${q.page}]`.replace(/\[p\. (\d+)\]/, `<button type="button" class="cite" data-page="$1">p.&nbsp;$1</button>`) : ""}</p></div></div>`;
  }).join("");
  return `<div class="page-head"><div><button class="ghost small" data-act="quiz-back">All exams</button><h1 style="margin-top:12px">${esc(z.title)}</h1></div><span class="score">${Math.round(z.score * 100)}%</span></div>${body}`;
}

function examBlock(e) {
  const header = `<div class="exam-head"><h2>${esc(e.name)} <span class="meta">${esc(e.course)} · ${when(e.date)}</span></h2>
    <div class="row">${e.status === "ready" ? `<span class="meta">${e.done} of ${e.total} done</span><button class="ghost small" data-act="exam-rebuild" data-id="${e.id}">Rebuild around weak topics</button>` : ""}
    <button class="ghost small danger" data-act="exam-del" data-id="${e.id}">Delete</button></div></div>`;
  if (e.status === "planning") return `<div class="section">${header}<p class="typing">working out your schedule…</p></div>`;
  if (e.status === "failed") return `<div class="section">${header}<p class="fail">${esc(e.detail || "The plan didn't finish.")}</p></div>`;
  const days = {};
  e.tasks.forEach((t) => (days[t.date] ||= []).push(t));
  const today = todayISO();
  return `<div class="section">${header}${Object.entries(days).map(([d, tasks]) => `<div class="day ${d === today ? "is-today" : ""}">
    <div class="day-label">${d === today ? "today" : esc(when(d))}</div>
    <div>${tasks.map((t) => `<div class="task ${t.done ? "done" : ""}"><label><input type="checkbox" data-act="tick" data-id="${t.id}" ${t.done ? "checked" : ""}>
      <span class="grow"><span class="title">${esc(t.topic)}</span> <span class="meta">${KIND[t.kind]} · ${t.minutes} min</span></span></label></div>`).join("")}</div></div>`).join("")}</div>`;
}

// ---- Rendering ----------------------------------------------------------

let renderSeq = 0;
async function render() {
  const seq = ++renderSeq;
  const section = S.route === "reader" ? "library" : S.route;
  document.querySelectorAll(".rail a").forEach((a) => {
    if (a.getAttribute("href") === `#${section}`) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  try {
    const html = await VIEWS[S.route]();
    if (seq !== renderSeq || html === null) return;
    const top = view.scrollTop;
    view.innerHTML = html;
    view.scrollTop = top;
    if (S.route === "library" || S.route === "papers") wireDrop();
    if (S.route === "reader" && S.reader.page) document.getElementById(`slide-${S.reader.page}`)?.scrollIntoView();
  } catch (e) { view.innerHTML = `<p class="fail">${esc(e.message)}</p>`; }
  refreshDue();
}

const refreshDue = guard(async () => {
  const s = await api("/api/cards/stats");
  const el = $("#due-count");
  el.hidden = !s.due; el.textContent = s.due;
});

function route() {
  const [r, id, page] = (location.hash.slice(1) || "today").split("/");
  S.reader = r === "reader" ? { id: +id, page: page || null } : null;
  S.route = VIEWS[r] && (r !== "reader" || +id) ? r : "today";
  $(".shell").classList.toggle("chat-full", S.route === "chat");
  stick();  // the thread changes width with the page, so keep the newest message in view
  if (S.route === "chat") $("#ask-text").focus();
  $("#drawer").hidden = true;
  view.scrollTop = 0;
  render();
}

function wireDrop() {
  const drop = $("#drop");
  if (!drop) return;
  $("#file").onchange = (e) => upload(e.target.files, drop.dataset);
  ["dragover", "dragenter"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => upload(e.dataTransfer.files, drop.dataset));
}

const upload = guard(async (files, target = {}) => {
  if (!files.length) return;
  const form = new FormData();
  if (target.course) form.append("course_id", target.course);
  if (target.kind) form.append("kind", target.kind);
  [...files].forEach((f) => form.append("files", f));
  const r = await api("/api/documents", { method: "POST", form });
  if (r.rejected.length) toast(`Can't read: ${r.rejected.join(", ")}`, "error");
});

async function nextCard() {
  S.cards.i++; S.cards.flipped = false;
  await render();
}

async function grade(g) {
  const card = S.cards.queue[S.cards.i];
  await api(`/api/cards/${card.id}/review`, { method: "POST", json: { grade: g } });
  if (g === 0) S.cards.queue.push(card);  // see it again this session
  await nextCard();
}

const ACTIONS = {
  "music-open": (d) => api(`/api/music/${d.id}/open`, { method: "POST" }),
  "music-del": (d) => api(`/api/music/${d.id}`, { method: "DELETE" }),
  "music-here": (d) => {
    const player = $("#player");
    player.hidden = false;
    player.innerHTML = `<button class="ghost small" data-act="music-close" type="button">Close player</button>
      <iframe title="Spotify player" src="https://open.spotify.com/embed/${encodeURIComponent(d.kind)}/${encodeURIComponent(d.sid)}?utm_source=generator" height="152" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>`;
  },
  "music-close": () => { const p = $("#player"); p.innerHTML = ""; p.hidden = true; },
  "papers-course": (d) => { S.papers.course = +d.id; render(); },
  "paper-open": (d) => { S.papers.open = +d.id; view.scrollTop = 0; render(); },
  "paper-back": () => { S.papers.open = null; render(); },
  "paper-del": async (d) => { if (confirm("Delete this practice paper?")) await api(`/api/papers/${d.id}`, { method: "DELETE" }); },
  "paper-answers": (d, el) => { S.papers.answers = el.checked; $(".exam-sheet")?.classList.toggle("show-answers", el.checked); },
  "paper-print": () => window.print(),
  "open-external": (d) => api(`/api/documents/${d.id}/open`, { method: "POST" }),
  "rename-doc": async (d) => {
    const title = prompt("New name for this document", d.title);
    if (title && title.trim()) await api(`/api/documents/${d.id}`, { method: "PATCH", json: { title: title.trim() } });
  },
  "pick-course": (d) => { S.library.course = d.id ? +d.id : null; render(); },
  "del-course": async (d) => { if (confirm("Delete this subject and everything made from it? This can't be undone.")) await api(`/api/courses/${d.id}`, { method: "DELETE" }); },
  "del-doc": async (d) => { if (confirm("Delete this document? Notes and cards made from it stay.")) await api(`/api/documents/${d.id}`, { method: "DELETE" }); },
  "make-notes": async (d) => {
    const style = $(`[data-note-style="${d.id}"]`).value;
    const r = await api(`/api/documents/${d.id}/notes`, { method: "POST", json: { style } });
    S.notes = { open: r.id, editing: false }; location.hash = "notes";
  },
  "make-cards": async (d) => {
    const n = prompt("How many flashcards?", "20");
    if (!n) return;
    await api(`/api/documents/${d.id}/cards`, { method: "POST", json: { count: +n || 20 } });
    toast("Writing flashcards. They'll appear in Flashcards as they're made.");
  },
  "make-quiz": async (d) => {
    const n = prompt("How many questions?", "10");
    if (!n) return;
    await api(`/api/documents/${d.id}/quiz`, { method: "POST", json: { count: +n || 10 } });
    S.quiz = { open: null, answers: {} }; location.hash = "practice";
  },
  "note-open": (d) => { S.notes = { ...S.notes, open: +d.id, editing: false }; render(); },
  "note-back": () => { S.notes = { ...S.notes, open: null, editing: false }; render(); },
  "notes-course": (d) => { S.notes.course = d.id ? +d.id : null; render(); },
  "note-edit": () => { S.notes.editing = true; view.innerHTML = ""; render(); },
  "note-cancel": () => { S.notes.editing = false; view.innerHTML = ""; render(); },
  "note-save": async () => {
    await api(`/api/notes/${S.notes.open}`, { method: "PUT", json: { body: $("#note-body").value } });
    S.notes.editing = false; view.innerHTML = ""; toast("Saved."); render();
  },
  "note-del": async () => {
    if (!confirm("Delete these notes?")) return;
    await api(`/api/notes/${S.notes.open}`, { method: "DELETE" });
    S.notes = { open: null, editing: false }; render();
  },
  "start-cards": async () => { S.cards = { queue: await api("/api/cards/due"), i: 0, flipped: false }; render(); },
  flip: () => { S.cards.flipped = !S.cards.flipped; $(".card-stage").classList.toggle("flipped", S.cards.flipped); render(); },
  grade: (d) => grade(+d.g),
  "quiz-open": (d) => { S.quiz = { open: +d.id, answers: {}, status: null }; view.innerHTML = ""; render(); },
  "quiz-back": () => { S.quiz = { open: null, answers: {} }; render(); },
  "quiz-del": async (d) => { if (confirm("Delete this practice exam?")) await api(`/api/quizzes/${d.id}`, { method: "DELETE" }); },
  "quiz-submit": async () => {
    const unanswered = view.querySelectorAll(".q").length - Object.values(S.quiz.answers).filter(Boolean).length;
    if (unanswered > 0 && !confirm(`Hand in with ${plural(unanswered, "question")} unanswered?`)) return;
    await api(`/api/quizzes/${S.quiz.open}/submit`, { method: "POST", json: { answers: S.quiz.answers } });
    S.quiz.status = "grading"; view.innerHTML = ""; render();
  },
  tick: async (d, el) => { await api(`/api/tasks/${d.id}`, { method: "PATCH", json: { done: el.checked } }); },
  "exam-del": async (d) => { if (confirm("Delete this exam and its plan?")) await api(`/api/exams/${d.id}`, { method: "DELETE" }); },
  "exam-rebuild": (d) => api(`/api/exams/${d.id}/rebuild`, { method: "POST" }),
  provider: async (d) => { await api("/api/settings", { method: "PUT", json: { provider: d.v } }); },
  "ocr-provider": async (d) => { await api("/api/settings", { method: "PUT", json: { ocr_provider: d.v } }); },
  "del-key": async () => { if (confirm("Remove the saved Claude key?")) await api("/api/settings/claude-key", { method: "DELETE" }); },
  "del-openai-key": async () => {
    if (!confirm("Remove the saved OpenAI key?")) return;
    await api("/api/settings/openai-key", { method: "DELETE" });
    S.openaiModels = null;
  },
  "ask-about": (d) => send(`Explain ${d.topic} simply, then give me one exam-style example.`),
};

document.addEventListener("click", guard(async (e) => {
  const cite = e.target.closest(".cite");
  if (cite) return openCitation(cite);
  const el = e.target.closest("[data-act]");
  if (el) await ACTIONS[el.dataset.act](el.dataset, el);
}));

document.addEventListener("input", (e) => {
  const id = e.target.dataset.answer;
  if (id) S.quiz.answers[id] = e.target.value;
});

document.addEventListener("change", guard(async (e) => {
  if (e.target.dataset.move) {
    await api(`/api/documents/${e.target.dataset.move}`, { method: "PATCH", json: { course_id: +e.target.value } });
    return;
  }
  const key = e.target.dataset.setting;
  if (!key) return;
  const value = e.target.type === "checkbox" ? e.target.checked : e.target.type === "range" ? +e.target.value : e.target.value;
  await api("/api/settings", { method: "PUT", json: { [key]: value } });
}));

document.addEventListener("submit", guard(async (e) => {
  const kind = e.target.dataset.form;
  if (!kind) return;
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  if (kind === "course") {
    const r = await api("/api/courses", { method: "POST", json: { name: f.name } });
    S.library.course = r.id;
  } else if (kind === "paper") {
    const ids = [...e.target.querySelectorAll('input[name="doc"]:checked')].map((i) => +i.value);
    if (!ids.length) { toast("Pick at least one module.", "error"); return; }
    await api("/api/papers", { method: "POST", json: { course_id: S.papers.course, document_ids: ids } });
    toast("Writing your paper. It'll appear under Your practice papers.");
  } else if (kind === "music") {
    await api("/api/music", { method: "POST", json: { link: f.link } });
  } else if (kind === "key") {
    await api("/api/settings/claude-key", { method: "PUT", json: { key: f.key } });
    toast("Key saved.");
  } else if (kind === "openai-key") {
    await api("/api/settings/openai-key", { method: "PUT", json: { key: f.key } });
    S.openaiModels = null;
    toast("Key saved. Pick ChatGPT under Thinking to use it.");
  } else if (kind === "exam") {
    await api("/api/exams", { method: "POST", json: {
      course_id: +f.course_id, name: f.name, date: f.date, minutes_per_day: +f.minutes_per_day, topics: f.topics.split("\n"),
    } });
  }
}));

document.addEventListener("keydown", (e) => {
  if (S.route !== "cards" || !S.cards.queue || /TEXTAREA|INPUT|SELECT/.test(e.target.tagName)) return;
  if (e.code === "Space") { e.preventDefault(); ACTIONS.flip(); }
  else if (S.cards.flipped && "1234".includes(e.key)) guard(grade)(+e.key - 1);
});

const openCitation = guard(async (cite) => {
  const page = cite.dataset.page;
  const host = cite.closest("[data-doc]");
  let doc = host?.dataset.doc;
  if (!doc) {
    const sources = JSON.parse(cite.closest("[data-sources]")?.dataset.sources || "[]");
    doc = (sources.find((s) => String(s.page) === page) || sources[0])?.document_id;
  }
  if (!doc) return;
  const p = await api(`/api/documents/${doc}/pages/${page}`);
  $("#drawer-body").innerHTML = `<h2>${esc(p.title)}</h2><p class="hand" style="font-size:1.15rem;color:var(--red)">page ${p.page}</p><p><a class="ghost small" href="#reader/${doc}/${p.page}">Open the document here</a></p><pre>${esc(p.text)}</pre>`;
  $("#drawer").hidden = false;
});
$("#drawer-close").onclick = () => { $("#drawer").hidden = true; };

// ---- Assistant margin ---------------------------------------------------

const thread = $("#thread");
const bubbles = new Map();

function stick() { thread.scrollTop = thread.scrollHeight; }

function addFiles(files) {
  if (!files?.length) return;
  const box = document.createElement("div");
  box.className = "msg-files";
  files.forEach((f) => {
    if (f.image) {
      const img = document.createElement("img");
      img.src = `/api/attachments/${f.file}`; img.alt = f.name; img.title = f.name;
      img.onerror = () => img.replaceWith(Object.assign(document.createElement("span"), { className: "chip", textContent: f.name }));
      box.append(img);
    } else {
      const chip = document.createElement("span");
      chip.className = "chip"; chip.textContent = f.name;
      box.append(chip);
    }
  });
  thread.append(box);
}

function addUser(content, via, files) {
  $(".empty-margin")?.remove();
  addFiles(files);
  const el = document.createElement("div");
  el.className = "msg-user";
  el.textContent = content;
  if (via === "voice") {
    const tag = document.createElement("span");
    tag.className = "via"; tag.textContent = "said aloud";
    thread.append(tag);
  }
  thread.append(el);
  stick();
}

function addAi(id, sources, content = "", error = false) {
  $(".empty-margin")?.remove();
  const el = document.createElement("div");
  el.className = `msg-ai prose${error ? " error" : ""}`;
  el.dataset.sources = JSON.stringify(sources || []);
  el.innerHTML = content ? md(content) : `<span class="typing">thinking…</span>`;
  thread.append(el);
  bubbles.set(id, { el, text: content, pending: false });
  stick();
}

const loadThread = guard(async () => {
  const msgs = await api("/api/chat");
  thread.innerHTML = "";
  bubbles.clear();
  if (!msgs.length) thread.innerHTML = `<p class="empty-margin">say “hey Jarvis” or type a doubt below</p>`;
  msgs.forEach((m) => m.role === "user" ? addUser(m.content, m.via, m.attachments) : addAi(m.id, m.sources, m.content));
});

const send = guard(async (text) => {
  await api("/api/chat", { method: "POST", json: { text } });
});

// Files waiting to go with the next message
const ATTACHABLE = /\.(pdf|docx|pptx|txt|md|png|jpe?g|webp|gif|bmp)$/i;
let attached = [];
function showChips() {
  const chips = $("#chips");
  chips.hidden = !attached.length;
  chips.innerHTML = "";
  attached.forEach((f, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    if (f.type.startsWith("image/")) {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(f); img.alt = "";
      img.onload = () => URL.revokeObjectURL(img.src);
      chip.append(img);
    }
    const name = document.createElement("span");
    name.textContent = f.name;
    const x = document.createElement("button");
    x.type = "button"; x.textContent = "×"; x.setAttribute("aria-label", `Remove ${f.name}`);
    x.onclick = () => { attached.splice(i, 1); showChips(); };
    chip.append(name, x);
    chips.append(chip);
  });
}
function attach(files) {
  const all = [...files];
  const ok = all.filter((f) => ATTACHABLE.test(f.name) || f.type.startsWith("image/"));
  if (ok.length < all.length) toast("Margin can read PDF, Word, PowerPoint, text and picture files.", "error");
  if (attached.length + ok.length > 6) toast("Up to 6 files per message.");
  attached = [...attached, ...ok].slice(0, 6);
  showChips();
  $("#ask-text").focus();
}
$("#attach").onclick = () => $("#attach-file").click();
$("#attach-file").onchange = (e) => { attach(e.target.files); e.target.value = ""; };
const askBox = $("#ask");
const panel = $(".margin");
["dragover", "dragenter"].forEach((n) => panel.addEventListener(n, (e) => {
  if (![...e.dataTransfer.types].includes("Files")) return;
  e.preventDefault(); askBox.classList.add("over");
}));
["dragleave", "drop"].forEach((n) => panel.addEventListener(n, (e) => { e.preventDefault(); askBox.classList.remove("over"); }));
panel.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) attach(e.dataTransfer.files); });
$("#ask-text").addEventListener("paste", (e) => {
  const files = [...e.clipboardData.files];
  if (!files.length) return;
  e.preventDefault();
  attach(files.map((f, i) => f.name && f.name !== "image.png" ? f : new File([f], `pasted-${Date.now()}-${i}.png`, { type: f.type })));
});

$("#ask").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const box = $("#ask-text");
  const text = box.value.trim();
  if (!text && !attached.length) return;
  if (!attached.length) { box.value = ""; await send(text); return; }
  const form = new FormData();
  form.append("text", text);
  attached.forEach((f) => form.append("files", f));
  const files = attached;
  box.value = ""; attached = []; showChips();
  try { await api("/api/chat", { method: "POST", form }); }
  catch (err) { attached = files; showChips(); box.value = text; throw err; }
}));
$("#new-chat").onclick = guard(async () => {
  if (confirm("Start a new chat? This clears the conversation and its attachments.")) await api("/api/chat", { method: "DELETE" });
});
$("#ask-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask").requestSubmit(); }
});

$("#focus").onchange = guard(async (e) => {
  await api("/api/settings", { method: "PUT", json: { focus_course_id: e.target.value ? +e.target.value : null } });
});

$("#brain").onchange = guard(async (e) => {
  const need = { claude: ["claude_configured", "Claude"], openai: ["openai_configured", "OpenAI"] }[e.target.value];
  if (need && !S.state[need[0]]) {
    e.target.value = S.state.settings.provider;
    toast(`Add your ${need[1]} API key here first.`);
    location.hash = "settings";
    return;
  }
  await api("/api/settings", { method: "PUT", json: { provider: e.target.value } });
});

const refreshFocus = guard(async () => {
  const [state, courses] = await Promise.all([api("/api/state"), api("/api/courses")]);
  S.state = state;
  $("#brain").value = state.settings.provider;
  const v = state.settings.focus_course_id;
  $("#focus").innerHTML = `<option value="">All courses</option>` +
    courses.map((c) => `<option value="${c.id}" ${c.id === v ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  showVoice(state.voice);
});

const VOICE_TEXT = {
  off: "voice is off in this window", loading: "getting voice ready…", idle: "say “hey Jarvis”", listening: "listening…",
  thinking: "thinking…", speaking: "answering…", muted: "microphone muted", error: "voice unavailable",
};
function showVoice(v) {
  $("#voice-dot").dataset.state = v.state;
  $("#voice-text").textContent = v.state === "error" ? v.detail : VOICE_TEXT[v.state] ?? v.state;
  const mute = $("#mute");
  mute.textContent = v.muted ? "Unmute" : "Mute";
  mute.hidden = ["off", "error", "loading"].includes(v.state);
  $("#talk").hidden = v.state !== "idle";
  S.muted = v.muted;
}
$("#talk").onclick = guard(async () => { showVoice(await api("/api/voice/trigger", { method: "POST" })); });
$("#mute").onclick = guard(async () => { showVoice(await api("/api/voice/mute", { method: "POST", json: { muted: !S.muted } })); });

// ---- Live updates -------------------------------------------------------

const DEPENDS = {
  documents: ["library", "today", "papers"], courses: ["library", "plan", "papers"], papers: ["papers"], notes: ["notes"], cards: ["cards", "today"],
  quizzes: ["practice", "today"], exams: ["plan", "today"], settings: ["settings"], chat: [], music: ["music"],
};

let noteDirty = false;
function onEvent(ev) {
  switch (ev.type) {
    case "ws.open": refreshFocus(); loadThread(); break;
    case "toast": toast(ev.message, ev.tone); break;
    case "navigate": location.hash = ev.page; break;
    case "voice.state": showVoice(ev); break;
    case "changed":
      if (ev.what === "courses" || ev.what === "settings") refreshFocus();
      if (ev.what === "chat") loadThread();
      if (ev.what === "cards" && S.cards.queue) break;
      if (DEPENDS[ev.what]?.includes(S.route)) render();
      break;
    case "note.delta":
      if (S.route === "notes" && S.notes.open === ev.id && !S.notes.editing && !noteDirty) {
        noteDirty = true;
        setTimeout(() => { noteDirty = false; render(); }, 700);
      }
      break;
    case "chat.user": addUser(ev.content, ev.via, ev.attachments); break;
    case "chat.start": addAi(ev.id, ev.sources); break;
    case "chat.delta": {
      const b = bubbles.get(ev.id);
      if (!b) break;
      b.text += ev.text;
      if (!b.pending) {
        b.pending = true;
        requestAnimationFrame(() => { b.pending = false; b.el.innerHTML = md(b.text); stick(); });
      }
      break;
    }
    case "chat.done": {
      const b = bubbles.get(ev.id);
      if (!b) break;
      b.text = ev.content;
      b.el.innerHTML = md(ev.content);
      b.el.classList.toggle("error", !!ev.error);
      stick();
      break;
    }
  }
}

window.addEventListener("hashchange", route);
connect(onEvent);
route();
