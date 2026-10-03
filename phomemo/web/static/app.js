/* Thermal: the print screen. Plain JS, no build step. */
(() => {
  "use strict";

  const DOTS_PER_MM = 8;
  const WIDTH_DOTS = 1664;
  const DITHERS = [["Auto", null], ["Sharp", "threshold"], ["Photo", "atkinson"], ["Ordered", "ordered"]];
  const MEDIA = [["a4", "A4 · 210 × 297 mm"], ["letter", "US Letter · 216 × 279 mm"], ["continuous", "Continuous (fanfold)"]];
  const RENDER_KEYS = ["media", "dither", "fit"];

  const ICON = {
    up: '<path d="M8 10.5V2M4.5 5.5L8 2l3.5 3.5M2.5 10.5v3h11v-3"/>',
    printer: '<path d="M4.5 6V1.5h7V6M4.5 12H2.5V6.5h11V12h-2M4.5 10h7v4.5h-7z"/>',
    printerOff: '<path d="M4.5 6V1.5h7V6M4.5 12H2.5V6.5h11V12h-2M4.5 10h7v4.5h-7zM1.5 1.5l13 13"/>',
    usb: '<path d="M8 1.5v10M5.5 4L8 1.5 10.5 4M8 11.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3M4 6v2.5l4 2M12 5.5v2.5l-4 2"/>',
    ble: '<path d="M4.5 4.5l7 7L8 15V1l3.5 3.5-7 7"/>',
    file: '<path d="M9.5 1.5h-6v13h9v-10zM9.5 1.5v3h3"/>',
    x: '<path d="M4 4l8 8M12 4l-8 8"/>',
    grip: '<path d="M6 4h.01M10 4h.01M6 8h.01M10 8h.01M6 12h.01M10 12h.01" stroke-width="2.4"/>',
    left: '<path d="M10 3L5 8l5 5"/>', right: '<path d="M6 3l5 5-5 5"/>',
  };
  const icon = (name) => `<svg class="tp-icon" viewBox="0 0 16 16" aria-hidden="true">${ICON[name]}</svg>`;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);

  // ---- state ---------------------------------------------------------------

  const defaults = { media: "a4", density: 5, dither: null, copies: 1, fit: "page", via: "ble" };     // Bluetooth first; USB is the fallback
  const settings = Object.assign({}, defaults, load("thermal.settings"));
  const state = {
    files: [],            // {id, name, kind, size, status: uploading|rendering|ready|error, error, pages:[{h}], version}
    selected: null,
    status: null,         // /api/status
    statusError: false,
    dismissedJob: load("thermal.dismissed"),
    lightbox: null,       // {fileIndex, pageIndex}
  };

  function load(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } }
  function save(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* private window */ } }

  // ---- server --------------------------------------------------------------

  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
    return body;
  }
  const postJSON = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

  // Renders run one at a time: PDF and Markdown rendering are CPU-heavy, and the
  // queue keeps results arriving in the order the person sees them.
  let renderChain = Promise.resolve();
  function queueRender(f) {
    f.status = "rendering";
    const opts = { media: settings.media, dither: settings.dither, fit: settings.fit };
    renderChain = renderChain.then(async () => {
      if (!state.files.includes(f)) return;
      try {
        const info = await postJSON(`/api/files/${f.id}/render`, opts);
        Object.assign(f, { kind: info.kind, pages: info.pages, version: info.version, error: info.error });
        f.status = info.error ? "error" : "ready";
      } catch (e) { f.status = "error"; f.error = e.message; }
      draw();
    });
  }

  async function addFiles(list) {
    const files = [...list].filter((f) => f.size > 0 || f.type.startsWith("text"));
    for (const file of files) {
      const f = { id: null, file, name: file.name || "untitled", size: file.size, status: "uploading", pages: [], version: 0 };
      state.files.push(f);
      await upload(f);
    }
  }

  async function upload(f) {
    f.id = null;
    f.status = "uploading";
    draw();
    try {
      const info = await api("/api/files", { method: "POST", headers: { "X-Filename": encodeURIComponent(f.name) }, body: f.file });
      f.id = info.id;
      queueRender(f);
    } catch (e) { f.status = "error"; f.error = e.message; }
    draw();
  }

  // The server keeps uploads in memory only. If it restarted while this page
  // stayed open, its file ids are gone: send the files again, in queue order.
  let instance = null;
  async function checkInstance(id) {
    const restarted = instance && id !== instance;
    instance = id;              // before re-uploading, so the next poll doesn't start again
    if (!restarted) return;
    for (const f of [...state.files]) {
      if (state.files.includes(f)) await upload(f);
    }
  }

  async function removeFile(f) {
    state.files = state.files.filter((x) => x !== f);
    if (state.selected === f.id) state.selected = null;
    draw();
    if (f.id) api(`/api/files/${f.id}`, { method: "DELETE" }).catch(() => {});
  }

  let polling = null;
  async function poll() {
    clearTimeout(polling);
    try {
      state.status = await api("/api/status");
      state.statusError = false;
      checkInstance(state.status.instance);
    } catch { state.statusError = true; }
    draw();
    const printing = state.status && state.status.job.state === "printing";
    polling = setTimeout(poll, printing ? 500 : 2000);
  }

  async function startPrint() {
    const ids = readyFiles().map((f) => f.id);
    try {
      await postJSON("/api/print", { files: ids, density: settings.density, copies: settings.copies, media: settings.media, via: settings.via });
    } catch (e) { alert(`Couldn't start printing: ${e.message}`); }
    poll();
  }
  async function testPage() {
    try { await postJSON("/api/test", { density: settings.density, via: settings.via }); }
    catch (e) { alert(`Couldn't print the test page: ${e.message}`); }
    poll();
  }
  async function checkBle() {
    try { await postJSON("/api/ble/check", {}); }
    catch (e) { alert(`Couldn't start the check: ${e.message}`); }
    poll();
  }
  async function cancelJob() { await postJSON("/api/job/cancel", {}).catch(() => {}); poll(); }

  // ---- derived -------------------------------------------------------------

  const readyFiles = () => state.files.filter((f) => f.status === "ready" && f.pages.length);
  const job = () => (state.status ? state.status.job : { state: "idle" });
  const printing = () => job().state === "printing";
  const sheetMM = (p) => p.h / DOTS_PER_MM;

  function totals() {
    const files = readyFiles();
    const pages = files.flatMap((f) => f.pages);
    const mm = pages.reduce((s, p) => s + sheetMM(p), 0) * settings.copies;
    const speed = settings.via === "ble" ? (state.status?.ble_mm_per_s || 12) : (state.status?.usb_mm_per_s || 15);
    return { files: files.length, sheets: pages.length * settings.copies, perCopy: pages.length, mm, seconds: mm / speed };
  }

  function connection() {
    const s = state.status;
    if (state.statusError || !s) return { kind: s ? "lost" : "search" };
    if (s.dry_run) return { kind: "dry", detail: s.dry_run };
    if (settings.via === "ble") return { kind: "ble" };
    if (s.usb.length) return { kind: "usb", detail: s.usb[0] };
    return { kind: "off" };
  }
  const canSend = () => ["dry", "ble", "usb"].includes(connection().kind);

  const fmtTime = (s) => {
    s = Math.max(0, Math.round(s));
    if (s >= 3600) return `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  const fmtSize = (b) => (b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${Math.round(b / 1e3)} KB` : `${b} B`);
  const kindTag = (f) => (f.name.split(".").pop() || f.kind || "?").slice(0, 4).toLowerCase();
  const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
  const thumb = (f, i, full) => `/api/files/${f.id}/pages/${i}?v=${f.version}${full ? "&size=full" : ""}`;

  // ---- views ---------------------------------------------------------------

  function viewPill() {
    const j = job();
    if (j.state === "printing" && j.kind === "check") return `<span class="tp-status tp-status--busy"><span class="tp-dot"></span>Checking Bluetooth…</span>`;
    if (j.state === "printing" && j.via === "ble") return `<span class="tp-status tp-status--busy"><span class="tp-dot"></span>Sending <span class="tp-via">Bluetooth</span></span>`;
    if (j.state === "printing") return `<span class="tp-status tp-status--busy"><span class="tp-dot"></span>Printing <span class="tp-via">${j.page + 1 > j.total ? j.total : j.page + 1}/${j.total}</span></span>`;
    const c = connection();
    switch (c.kind) {
      case "usb": return `<span class="tp-status"><span class="tp-dot"></span>Connected <span class="tp-via">USB</span></span>`;
      case "ble": {
        const paper = state.status?.ble?.paper;
        if (paper === true) return `<span class="tp-status"><span class="tp-dot"></span>Paper loaded <span class="tp-via">Bluetooth</span></span>`;
        if (paper === false) return `<span class="tp-status tp-status--off"><span class="tp-dot"></span>No paper <span class="tp-via">Bluetooth</span></span>`;
        return `<span class="tp-status"><span class="tp-dot"></span>Ready <span class="tp-via">Bluetooth</span></span>`;
      }
      case "dry": return `<span class="tp-status tp-status--warn"><span class="tp-dot"></span>Dry run <span class="tp-via">no printer</span></span>`;
      case "search": return `<span class="tp-status tp-status--busy"><span class="tp-dot"></span>Looking for printer…</span>`;
      case "lost": return `<span class="tp-status tp-status--off"><span class="tp-dot"></span>App server stopped</span>`;
      default: return `<span class="tp-status tp-status--off"><span class="tp-dot"></span>Not connected</span>`;
    }
  }

  function viewConn() {
    const c = connection();
    const card = (cls, glyph, title, detail, action = "") =>
      `<div class="tp-conn ${cls}" role="status"><div class="tp-conn-glyph">${icon(glyph)}</div><div><div class="tp-conn-state"><span class="tp-dot"></span>${title}</div><div class="tp-conn-detail">${detail}</div></div>${action}</div>`;
    const test = `<button class="tp-btn" data-action="test" ${printing() ? "disabled" : ""}>Print test page</button>`;
    let html;
    switch (c.kind) {
      case "usb": html = card("", "usb", "Connected over USB", `Phomemo M08F · ${esc(c.detail)}`, test); break;
      case "dry": html = card("tp-conn--ble", "file", "Dry run", `Nothing prints. Jobs are saved to ${esc(c.detail.split("/").pop())}`, test); break;
      case "ble": html = card("", "ble", "Bluetooth", `Phomemo M08F, connects when you print. Load a sheet first: the light turns green.${paperLine()}`, `<div class="tp-row" style="grid-column:1/-1;gap:var(--space-2)"><button class="tp-btn" data-action="ble-check" ${printing() ? "disabled" : ""}>Check printer</button>${test}</div>`); break;
      case "search": html = card("tp-conn--search", "printer", "Starting…", "Connecting to the app server"); break;
      case "lost": html = card("tp-conn--off", "printerOff", "Lost the app server", "Run <code>phomemo ui</code> again in Terminal, then reload."); break;
      default: html = card("tp-conn--off", "printerOff", "No USB printer", "Plug in the cable, then hold the power button ~3 s until the light is solid red.", `<button class="tp-btn" data-action="retry">Check again</button>`);
    }
    const other = settings.via === "ble"
      ? `<button class="tp-link tp-switch" data-action="via" data-value="usb">Use USB instead</button>`
      : `<button class="tp-link tp-switch" data-action="via" data-value="ble">Back to Bluetooth</button>`;
    return `<div class="tp-stack" style="gap:var(--space-3)"><div class="tp-overline">Printer</div>${html}${other}</div>`;
  }

  // Paper state is only known from what the printer last reported over Bluetooth.
  function paperLine() {
    const b = state.status?.ble;
    if (!b || b.paper === null || b.paper === undefined) return "";
    const ago = Math.max(0, Math.round((state.status.now - b.paper_at) / 60));
    const when = ago < 1 ? "just now" : `${ago} min ago`;
    return b.paper
      ? `<br><span class="tp-paper tp-paper--ok">● Paper loaded</span> <span class="tp-muted">(${when})</span>`
      : `<br><span class="tp-paper tp-paper--out">○ Out of paper</span> <span class="tp-muted">(${when})</span>`;
  }

  function viewSettings() {
    const media = MEDIA.map(([v, l]) => `<option value="${v}" ${settings.media === v ? "selected" : ""}>${l}</option>`).join("");
    const density = Array.from({ length: 8 }, (_, i) => i + 1).map((n) =>
      `<button data-action="density" data-value="${n}" class="${n < settings.density ? "tp-on" : ""}" aria-pressed="${n === settings.density}" aria-label="Density ${n}">${n}</button>`).join("");
    const dither = DITHERS.map(([l, v]) => `<button data-action="dither" data-value="${v ?? ""}" aria-pressed="${settings.dither === v}">${l}</button>`).join("");
    const fit = [["page", "Fit page"], ["width", "Fill width"]].map(([v, l]) => `<button data-action="fit" data-value="${v}" aria-pressed="${settings.fit === v}">${l}</button>`).join("");
    return `<div class="tp-stack">
      <div class="tp-overline">Settings</div>
      <label class="tp-field"><span class="tp-field-label">Paper</span><select class="tp-select" data-action="media">${media}</select></label>
      <div class="tp-field"><div class="tp-field-label">Density <span class="tp-mono">${settings.density} / 8</span></div><div class="tp-density" role="group" aria-label="Density">${density}</div><div class="tp-scale"><span>Lighter</span><span>Darker</span></div></div>
      <div class="tp-field"><div class="tp-field-label">Dithering</div><div class="tp-seg" role="group" aria-label="Dithering">${dither}</div><p class="tp-caption">Auto picks per file: hybrid for PDFs, sharp for text, photo for images.</p></div>
      <div class="tp-field"><div class="tp-field-label">Photos</div><div class="tp-seg" role="group" aria-label="Photo fit">${fit}</div><p class="tp-caption">Fill width can span several sheets.</p></div>
      <div class="tp-field"><div class="tp-field-label">Copies</div><div class="tp-stepper"><button data-action="copies" data-value="-1" aria-label="Fewer copies">−</button><span>${settings.copies}</span><button data-action="copies" data-value="1" aria-label="More copies">+</button></div></div>
    </div>`;
  }

  function viewProgress() {
    const j = job();
    if (j.state === "idle" || (j.state !== "printing" && state.dismissedJob === j.started)) return "";
    const what = j.files.length === 1 ? esc(j.files[0]) : plural(j.files.length, "file");
    const dismiss = `<button class="tp-btn tp-btn--ghost" data-action="dismiss">Dismiss</button>`;
    if (j.kind === "check") {
      if (j.state === "printing") return `<div class="tp-panel"><div class="tp-pad tp-stack" style="gap:var(--space-3)"><div class="tp-progress-row"><span class="tp-heading" style="font-weight:600">Checking the printer…</span><span class="tp-caption">up to ~25 s</span></div><div class="tp-bar tp-bar--indeterminate"><span></span></div><p class="tp-caption">Connecting over Bluetooth and asking whether paper is loaded. The printer is slow to answer just after connecting. Nothing prints.</p></div></div>`;
      const body = j.state === "done"
        ? `<div class="tp-msg tp-msg--ok"><div><strong>Printer found.</strong><div class="tp-msg-detail">Connected and the printer answered. ${state.status?.ble?.paper === true ? "Paper is loaded." : state.status?.ble?.paper === false ? "No paper: load a sheet (the light turns green) before printing." : "It didn't say whether paper is loaded."} Nothing was printed.</div></div></div>`
        : `<div class="tp-msg tp-msg--error">${icon("x")}<div><strong>Couldn't reach the printer.</strong><div class="tp-msg-detail">${esc(j.error || "unknown error")}</div></div></div>`;
      return `<div class="tp-panel"><div class="tp-panel-head">${body}${dismiss}</div></div>`;
    }
    const ws = state.status?.ble?.waiting_since;
    const paused = ws && state.status.now - ws > 3 ? state.status.now - ws : 0;
    if (j.state === "printing" && j.via === "ble") {
      return `<div class="tp-panel tp-progress-panel">
        <div class="tp-panel-head"><div class="tp-row">${viewPill()}<span class="tp-caption">${what}</span></div></div>
        <div class="tp-pad"><div class="tp-progress">
          <div class="tp-progress-row"><span class="tp-figure" style="font-size:24px;line-height:28px">${paused ? "Printer paused" : "Sending over Bluetooth…"}</span><span class="tp-mono tp-muted" style="font-size:12px">${paused ? fmtTime(paused) : plural(j.total, "page")}</span></div>
          <div class="tp-bar tp-bar--indeterminate"><span></span></div>
          <p class="tp-caption">${paused
            ? (state.status.ble.paper === false ? "The printer reports it's out of paper. Load a sheet; printing continues when it's ready." : "The printer has stopped taking data, usually to cool down after dark areas. It resumes on its own; this waits up to 3 minutes.")
            : "Connecting to the printer, then sending the whole job. Page progress isn't available over Bluetooth."}</p>
        </div></div></div>`;
    }
    if (j.state === "printing") {
      const t = totals();
      const perSheet = t.sheets ? t.mm / t.sheets : 297;
      const left = ((j.total - j.page) * perSheet) / (settings.via === "ble" ? state.status.ble_mm_per_s : state.status.usb_mm_per_s);
      const pct = j.total ? (j.page / j.total) * 100 : 0;
      return `<div class="tp-panel tp-progress-panel">
        <div class="tp-panel-head"><div class="tp-row">${viewPill()}<span class="tp-caption">${what}</span></div><button class="tp-btn" data-action="cancel">Cancel</button></div>
        <div class="tp-pad"><div class="tp-progress">
          <div class="tp-progress-row"><span class="tp-figure" style="font-size:24px;line-height:28px">Page ${Math.min(j.page + 1, j.total)} <small>of ${j.total}</small></span><span class="tp-mono tp-muted" style="font-size:12px">~${fmtTime(left)} left</span></div>
          <div class="tp-bar" style="--pages:${Math.min(j.total, 60)}"><span style="width:${pct}%"></span>${j.total <= 60 ? '<div class="tp-ticks"></div>' : ""}</div>
        </div></div></div>`;
    }
    const msg = {
      done: `<div class="tp-msg tp-msg--ok">Printed ${plural(j.total, "page")} of ${what}.</div>`,
      cancelled: `<div class="tp-msg">Cancelled after ${plural(j.page, "page")}. The printer finishes what it already received.</div>`,
      error: `<div class="tp-msg tp-msg--error">${icon("x")}<div><strong>${j.page ? `Printing stopped after ${plural(j.page, "page")}.` : "Nothing was printed."}</strong><div class="tp-msg-detail">${esc(j.error || "unknown error")}</div>${j.via === "ble" ? '<div class="tp-msg-detail">USB is more reliable: plug in the cable, hold power ~3 s until the light is solid red, and switch the Printer panel to USB.</div>' : ""}</div></div>`,
    }[j.state] || "";
    return `<div class="tp-panel"><div class="tp-panel-head">${msg}${dismiss}</div></div>`;
  }

  function viewSummary() {
    const t = totals();
    const speedNote = settings.via === "ble" ? "over Bluetooth" : "at ~15 mm/s over USB";
    const sheetLen = { a4: "A4 · 297 mm each", letter: "Letter · 279 mm each", continuous: "Continuous, trimmed" }[settings.media];
    const long = t.seconds > 600;
    return `<div class="tp-panel tp-summary">
      <div class="tp-stat"><span class="tp-overline">${settings.media === "continuous" ? "Pages" : "Sheets"}</span><span class="tp-figure">${t.sheets}</span><span class="tp-stat-note">from ${plural(t.files, "file")}${settings.copies > 1 ? ` · ${settings.copies} × ${t.perCopy}` : ""}</span></div>
      <div class="tp-stat"><span class="tp-overline">Paper</span><span class="tp-figure">${(t.mm / 1000).toFixed(2)}<small>m</small></span><span class="tp-stat-note">${sheetLen}</span></div>
      <div class="tp-stat"><span class="tp-overline">Time</span><span class="tp-figure">~${fmtTime(t.seconds)}</span><span class="tp-stat-note ${long ? "tp-stat-note--warn" : ""}">${long ? "Long job: " : ""}${speedNote}</span></div>
      <div class="tp-stat"><span class="tp-overline">Copies</span><span class="tp-figure">${settings.copies}<small>×</small></span><span class="tp-stat-note">density ${settings.density} of 8</span></div>
    </div>`;
  }

  function viewPayout() {
    const j = job();
    const order = readyFiles();
    const total = order.reduce((s, f) => s + f.pages.length, 0);
    const live = j.state === "printing" && settings.copies === 1 && j.total === total;
    let n = 0;
    const groups = state.files.filter((f) => f.status !== "error").map((f, gi) => {
      const fi = state.files.indexOf(f);
      let sheets;
      if (f.status !== "ready") {
        sheets = `<div class="tp-sheet-wrap"><div class="tp-sheet tp-sheet--pending" style="aspect-ratio:210/297"></div><div class="tp-sheet-cap"><span>…</span></div></div>`;
      } else {
        sheets = f.pages.map((p, pi) => {
          const idx = n++;
          const ratio = `${WIDTH_DOTS} / ${p.h}`;
          const tall = p.h / WIDTH_DOTS > 2.2;
          const cls = [tall && "tp-sheet--long", live && idx < j.page && "tp-sheet--done", live && idx === j.page && "tp-sheet--active",
            state.selected === f.id && !live && "tp-sheet--active"].filter(Boolean).join(" ");
          return `<div class="tp-sheet-wrap"><button class="tp-sheet ${cls}" style="aspect-ratio:${tall ? "210/420" : ratio}" data-action="zoom" data-file="${fi}" data-page="${pi}" aria-label="Page ${idx + 1} of ${total}, ${esc(f.name)}"><img src="${thumb(f, pi)}" alt="" loading="lazy"></button><div class="tp-sheet-cap"><span>${String(idx + 1).padStart(2, "0")}</span><span>/${total}</span></div></div>`;
        }).join("");
      }
      const label = `${esc(f.name)}${f.status === "ready" ? ` · ${f.pages.length}` : ""}`;
      return `${gi ? '<div class="tp-split"></div>' : ""}<div><div class="tp-file-label">${label}</div><div class="tp-roll">${sheets}</div></div>`;
    }).join("");
    const t = totals();
    return `<div class="tp-stack" style="gap:var(--space-3)">
      <div class="tp-row"><span class="tp-overline">Payout</span><span class="tp-mono tp-muted" style="font-size:12px">${plural(t.perCopy, "sheet")} per copy · ${MEDIA.find((m) => m[0] === settings.media)[1].split(" ·")[0]} · ${(t.mm / 1000 / settings.copies).toFixed(2)} m</span></div>
      <div class="tp-payout"><div class="tp-roll">${groups}</div></div>
    </div>`;
  }

  function viewQueue() {
    const rows = state.files.map((f, i) => {
      const st = {
        uploading: `<span class="tp-file-state tp-file-state--busy">Uploading…</span>`,
        rendering: `<span class="tp-file-state tp-file-state--busy">Rendering…</span>`,
        ready: `<span class="tp-file-state tp-file-state--ok">Ready</span>`,
        error: `<span class="tp-file-state tp-file-state--error">${icon("x")} Failed</span>`,
      }[f.status];
      const meta = f.status === "error" ? esc(f.error || "Couldn't render this file") : `${fmtSize(f.size)}${f.kind ? ` · ${f.kind}` : ""}`;
      const mini = f.status === "ready" && f.pages.length ? `<span class="tp-mini"><img src="${thumb(f, 0)}" alt=""></span>` : `<span class="tp-mini"></span>`;
      const pages = f.status === "ready" ? `${f.pages.length} pp` : "— pp";
      return `<li class="tp-file tp-file--clickable ${state.selected === f.id ? "tp-file--selected" : ""}" draggable="${!printing()}" data-index="${i}" data-action="select">
        <span class="tp-grip" title="Drag to reorder">${icon("grip")}</span>${mini}
        <div style="min-width:0"><div class="tp-file-name" title="${esc(f.name)}">${esc(f.name)}</div><div class="tp-file-meta"><span class="tp-tag">${esc(kindTag(f))}</span>${meta}</div></div>
        ${st}<span class="tp-file-pages ${f.status === "ready" ? "" : "tp-muted"}">${pages}</span>
        <button class="tp-btn tp-btn--icon tp-btn--ghost" data-action="remove" data-index="${i}" aria-label="Remove ${esc(f.name)}" ${printing() ? "disabled" : ""}>${icon("x")}</button></li>`;
    }).join("");
    return `<div class="tp-panel">
      <div class="tp-panel-head"><h2 class="tp-panel-title">Queue <span class="tp-muted tp-mono" style="font-weight:400">· ${plural(state.files.length, "file")}</span></h2><button class="tp-btn tp-btn--ghost" data-action="clear" ${printing() ? "disabled" : ""}>Clear</button></div>
      <ul class="tp-queue" id="queue">${rows}</ul></div>`;
  }

  const dropZone = (compact) => compact
    ? `<div class="tp-drop tp-drop--compact" data-action="pick" role="button" tabindex="0"><div class="tp-drop-icon">${icon("up")}</div><div><p class="tp-drop-title">Add more files</p><p class="tp-drop-hint">Drop anywhere, <span class="tp-link">browse</span>, or paste text with ⌘V</p></div></div>`
    : `<div class="tp-drop" data-action="pick" role="button" tabindex="0"><div class="tp-drop-icon">${icon("up")}</div><p class="tp-drop-title">Drop files to print</p><p class="tp-drop-hint">or <span class="tp-link">choose files</span> · paste text with ⌘V</p><div class="tp-drop-types"><span class="tp-tag">pdf</span><span class="tp-tag">md</span><span class="tp-tag">txt</span><span class="tp-tag">jpg</span><span class="tp-tag">png</span></div></div>`;

  function viewMain() {
    const progress = viewProgress();
    if (!state.files.length) {
      return `${progress}<div class="tp-empty"><div><h1 class="tp-display">Ready to print</h1><p class="tp-caption" style="font-size:14px;line-height:20px;margin-top:var(--space-2)">Add PDFs, Markdown, text or photos. You'll see every sheet before anything prints.</p></div>${dropZone(false)}</div>`;
    }
    return `${progress}${readyFiles().length ? viewSummary() + viewPayout() : ""}${dropZone(true)}${viewQueue()}`;
  }

  function viewFoot() {
    const t = totals();
    const busy = state.files.some((f) => f.status === "uploading" || f.status === "rendering");
    const failed = state.files.filter((f) => f.status === "error").length;
    let reason = "", block = false;
    if (printing()) reason = "Printing…";
    else if (!canSend()) { reason = connection().kind === "search" ? "Looking for the printer…" : "Printer not connected: see the Printer panel"; block = true; }
    else if (!state.files.length) reason = "Add a file to print";
    else if (busy) reason = "Waiting for files to finish rendering";
    else if (!t.sheets) { reason = "Nothing printable in the queue"; block = true; }
    else if (failed) reason = `${plural(failed, "file")} failed and will be skipped`;
    else reason = `${plural(t.sheets, "sheet")}, ${(t.mm / 1000).toFixed(2)} m of paper`;
    const disabled = printing() || !canSend() || busy || !t.sheets;
    const label = t.sheets ? `Print ${plural(t.sheets, "page")}` : "Print";
    return `<span class="tp-reason ${block ? "tp-reason--block" : ""}">${reason}</span>
      <button class="tp-btn tp-btn--primary tp-btn--lg" data-action="print" ${disabled ? "disabled" : ""}>${icon("printer")} ${label} <span class="tp-kbd">⌘P</span></button>`;
  }

  function viewLightbox() {
    const lb = state.lightbox;
    if (!lb) return "";
    const f = state.files[lb.fileIndex];
    if (!f || !f.pages[lb.pageIndex]) return "";
    const flat = readyFiles().flatMap((x) => x.pages.map((_, i) => [x, i]));
    const pos = flat.findIndex(([x, i]) => x === f && i === lb.pageIndex);
    const p = f.pages[lb.pageIndex];
    return `<div class="tp-lightbox-bar"><span>${esc(f.name)} · page ${String(pos + 1).padStart(2, "0")}/${flat.length} · ${WIDTH_DOTS} × ${p.h} dots · ${(p.h / DOTS_PER_MM).toFixed(0)} mm</span>
      <div class="tp-row"><button class="tp-btn tp-btn--icon" data-action="lb-step" data-value="-1" aria-label="Previous page" ${pos <= 0 ? "disabled" : ""}>${icon("left")}</button><button class="tp-btn tp-btn--icon" data-action="lb-step" data-value="1" aria-label="Next page" ${pos >= flat.length - 1 ? "disabled" : ""}>${icon("right")}</button><button class="tp-btn" data-action="lb-close">Close</button></div></div>
      <div class="tp-lightbox-body" data-action="lb-close"><img src="${thumb(f, lb.pageIndex, true)}" alt="Page ${pos + 1} at printed resolution"></div>`;
  }

  // Only rewrite a region when its markup changed, so polling never steals focus or hover.
  function region(el, html) { if (el._html !== html) { el.innerHTML = html; el._html = html; } }
  let drawQueued = false;
  function draw() {
    if (drawQueued) return;
    drawQueued = true;
    requestAnimationFrame(() => {
      drawQueued = false;
      region($("pill"), viewPill());
      region($("conn"), viewConn());
      region($("settings"), viewSettings());
      region($("main"), viewMain());
      region($("foot"), viewFoot());
      region($("lightbox"), viewLightbox());
      $("lightbox").hidden = !state.lightbox;
    });
  }

  // ---- interaction ---------------------------------------------------------

  function setSetting(key, value) {
    if (settings[key] === value) return;
    settings[key] = value;
    save("thermal.settings", settings);
    if (RENDER_KEYS.includes(key)) state.files.filter((f) => f.id && f.status !== "uploading").forEach(queueRender);
    draw();
  }

  function stepLightbox(d) {
    const flat = readyFiles().flatMap((x) => x.pages.map((_, i) => [x, i]));
    const lb = state.lightbox;
    const pos = flat.findIndex(([x, i]) => x === state.files[lb.fileIndex] && i === lb.pageIndex) + d;
    if (pos < 0 || pos >= flat.length) return;
    state.lightbox = { fileIndex: state.files.indexOf(flat[pos][0]), pageIndex: flat[pos][1] };
    draw();
  }

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el || el.disabled) return;
    const v = el.dataset.value;
    switch (el.dataset.action) {
      case "pick": $("picker").click(); break;
      case "print": startPrint(); break;
      case "test": testPage(); break;
      case "ble-check": checkBle(); break;
      case "cancel": cancelJob(); break;
      case "retry": state.status = null; draw(); poll(); break;
      case "dismiss": state.dismissedJob = job().started; save("thermal.dismissed", state.dismissedJob); draw(); break;
      case "via": setSetting("via", v); break;
      case "density": setSetting("density", Number(v)); break;
      case "dither": setSetting("dither", v || null); break;
      case "fit": setSetting("fit", v); break;
      case "copies": setSetting("copies", Math.max(1, Math.min(99, settings.copies + Number(v)))); break;
      case "remove": e.stopPropagation(); removeFile(state.files[Number(el.dataset.index)]); break;
      case "clear": [...state.files].forEach(removeFile); break;
      case "select": { const f = state.files[Number(el.dataset.index)]; state.selected = state.selected === f.id ? null : f.id; draw(); break; }
      case "zoom": state.lightbox = { fileIndex: Number(el.dataset.file), pageIndex: Number(el.dataset.page) }; draw(); break;
      case "lb-step": e.stopPropagation(); stepLightbox(Number(v)); break;
      case "lb-close": if (e.target.tagName !== "IMG") { state.lightbox = null; draw(); } break;
    }
  });
  document.addEventListener("change", (e) => { if (e.target.dataset.action === "media") setSetting("media", e.target.value); });
  document.addEventListener("keydown", (e) => {
    if (state.lightbox) {
      if (e.key === "Escape") { state.lightbox = null; draw(); }
      if (e.key === "ArrowRight") stepLightbox(1);
      if (e.key === "ArrowLeft") stepLightbox(-1);
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && e.target.dataset?.action === "pick") { e.preventDefault(); $("picker").click(); }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "p") {
      e.preventDefault();
      const b = document.querySelector('[data-action="print"]');
      if (b && !b.disabled) startPrint();
    }
  });
  $("picker").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

  // Paste: files (screenshots) or plain text become a print job.
  document.addEventListener("paste", (e) => {
    if (e.target.closest?.("input, textarea, select")) return;
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { addFiles(files); return; }
    const text = e.clipboardData?.getData("text/plain");
    if (text && text.trim()) addFiles([new File([text], "pasted-text.txt", { type: "text/plain" })]);
  });

  // Files dragged in from Finder: the whole window is the drop target.
  let dragDepth = 0;
  const isFileDrag = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => { if (!isFileDrag(e)) return; dragDepth++; $("veil").hidden = false; });
  window.addEventListener("dragleave", (e) => { if (!isFileDrag(e)) return; if (--dragDepth <= 0) { dragDepth = 0; $("veil").hidden = true; } });
  window.addEventListener("dragover", (e) => { if (isFileDrag(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault(); dragDepth = 0; $("veil").hidden = true;
    addFiles(e.dataTransfer.files);
  });

  // Reordering the queue: drag a row onto another.
  let dragFrom = null;
  document.addEventListener("dragstart", (e) => {
    const li = e.target.closest?.(".tp-file");
    if (!li) return;
    dragFrom = Number(li.dataset.index);
    li.classList.add("tp-file--dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/x-thermal-row", String(dragFrom));
  });
  document.addEventListener("dragover", (e) => {
    const li = dragFrom !== null && e.target.closest?.(".tp-file");
    if (!li) return;
    e.preventDefault();
    document.querySelectorAll(".tp-file--over").forEach((x) => x.classList.remove("tp-file--over"));
    li.classList.add("tp-file--over");
  });
  document.addEventListener("drop", (e) => {
    const li = dragFrom !== null && e.target.closest?.(".tp-file");
    if (!li) return;
    e.preventDefault();
    const to = Number(li.dataset.index);
    const [moved] = state.files.splice(dragFrom, 1);
    state.files.splice(to, 0, moved);
    dragFrom = null;
    draw();
  });
  document.addEventListener("dragend", () => {
    dragFrom = null;
    document.querySelectorAll(".tp-file--over, .tp-file--dragging").forEach((x) => x.classList.remove("tp-file--over", "tp-file--dragging"));
  });

  draw();
  poll();
})();
