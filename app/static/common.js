// Helpers for the main window.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export async function api(path, { method = "GET", json, form } = {}) {
  const res = await fetch(path, {
    method,
    headers: json !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: json !== undefined ? JSON.stringify(json) : form,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : "That didn't work. Try again.");
  }
  return data;
}

// Maths is lifted out first so Markdown can't mangle it, and put back after sanitising.
export function md(text) {
  const maths = [];
  const stash = (tex, display) => {
    maths.push(katex.renderToString(tex, { displayMode: display, throwOnError: false, trust: false }));
    return `@@M${maths.length - 1}@@`;
  };
  const source = String(text ?? "")
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => stash(t, true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => stash(t, true))
    .replace(/\\\((.+?)\\\)/g, (_, t) => stash(t, false))
    .replace(/(^|[^\\$\w])\$([^\s$](?:[^$\n]*[^\s$])?)\$(?![\d$])/g, (_, pre, t) => pre + stash(t, false));
  // Local models sometimes write LaTeX formatting commands in plain prose.
  const tidied = source
    .replace(/\\textbf\{([^{}]*)\}/g, "**$1**")
    .replace(/\\(?:textit|emph)\{([^{}]*)\}/g, "*$1*");
  const html = DOMPurify.sanitize(marked.parse(tidied));
  return html
    .replace(/\[(?:[^\]\[<]*,\s*)?p\.\s*(\d+)\]/g, (_, p) => `<button type="button" class="cite" data-page="${p}">p.&nbsp;${p}</button>`)
    .replace(/@@M(\d+)@@/g, (_, i) => maths[+i]);
}

export function connect(onEvent) {
  let backoff = 500;
  const open = () => {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => { backoff = 500; onEvent({ type: "ws.open" }); };
    ws.onmessage = (m) => onEvent(JSON.parse(m.data));
    ws.onclose = () => { setTimeout(open, backoff); backoff = Math.min(backoff * 2, 5000); };
  };
  open();
}
