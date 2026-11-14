/* Intern Summit Hub: single-page frontend. No framework, no build step. */
(() => {
  "use strict";

  // ---------- helpers ----------
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const initials = (n) => n.replace(/^Dr\.\s*/, "").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  const fmt = (n) => Number(n || 0).toLocaleString();
  const money = (n) => (n < 0.01 ? "$" + Number(n).toFixed(4) : "$" + Number(n).toFixed(3));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  let SESSION = store.get("css_session");
  if (!SESSION) {
    SESSION = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now());
    store.set("css_session", SESSION);
  }

  async function api(path, opts = {}) {
    const headers = { "X-Session": SESSION, ...(opts.headers || {}) };
    let body = opts.body;
    if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
    const res = await fetch(path, { ...opts, headers, body, credentials: "same-origin" });
    let data = null;
    try { data = await res.json(); } catch { /* non-json */ }
    if (res.status === 401 && !path.startsWith("/api/auth") && !path.startsWith("/api/admin/login")) {
      showGate(); throw new Error("Please sign in.");
    }
    if (res.status === 403 && data?.detail === "password_change_required") {
      showForceChange(); throw new Error("Choose a new password to continue.");
    }
    if (!res.ok) {
      const msg = data?.message || (typeof data?.detail === "string" ? data.detail : data?.detail?.[0]?.msg) || `Request failed (${res.status})`;
      const err = new Error(msg); err.status = res.status; err.data = data; throw err;
    }
    return data;
  }

  function toast(msg) {
    const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
  }

  // In-page replacements for confirm()/alert(): native pop-ups are blocked in some
  // embedded browsers, which silently returns "cancel".
  function modal({ title, body, ok = "OK", cancel = "Cancel", danger = false, html = false }) {
    return new Promise((resolve) => {
      const d = $("#confirm-dialog");
      $("#confirm-title").textContent = title;
      $("#confirm-body")[html ? "innerHTML" : "textContent"] = body;
      const okBtn = $("#confirm-ok"), cancelBtn = $("#confirm-cancel");
      okBtn.textContent = ok; okBtn.classList.toggle("danger-fill", danger);
      cancelBtn.hidden = !cancel; cancelBtn.textContent = cancel || "";
      const done = (v) => { d.close(); okBtn.onclick = cancelBtn.onclick = d.oncancel = null; resolve(v); };
      okBtn.onclick = () => done(true);
      cancelBtn.onclick = () => done(false);
      d.oncancel = (e) => { e.preventDefault(); done(false); };
      d.showModal(); okBtn.focus();
    });
  }

  function avatar(p, cls = "") {
    const name = esc(p.name);
    return `<span class="avatar ${cls}" data-initials="${esc(initials(p.name))}"><img class="avatar ${cls}" src="${esc(p.photo_url)}" alt="${name}" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.textContent=this.parentNode.dataset.initials"></span>`;
  }

  const fmtDate = (iso, o) => new Date(iso).toLocaleString([], o);
  const timeRange = (e) => `${fmtDate(e.starts_at, { hour: "numeric", minute: "2-digit" })} – ${fmtDate(e.ends_at, { hour: "numeric", minute: "2-digit" })} CT`;

  // ---------- state ----------
  let META = {};
  let ME = null;
  let PEOPLE = [];
  const PEOPLE_BY_SLUG = {};

  // ---------- tabs ----------
  function showTab() {
    const tab = (location.hash || "#match").slice(1).split("/")[0];
    const tabs = ["match", "directory", "cohort", "calendar"].concat(META.is_admin ? ["admin"] : []);
    const valid = tabs.includes(tab) ? tab : (META.is_admin && !ME ? "admin" : "match");
    $$(".tab").forEach((s) => (s.hidden = s.id !== "tab-" + valid));
    $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === valid));
    if (valid === "calendar") loadEvents();
    if (valid === "admin") loadAdmin();
    if (valid === "cohort") loadCohort();
    if (valid === "directory" && !$("#dir-grid").children.length) renderDirectory();
  }
  window.addEventListener("hashchange", showTab);

  // ---------- notices (announcements + talks within 48h) ----------
  async function loadNotices() {
    const [anns, events] = await Promise.all([api("/api/announcements"), api("/api/events")]);
    const soon = events.filter((e) => new Date(e.starts_at) - Date.now() < 48 * 3600e3);
    const count = $("#talk-count");
    count.textContent = events.length; count.hidden = !events.length;
    $("#notices").innerHTML =
      anns.map((a) => `<div class="notice"><span class="tag">From the Program Lead</span><span>${esc(a.message)}</span></div>`).join("") +
      soon.map((e) => `<div class="notice talk"><span class="tag">${new Date(e.starts_at) < Date.now() ? "Happening now" : "Coming up"}</span>
        <span><strong>${esc(e.title)}</strong> · ${esc(fmtDate(e.starts_at, { weekday: "short", hour: "numeric", minute: "2-digit" }))} CT
        ${e.prep_note ? `· ${esc(e.prep_note)}` : ""}</span> <a href="#calendar">Details</a></div>`).join("");
  }

  // ---------- person dialog ----------
  const dialog = $("#person-dialog");
  $(".dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  async function openPerson(slug) {
    const p = await api(`/api/people/${encodeURIComponent(slug)}`);
    const f = p.facts || {};
    const list = (label, items) => (items && items.length ? `<div><b>${label}</b><div class="chips">${items.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div></div>` : "");
    $("#person-body").innerHTML = `
      <div class="pd-head">${avatar(p, "lg")}
        <div><h2>${esc(p.name)}</h2>
          <div>${esc(f.title || "")}${f.title ? " · " : ""}${esc(p.org)}</div>
          <div class="chips" style="margin-top:6px"><span class="badge ${p.section === "Co-chair" ? "cochair" : ""}">${esc(p.section)}</span>${f.is_ciso ? '<span class="badge ciso">CISO</span>' : ""}${f.sector ? `<span class="chip accent">${esc(f.sector)}</span>` : ""}</div>
        </div></div>
      <div class="pd-bio">${p.bio.split("\n").map((l) => `<p>${esc(l)}</p>`).join("")}</div>
      <div class="pd-facts">
        ${list("Expertise", f.expertise)}${list("Ask them about", f.ask_them_about)}
        ${list("Background", f.background)}${list("Outside work", f.personal_interests)}
        ${list("Education", f.education)}${list("Certifications", f.certifications)}
      </div>
      ${p.talks.length ? `<h3 class="sub">Upcoming talks</h3>${p.talks.map((e) => `<div class="prep"><strong>${esc(e.title)}</strong> · ${esc(fmtDate(e.starts_at, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))} CT</div>`).join("")}` : ""}
      <div class="event-actions">
        <a class="btn" href="${esc(p.profile_url)}" target="_blank" rel="noopener">Summit profile ↗</a>
        ${p.linkedin_url ? `<a class="btn" href="${esc(p.linkedin_url)}" target="_blank" rel="noopener">LinkedIn ↗</a>` : ""}
        <button class="btn primary" type="button" id="ask-about">Ask Mentor Match about ${esc(p.name.split(" ")[0])}</button>
      </div>`;
    $("#ask-about").onclick = () => { dialog.close(); location.hash = "#match"; sendChat(`How should I approach ${p.name}, and what should I ask?`); };
    dialog.showModal();
  }
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-person]");
    if (t) { e.preventDefault(); openPerson(t.dataset.person); }
  });

  // ---------- directory ----------
  let dirSection = "";
  function personCard(p) {
    const f = p.facts || {};
    return `<button class="person" type="button" data-person="${esc(p.slug)}">
      <div class="top">${avatar(p)}<div><div class="name">${esc(p.name)}</div><div class="org">${esc(p.org)}</div></div></div>
      <div class="title">${esc(f.title || "")}</div>
      <div class="chips">${p.section !== "Think Tank" ? `<span class="badge ${p.section === "Co-chair" ? "cochair" : ""}">${esc(p.section)}</span>` : ""}${f.is_ciso ? '<span class="badge ciso">CISO</span>' : ""}${(f.expertise || []).slice(0, 2).map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>
    </button>`;
  }
  let dirTimer;
  async function renderDirectory() {
    const q = $("#dir-q").value.trim(), sector = $("#dir-sector").value;
    const list = await api(`/api/people?q=${encodeURIComponent(q)}&sector=${encodeURIComponent(sector)}&section=${encodeURIComponent(dirSection)}`);
    $("#dir-count").textContent = `${list.length} ${list.length === 1 ? "person" : "people"}${q ? ` matching “${q}”` : ""}`;
    $("#dir-grid").innerHTML = list.map(personCard).join("") || `<div class="empty">No one matches that. Try a broader search.</div>`;
  }
  $("#dir-q").addEventListener("input", () => { clearTimeout(dirTimer); dirTimer = setTimeout(renderDirectory, 200); });
  $("#dir-sector").addEventListener("change", renderDirectory);
  $("#dir-section").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $$("#dir-section button").forEach((x) => x.classList.toggle("on", x === b));
    dirSection = b.dataset.v; renderDirectory();
  });

  // ---------- profile ----------
  const fileInput = $("#resume-file"), drop = $("#drop");
  function setFileLabel() {
    const f = fileInput.files[0];
    drop.classList.toggle("has-file", !!f);
    $("#drop-label").innerHTML = f ? `<strong>${esc(f.name)}</strong><br><small>${(f.size / 1024).toFixed(0)} KB · click to change</small>` : `<strong>Upload resume</strong><br><small>PDF, DOCX, or TXT · max 2 MB</small>`;
  }
  fileInput.addEventListener("change", setFileLabel);
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; setFileLabel(); } });

  function renderProfile(p, extra = {}) {
    const grp = (label, items) => (items && items.length ? `<div class="group"><b>${label}</b><div class="chips">${items.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div></div>` : "");
    const flags = [];
    if (extra.screened_lines?.length) flags.push(`<div class="flag">🛡️ Removed ${extra.screened_lines.length} line${extra.screened_lines.length > 1 ? "s" : ""} that looked like instructions to an AI before reading your resume.</div>`);
    if (p.injection_detected) flags.push(`<div class="flag">🛡️ The profile reader also flagged: ${esc(p.injection_note)}</div>`);
    $("#profile-view").innerHTML = `<div class="profile-view">
      ${p.first_name ? `<h3>${esc(p.first_name)}</h3>` : ""}
      <div class="sub">${esc([p.school, p.program].filter(Boolean).join(" · "))}</div>
      <p class="small">${esc(p.summary)}</p>
      ${p.career_goal ? `<div class="group"><b>Goal</b>${esc(p.career_goal)}</div>` : ""}
      ${grp("Interests", p.interests)}${grp("Skills", (p.skills || []).slice(0, 10))}${grp("Hobbies", p.hobbies)}${grp("Background", p.background)}
      ${flags.join("")}
      ${extra.usage ? `<div class="usage-line">${usageBits(extra.usage)}</div>` : ""}
      ${extra.resume ? `<div class="resume-saved">📄 <span><b>${esc(extra.resume.filename || "Resume")}</b> saved to your profile · ${esc(fmtDate(extra.resume.uploaded_at, { month: "short", day: "numeric" }))}</span>
        <button class="btn ghost small" type="button" id="remove-resume">Remove</button></div>`
        : (ME ? `<div class="fine">Built from your registration answers. Upload a resume to make the matches sharper.</div>` : "")}
      <button class="btn ghost small" type="button" id="edit-profile" style="margin-top:10px">Update profile</button>
    </div>`;
    $("#profile-view").hidden = false; $("#profile-form").hidden = true;
    $("#edit-profile").onclick = () => { $("#profile-form").hidden = false; $("#profile-view").hidden = true; };
    const rm = $("#remove-resume");
    if (rm) rm.onclick = async () => {
      if (!await modal({ title: "Remove your resume?", body: "This deletes the saved resume and the profile Mentor Match built from it. Mentor Match will go back to using your registration answers.", ok: "Remove resume", danger: true })) return;
      await api("/api/me/resume", { method: "DELETE" });
      const st = await api("/api/chat/state");
      if (st.profile) renderProfile(st.profile, { resume: st.resume }); else { $("#profile-view").hidden = true; $("#profile-form").hidden = false; }
      toast("Resume removed");
    };
  }

  $("#profile-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    if (fileInput.files[0]) fd.append("file", fileInput.files[0]);
    fd.append("intro", $("#intro").value);
    const btn = $("#profile-btn"); btn.disabled = true; btn.textContent = "Reading your resume…";
    try {
      const r = await api("/api/profile", { method: "POST", body: fd });
      renderProfile(r.profile, r);
      addSystem(`Profile ready. Ask me who to meet, or tap a suggestion below.`);
    } catch (err) { toast(err.message); }
    finally { btn.disabled = false; btn.textContent = "Build my profile"; }
  });

  // ---------- chat ----------
  const log = $("#chat-log");
  const scroll = () => { log.scrollTop = log.scrollHeight; };

  function usageBits(u) {
    const cached = u.cache_read_tokens ? ` (${fmt(u.cache_read_tokens)} cached)` : "";
    return `<span>🤖 ${esc(u.model)}</span><span>${fmt(u.input_tokens)} in${cached}</span><span>${fmt(u.output_tokens)} out</span>
      ${u.tool_calls ? `<span>${u.tool_calls} tool call${u.tool_calls > 1 ? "s" : ""}</span>` : ""}<span>${money(u.cost_usd)}</span><span>${(u.latency_ms / 1000).toFixed(1)}s</span>`;
  }

  function addUser(text) {
    const d = document.createElement("div"); d.className = "msg user"; d.textContent = text; log.appendChild(d); scroll();
  }
  function addSystem(text) {
    const d = document.createElement("div"); d.className = "msg system"; d.textContent = text; log.appendChild(d); scroll();
  }
  function addTyping() {
    const d = document.createElement("div"); d.className = "msg bot"; d.innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`;
    log.appendChild(d); scroll(); return d;
  }

  function recCard(r) {
    const p = r.person, f = p.facts || {};
    return `<div class="rec">
      <div class="rec-head">${avatar(p)}<div><div class="name">${esc(p.name)}</div><div class="role">${esc(f.title || "")}${f.title ? " · " : ""}${esc(p.org)}</div></div></div>
      ${r.shared_ground?.length ? `<div class="chips">${r.shared_ground.map((x) => `<span class="chip gold">${esc(x)}</span>`).join("")}</div>` : ""}
      <p class="why">${esc(r.why)}</p>
      <div><div class="lbl">Open with</div><ul>${r.conversation_starters.map((q) => `<li>${esc(q)}</li>`).join("")}</ul></div>
      <div class="rec-actions">
        <button class="btn small" type="button" data-person="${esc(p.slug)}">Full bio</button>
        ${p.linkedin_url ? `<a class="btn small ghost" href="${esc(p.linkedin_url)}" target="_blank" rel="noopener">LinkedIn ↗</a>` : ""}
      </div></div>`;
  }

  function eventMini(e) {
    return `<div class="prep"><strong>${esc(e.title)}</strong> · ${esc(fmtDate(e.starts_at, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))} CT · <a href="#calendar">See talk</a></div>`;
  }

  function addBot(r) {
    const d = document.createElement("div"); d.className = "msg bot";
    d.innerHTML = `<div class="bubble">${esc(r.reply).replace(/\n/g, "<br>")}</div>
      ${r.recommendations?.length ? `<div class="recs">${r.recommendations.map(recCard).join("")}</div>` : ""}
      ${r.events?.length ? `<div style="margin-top:10px"><div class="lbl small muted">Talks worth attending</div>${r.events.map(eventMini).join("")}</div>` : ""}
      ${r.guardrail ? `<div class="guardrail">🛡️ ${esc(r.guardrail)}</div>` : ""}
      ${r.usage ? `<div class="usage-line">${usageBits(r.usage)}</div>` : ""}`;
    log.appendChild(d); scroll();
  }
  function addError(msg) {
    const d = document.createElement("div"); d.className = "msg bot error"; d.innerHTML = `<div class="bubble">${esc(msg)}</div>`; log.appendChild(d); scroll();
  }

  let busy = false;
  async function sendChat(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; $("#send-btn").disabled = true;
    addUser(text); $("#chat-input").value = ""; autoGrow();
    const typing = addTyping();
    try { addBot(await api("/api/chat", { method: "POST", body: { message: text } })); }
    catch (err) { addError(err.message); }
    finally { typing.remove(); busy = false; $("#send-btn").disabled = false; $("#chat-input").focus(); }
  }
  $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendChat($("#chat-input").value); });
  const input = $("#chat-input");
  function autoGrow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; }
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(input.value); } });
  $("#suggestions").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) sendChat(b.textContent); });
  $("#reset-chat").addEventListener("click", async () => {
    await api("/api/chat/reset", { method: "POST" });
    log.innerHTML = "";
    // A new chat clears the conversation, not the saved resume or profile.
    const st = await api("/api/chat/state");
    if (st.profile) renderProfile(st.profile, { resume: st.resume });
    else { $("#profile-view").hidden = true; $("#profile-form").hidden = false; }
    greet(st.resume);
  });

  function greet(resume) {
    const d = document.createElement("div"); d.className = "msg bot";
    d.innerHTML = `<div class="bubble">Hi${ME ? " " + esc(ME.first_name) : ""}! I'm Mentor Match, your guide to the Think Tank. ${resume ? `I have your saved resume (${esc(resume.filename || "resume")}) and profile, so you can ask me who to meet right away. ` : ME && (ME.interests || ME.hobbies || ME.goals) ? "I already know the interests, hobbies, and goals from your registration, so you can ask me who to meet right away. Adding a resume on the left makes the matches sharper. " : ""}${META.llm_ready ? "Add your resume on the left (or just tell me what you're into) and I'll suggest who to meet at the Summit, why, and how to start the conversation." : "<strong>Mentor Match is offline because no API key is configured.</strong> The Think Tank directory and the talks calendar still work."}</div>`;
    log.appendChild(d);
  }

  // ---------- calendar ----------
  async function loadEvents() {
    const events = await api("/api/events");
    if (!events.length) { $("#event-list").innerHTML = `<div class="empty">No talks scheduled yet. Check back soon.</div>`; return; }
    let month = "";
    const rsvped = JSON.parse(store.get("css_rsvps") || "{}");
    $("#event-list").innerHTML = events.map((e) => {
      const d = new Date(e.starts_at), m = d.toLocaleString([], { month: "long", year: "numeric" });
      const head = m !== month ? `<div class="month">${esc(m)}</div>` : ""; month = m;
      const soon = d - Date.now() < 48 * 3600e3;
      return `${head}<article class="event ${soon ? "soon" : ""}" id="event-${e.id}">
        <div class="datebox"><div class="dow">${esc(d.toLocaleString([], { weekday: "short" }))}</div><div class="day">${d.getDate()}</div><div class="mon">${esc(d.toLocaleString([], { month: "short" }))}</div></div>
        <div>
          <div class="badges"><span class="badge">${esc(e.kind)}</span>${e.required ? '<span class="badge req">Required</span>' : ""}${e.is_sample ? '<span class="badge sample">Sample</span>' : ""}</div>
          <h3>${esc(e.title)}</h3>
          <div class="meta"><span>🕒 ${esc(timeRange(e))}</span>${e.location ? `<span>📍 ${esc(e.location)}</span>` : ""}<span>👥 ${e.rsvp_count} going</span></div>
          ${e.speakers.length ? `<div class="speakers">${e.speakers.map((s) => `<button class="speaker" type="button" data-person="${esc(s.slug)}">${avatar(s, "sm")}${esc(s.name)} <span class="muted">· ${esc(s.org)}</span></button>`).join("")}</div>` : ""}
          ${e.description ? `<p>${esc(e.description)}</p>` : ""}
          ${e.prep_note ? `<div class="prep"><strong>Before you go:</strong> ${esc(e.prep_note)}</div>` : ""}
          <div class="event-actions">
            ${e.join_url ? `<a class="btn primary small" href="${esc(e.join_url)}" target="_blank" rel="noopener">Join link ↗</a>` : ""}
            <a class="btn small" href="/api/events/${e.id}.ics" download>Add to calendar</a>
            ${rsvped[e.id] ? `<span class="chip accent">✓ You're going</span>` : `<form class="rsvp" data-id="${e.id}"><button class="btn small" type="submit">I'll attend</button></form>`}
          </div>
        </div></article>`;
    }).join("");
    $$(".rsvp").forEach((f) => f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await api(`/api/events/${f.dataset.id}/rsvp`, { method: "POST" });
        rsvped[f.dataset.id] = 1; store.set("css_rsvps", JSON.stringify(rsvped));
        toast("You're on the list. Add it to your calendar for reminders."); loadEvents();
      } catch (err) { toast(err.message); }
    }));
  }

  // ---------- admin ----------
  let adminSpeakers = [];
  async function loadAdmin() {
    if (!META.is_admin) return;
    $("#admin-panel").hidden = false;
    $("#admin-name").textContent = META.admin_name; $("#admin-title").textContent = META.admin_title;
    await Promise.all([loadInterns(), loadResetRequests(), loadAccessCode(), loadAdminEvents(), loadAnnouncements(), loadDemoStatus()]);
    showAdminSection(store.get("css_admin_sec") || "interns");
  }

  // speaker picker
  function renderSpeakerChips() {
    $("#ev-speakers").innerHTML = adminSpeakers.map((s) => `<span class="chip accent">${esc(PEOPLE_BY_SLUG[s]?.name || s)}<button type="button" data-rm="${esc(s)}" aria-label="Remove">×</button></span>`).join("");
  }
  $("#ev-speakers").addEventListener("click", (e) => { const b = e.target.closest("[data-rm]"); if (b) { adminSpeakers = adminSpeakers.filter((s) => s !== b.dataset.rm); renderSpeakerChips(); } });
  const sq = $("#ev-speaker-q"), opts = $("#ev-speaker-opts");
  sq.addEventListener("input", () => {
    const q = sq.value.trim().toLowerCase();
    if (!q) { opts.hidden = true; return; }
    const hits = PEOPLE.filter((p) => !adminSpeakers.includes(p.slug) && (p.name + " " + p.org).toLowerCase().includes(q)).slice(0, 8);
    opts.innerHTML = hits.map((p) => `<button type="button" data-add="${esc(p.slug)}">${esc(p.name)} <small>· ${esc(p.org)}</small></button>`).join("") || `<button type="button" disabled>No match</button>`;
    opts.hidden = false;
  });
  opts.addEventListener("click", (e) => { const b = e.target.closest("[data-add]"); if (b) { adminSpeakers.push(b.dataset.add); renderSpeakerChips(); sq.value = ""; opts.hidden = true; sq.focus(); } });
  sq.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("[data-add]", opts)?.click(); } });
  document.addEventListener("click", (e) => { if (!e.target.closest(".speaker-picker")) opts.hidden = true; });

  function resetEventForm() {
    $("#event-form").reset(); $("#ev-id").value = ""; adminSpeakers = []; renderSpeakerChips();
    $("#ev-start").value = "12:00"; $("#ev-end").value = "13:00";
    $("#event-form-title").textContent = "Schedule a talk"; $("#ev-save").textContent = "Add to calendar";
    $("#ev-cancel").hidden = true; $("#ev-error").hidden = true;
  }
  $("#ev-cancel").addEventListener("click", resetEventForm);

  $("#event-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const date = $("#ev-date").value;
    const body = {
      title: $("#ev-title").value.trim(), kind: $("#ev-kind").value, speaker_slugs: adminSpeakers,
      starts_at: `${date}T${$("#ev-start").value}`, ends_at: `${date}T${$("#ev-end").value}`,
      location: $("#ev-location").value.trim(), join_url: $("#ev-url").value.trim(),
      description: $("#ev-desc").value.trim(), prep_note: $("#ev-prep").value.trim(), required: $("#ev-required").checked,
    };
    const id = $("#ev-id").value;
    try {
      await api(id ? `/api/admin/events/${id}` : "/api/admin/events", { method: id ? "PUT" : "POST", body });
      toast(id ? "Talk updated" : "Talk added. Interns can see it now."); resetEventForm(); loadAdminEvents(); loadNotices();
    } catch (err) { $("#ev-error").textContent = err.message; $("#ev-error").hidden = false; }
  });

  let ADMIN_EVENTS = [];
  async function loadAdminEvents() {
    ADMIN_EVENTS = await api("/api/admin/events");
    const now = Date.now();
    $("#admin-events").innerHTML = `<thead><tr><th>When</th><th>Talk</th><th>Speakers</th><th class="num">RSVPs</th><th></th></tr></thead><tbody>${
      ADMIN_EVENTS.map((e) => `<tr style="${new Date(e.ends_at) < now ? "opacity:.5" : ""}">
        <td>${esc(fmtDate(e.starts_at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))}</td>
        <td><strong>${esc(e.title)}</strong><br><span class="muted small">${esc(e.kind)}${e.required ? " · required" : ""}${e.is_sample ? " · sample" : ""}</span></td>
        <td>${e.speakers.map((s) => esc(s.name)).join(", ") || "—"}</td>
        <td class="num"><button class="btn ghost small" data-rsvps="${e.id}">${e.rsvp_count}</button></td>
        <td style="white-space:nowrap"><button class="btn small" data-edit="${e.id}">Edit</button> <button class="btn small danger" data-del="${e.id}">Delete</button></td>
      </tr>`).join("") || `<tr><td colspan="5" class="muted">No talks yet.</td></tr>`}</tbody>`;
  }
  $("#admin-events").addEventListener("click", async (e) => {
    const ed = e.target.closest("[data-edit]"), del = e.target.closest("[data-del]"), rs = e.target.closest("[data-rsvps]");
    if (ed) {
      const ev = ADMIN_EVENTS.find((x) => x.id == ed.dataset.edit);
      $("#ev-id").value = ev.id; $("#ev-title").value = ev.title; $("#ev-kind").value = ev.kind;
      $("#ev-date").value = ev.starts_at.slice(0, 10); $("#ev-start").value = ev.starts_at.slice(11, 16); $("#ev-end").value = ev.ends_at.slice(11, 16);
      $("#ev-location").value = ev.location; $("#ev-url").value = ev.join_url; $("#ev-desc").value = ev.description;
      $("#ev-prep").value = ev.prep_note; $("#ev-required").checked = ev.required; adminSpeakers = [...ev.speaker_slugs]; renderSpeakerChips();
      $("#event-form-title").textContent = "Edit talk"; $("#ev-save").textContent = "Save changes"; $("#ev-cancel").hidden = false;
      $("#event-form").scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (del && await modal({ title: "Delete this talk?", body: "Interns will no longer see it, and its RSVPs are removed.", ok: "Delete talk", danger: true })) {
      await api(`/api/admin/events/${del.dataset.del}`, { method: "DELETE" }); toast("Talk deleted"); loadAdminEvents(); loadNotices();
    }
    if (rs) {
      const r = await api(`/api/admin/events/${rs.dataset.rsvps}/rsvps`);
      await modal({ title: r.names.length ? `Going (${r.names.length})` : "No RSVPs yet", html: true, cancel: null, ok: "Close",
        body: r.names.length ? `<ul class="plain-list">${r.names.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "No one has RSVP'd to this talk yet." });
    }
  });

  async function loadAnnouncements() {
    const anns = await api("/api/announcements");
    $("#ann-list").innerHTML = anns.map((a) => `<li><span>${esc(a.message)}</span><button class="btn small ghost" data-ann="${a.id}">Remove</button></li>`).join("") || `<li class="muted">No active announcements.</li>`;
  }
  $("#ann-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/admin/announcements", { method: "POST", body: { message: $("#ann-text").value } });
    $("#ann-text").value = ""; loadAnnouncements(); loadNotices();
  });
  $("#ann-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-ann]"); if (!b) return;
    await api(`/api/admin/announcements/${b.dataset.ann}`, { method: "DELETE" }); loadAnnouncements(); loadNotices();
  });

  async function loadUsage() {
    const u = await api("/api/admin/usage");
    const t = u.totals, pct = Math.min(100, (u.tokens_today / u.daily_budget) * 100);
    const perSession = t.sessions ? t.cost_usd / t.sessions : 0;
    const cacheShare = t.input_tokens ? (t.cache_read_tokens / t.input_tokens) * 100 : 0;
    $("#usage").innerHTML = `
      <div class="stats">
        <div class="stat"><div class="v">${fmt(t.sessions)}</div><div class="k">intern sessions</div></div>
        <div class="stat"><div class="v">${fmt(t.runs)}</div><div class="k">agent runs</div></div>
        <div class="stat"><div class="v">${money(t.cost_usd)}</div><div class="k">total AI cost</div></div>
        <div class="stat"><div class="v">${money(perSession)}</div><div class="k">avg cost / session</div></div>
        <div class="stat"><div class="v">${cacheShare.toFixed(0)}%</div><div class="k">input served from cache</div></div>
        <div class="stat"><div class="v">${fmt(u.tokens_today)}</div><div class="k">tokens today of ${fmt(u.daily_budget)} budget</div><div class="meter"><i style="width:${pct}%"></i></div></div>
      </div>
      <h3 class="sub">By agent</h3>
      <div class="table-wrap"><table class="table"><thead><tr><th>Agent</th><th>Model</th><th class="num">Runs</th><th class="num">Input</th><th class="num">Cache write</th><th class="num">Cache read</th><th class="num">Output</th><th class="num">Cost</th><th class="num">Avg latency</th></tr></thead><tbody>
      ${u.by_agent.map((a) => `<tr><td>${esc(a.agent)}</td><td>${esc(a.model)}</td><td class="num">${fmt(a.runs)}</td><td class="num">${fmt(a.input_tokens)}</td><td class="num">${fmt(a.cache_write_tokens)}</td><td class="num">${fmt(a.cache_read_tokens)}</td><td class="num">${fmt(a.output_tokens)}</td><td class="num">${money(a.cost_usd)}</td><td class="num">${(a.avg_latency_ms / 1000).toFixed(1)}s</td></tr>`).join("") || `<tr><td colspan="9" class="muted">No runs yet.</td></tr>`}
      </tbody></table></div>
      <h3 class="sub">Recent runs</h3>
      <div class="table-wrap"><table class="table"><thead><tr><th>Time</th><th>Agent</th><th class="num">In</th><th class="num">Cached</th><th class="num">Out</th><th class="num">Cost</th><th class="num">Latency</th></tr></thead><tbody>
      ${u.recent.map((r) => `<tr><td>${esc(r.ts.replace("T", " "))}</td><td>${esc(r.agent)}</td><td class="num">${fmt(r.input_tokens)}</td><td class="num">${fmt(r.cache_read_tokens)}</td><td class="num">${fmt(r.output_tokens)}</td><td class="num">${money(r.cost_usd)}</td><td class="num">${(r.latency_ms / 1000).toFixed(1)}s</td></tr>`).join("") || `<tr><td colspan="7" class="muted">No runs yet.</td></tr>`}
      </tbody></table></div>
      <h3 class="sub">Safety log</h3>
      <div class="table-wrap"><table class="table"><thead><tr><th>Time</th><th>Event</th><th>Detail</th></tr></thead><tbody>
      ${u.safety.map((s) => `<tr><td>${esc(s.ts.replace("T", " "))}</td><td><span class="badge">${esc(s.kind)}</span></td><td>${esc(s.detail)}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">Nothing blocked yet.</td></tr>`}
      </tbody></table></div>`;
  }
  $("#usage-refresh").addEventListener("click", loadUsage);

  // ---------- sign-in gate ----------
  function showGate() {
    $("#gate").hidden = false; $("#app-nav").hidden = true; $("#app-main").hidden = true; $("#notices").innerHTML = "";
    $("#admin-not-configured").hidden = META.admin_configured !== false;
  }
  function gatePane(name) {
    $$(".gate-seg button").forEach((b) => b.classList.toggle("on", b.dataset.gate === name));
    $$("[data-pane]").forEach((f) => (f.hidden = f.dataset.pane !== name));
    $("#gate-error").hidden = true; $("#gate-msg").hidden = true;
  }
  $(".gate-seg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) gatePane(b.dataset.gate); });
  document.addEventListener("click", (e) => { const a = e.target.closest("[data-goto]"); if (a) { e.preventDefault(); gatePane(a.dataset.goto); } });
  function gateError(msg) { $("#gate-msg").hidden = true; $("#gate-error").textContent = msg; $("#gate-error").hidden = false; }
  function gateMsg(msg) { $("#gate-error").hidden = true; $("#gate-msg").textContent = msg; $("#gate-msg").hidden = false; }
  function fillStates() {
    const opts = (META.us_states || []).map((s) => `<option>${esc(s)}</option>`).join("");
    $("#rg-state").insertAdjacentHTML("beforeend", opts);
    $("#me-state").innerHTML = opts;
  }

  $("#login-form-intern").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { email: $("#li-email").value, password: $("#li-pw").value } });
      if (r.must_change_password) { setTempMode(false); showForceChange(r.user && r.user.slack_email); return; }
      location.hash = "#match"; location.reload();
    } catch (err) { gateError(err.message); }
  });
  $("#admin-login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/admin/login", { method: "POST", body: { username: $("#ad-user").value, password: $("#ad-pw").value } }); location.hash = "#admin"; location.reload(); }
    catch (err) { gateError(err.message); }
  });
  $("#register-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!rulesPass("rg-pw")) { gateError(RULES_MSG); return; }
    const body = {
      access_code: $("#rg-code").value.trim(), first_name: $("#rg-first").value, last_name: $("#rg-last").value,
      phone: $("#rg-phone").value, slack_email: $("#rg-email").value, city: $("#rg-city").value, state: $("#rg-state").value,
      school: $("#rg-school").value, interests: $("#rg-interests").value, hobbies: $("#rg-hobbies").value,
      goals: $("#rg-goals").value, fun_fact: $("#rg-fun").value, linkedin_url: $("#rg-linkedin").value,
      committee_prefs: PREFS.rg, availability: $("#rg-avail").value,
      share_with_cohort: $("#rg-share").checked, password: $("#rg-pw").value,
    };
    try { await api("/api/auth/register", { method: "POST", body }); }
    catch (err) { gateError(err.message); return; }
    const photo = $("#rg-photo").files[0];
    if (photo) {
      try { await uploadPhoto(photo); }
      catch (err) { alertLater(`Your account was created, but the photo didn't upload: ${err.message} You can add it from Cohort → Edit my profile.`); }
    }
    location.hash = "#match"; location.reload();
  });
  // ---------- password fields: show toggle + requirement checklist ----------
  document.addEventListener("change", (e) => {
    const cb = e.target.closest("[data-show]");
    if (!cb) return;
    cb.dataset.show.split(",").forEach((id) => { const f = document.getElementById(id); if (f) f.type = cb.checked ? "text" : "password"; });
  });

  let RESET_EMAIL = "", FORCE_EMAIL = "";
  const RULE_EMAIL = { "rg-pw": () => $("#rg-email").value, "rs-pw": () => RESET_EMAIL, "fc-pw": () => FORCE_EMAIL, "pw-new": () => (ME && ME.slack_email) || "" };

  // Each checklist re-evaluates as the person types. While typing, unmet rules stay neutral;
  // after a submit attempt they turn red, so nobody is scolded mid-word.
  function checkRules(ul, strict = false) {
    const pw = document.getElementById(ul.dataset.pw).value;
    const confirm = document.getElementById(ul.dataset.confirm).value;
    const email = (RULE_EMAIL[ul.dataset.pw] || (() => ""))().trim().toLowerCase();
    const tests = {
      len: pw.length >= 12,
      email: !(email && pw.toLowerCase() === email),
      match: confirm.length > 0 && pw === confirm,
      notcurrent: ul.dataset.pw !== "pw-new" || !pw || pw !== $("#pw-current").value,
    };
    let allOk = true;
    $$("li[data-rule]", ul).forEach((li) => {
      const rule = li.dataset.rule;
      if (!(rule in tests)) return;          // "old" is checked by the server
      const ok = tests[rule] && (rule !== "email" || pw.length > 0) && (rule !== "notcurrent" || pw.length > 0);
      if (!ok) allOk = false;
      li.classList.toggle("ok", ok);
      li.classList.toggle("bad", !ok && strict);
    });
    return allOk;
  }
  $$(".pw-rules").forEach((ul) => {
    const ids = [ul.dataset.pw, ul.dataset.confirm, "rg-email", "pw-current"];
    ids.forEach((id) => document.getElementById(id)?.addEventListener("input", () => checkRules(ul)));
  });
  function rulesPass(pwId) {
    const ul = document.querySelector(`.pw-rules[data-pw="${pwId}"]`);
    return ul ? checkRules(ul, true) : true;
  }
  function resetRules(pwId) {
    const ul = document.querySelector(`.pw-rules[data-pw="${pwId}"]`);
    if (ul) $$("li", ul).forEach((li) => li.classList.remove("ok", "bad"));
  }
  const RULES_MSG = "Your new password doesn't meet the requirements yet. Check the list under it.";

  // ---------- temporary-password sign-in mode ----------
  function setTempMode(on) {
    const pw = $("#li-pw");
    $("#temp-banner").hidden = !on; $("#temp-cancel").hidden = !on;
    $("#li-pw-label").textContent = on ? "Temporary password (from your email)" : "Password";
    pw.placeholder = on ? "e.g. Ab3d-Ef7h-Jk9m-Np2q" : "";
    pw.autocomplete = on ? "one-time-code" : "current-password";
    pw.classList.toggle("temp-mode", on);
    $("#li-submit").textContent = on ? "Sign in with temporary password" : "Sign in";
    pw.value = "";
  }
  $("#temp-cancel-link").addEventListener("click", (e) => { e.preventDefault(); setTempMode(false); $("#gate-msg").hidden = true; $("#li-pw").focus(); });
  function setForgotCopy() {
    const on = !!META.email_enabled;
    $("#fg-help").textContent = on
      ? "Enter the email you registered with. We'll email you a temporary password that works once. After you sign in with it, you'll choose a new password."
      : "Enter the email you registered with. The Program Lead will send you a one-time reset link on Slack.";
    $("#fg-submit").textContent = on ? "Email me a temporary password" : "Request a reset link";
  }

  // ---------- forgot / reset password ----------
  $("#forgot-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = $("#fg-email").value.trim();
    try {
      const r = await api("/api/auth/forgot", { method: "POST", body: { email } });
      gatePane("login"); $("#li-email").value = email;
      if (r.email_enabled) {
        setTempMode(true); $("#li-pw").focus();
        gateMsg("If that email has an account, a temporary password is on its way (check spam too).");
      } else {
        gateMsg("Request sent. If that email has an account, the Program Lead will send you a one-time reset link on Slack. It expires 24 hours after it's created.");
      }
    } catch (err) { gateError(err.message); }
  });

  let RESET_TOKEN = "";
  async function startReset(token) {
    RESET_TOKEN = token;
    history.replaceState(null, "", location.pathname);  // keep the token out of history
    showGate(); gatePane("reset");
    try {
      const r = await api(`/api/auth/reset/${encodeURIComponent(token)}`);
      $("#reset-title").textContent = r.first_name ? `Hi ${r.first_name}, choose a new password` : "Choose a new password";
      RESET_EMAIL = r.email || "";
    } catch (err) { gatePane("forgot"); gateError(err.message); }
  }
  $("#reset-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!rulesPass("rs-pw")) { gateError(RULES_MSG); return; }
    try {
      const r = await api("/api/auth/reset", { method: "POST", body: { token: RESET_TOKEN, password: $("#rs-pw").value } });
      RESET_TOKEN = ""; $("#reset-form").reset();
      gatePane("login"); $("#li-email").value = r.email; $("#li-pw").focus();
      gateMsg("Password updated. Sign in with your new password.");
    } catch (err) { gateError(err.message); }
  });

  function showForceChange(email) {
    if (email) FORCE_EMAIL = email;
    showGate(); gatePane("force"); resetRules("fc-pw"); $("#fc-pw").focus();
  }
  $("#force-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!rulesPass("fc-pw")) { gateError(RULES_MSG); return; }
    try {
      await api("/api/me/new-password", { method: "POST", body: { new_password: $("#fc-pw").value } });
      alertLater("Password updated. You're all set.");
      location.hash = "#match"; location.reload();
    } catch (err) { gateError(err.message); }
  });
  $("#force-signout").addEventListener("click", async (e) => {
    e.preventDefault(); await api("/api/auth/logout", { method: "POST" }); location.hash = ""; location.reload();
  });

  $("#signout-btn").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); location.hash = ""; location.reload(); });

  // ---------- photos ----------
  function alertLater(msg) { try { sessionStorage.setItem("css_flash", msg); } catch { /* ignore */ } }
  function previewPhoto(input, img, ph) {
    input.addEventListener("change", () => {
      const f = input.files[0];
      if (!f) return;
      if (f.size > 5e6) { toast("That photo is over 5 MB."); input.value = ""; return; }
      img.src = URL.createObjectURL(f); img.hidden = false; ph.hidden = true;
    });
  }
  previewPhoto($("#rg-photo"), $("#rg-photo-preview"), $("#rg-photo-ph"));
  previewPhoto($("#me-photo"), $("#me-photo-preview"), $("#me-photo-ph"));
  async function uploadPhoto(file) {
    const fd = new FormData(); fd.append("file", file);
    return (await api("/api/me/photo", { method: "POST", body: fd })).user;
  }

  // ---------- cohort ----------
  let COHORT = [];
  const initAvatar = (u, cls = "") => u.photo_url
    ? `<img class="avatar ${cls}" src="${esc(u.photo_url)}" alt="${esc(u.first_name)} ${esc(u.last_name)}" loading="lazy">`
    : `<span class="avatar init ${cls}">${esc(((u.first_name || "")[0] || "") + ((u.last_name || "")[0] || ""))}</span>`;
  const splitList = (t) => (t || "").split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);
  async function loadCohort() {
    COHORT = await api("/api/cohort");
    renderCohort();
  }
  function renderCohort() {
    const q = $("#cohort-q").value.trim().toLowerCase();
    const list = COHORT.filter((u) => !q || Object.values(u).join(" ").toLowerCase().includes(q));
    $("#cohort-grid").innerHTML = list.map((u) => `<div class="person intern">
      <div class="top">${initAvatar(u)}<div><div class="name">${esc(u.first_name)} ${esc(u.last_name)}${ME && u.id === ME.id ? ' <span class="badge">You</span>' : ""}</div>
        <div class="loc">${esc(u.city)}, ${esc(u.state)}${u.school ? " · " + esc(u.school) : ""}</div></div></div>
      ${splitList(u.interests).length ? `<div><div class="lbl small muted">Interests</div><div class="chips">${splitList(u.interests).slice(0, 6).map((x) => `<span class="chip accent">${esc(x)}</span>`).join("")}</div></div>` : ""}
      ${splitList(u.hobbies).length ? `<div><div class="lbl small muted">Hobbies</div><div class="chips">${splitList(u.hobbies).slice(0, 6).map((x) => `<span class="chip gold">${esc(x)}</span>`).join("")}</div></div>` : ""}
      ${u.goals ? `<p class="goal"><strong>Wants to:</strong> ${esc(u.goals)}</p>` : ""}
      ${u.fun_fact ? `<p class="fun">✨ ${esc(u.fun_fact)}</p>` : ""}
      <div class="links"><a href="mailto:${esc(u.slack_email)}">✉ ${esc(u.slack_email)}</a>${u.linkedin_url ? `<a href="${esc(u.linkedin_url)}" target="_blank" rel="noopener">LinkedIn ↗</a>` : ""}</div>
    </div>`).join("") || `<div class="empty">${COHORT.length ? "No one matches that search." : "No interns have shared a profile yet."}</div>`;
  }
  $("#cohort-q").addEventListener("input", renderCohort);

  const meDialog = $("#me-dialog");
  $("#edit-me-btn").addEventListener("click", () => {
    if (!ME) { toast("The admin account has no intern profile."); return; }
    const m = { first: "first_name", last: "last_name", phone: "phone", email: "slack_email", city: "city", state: "state", school: "school", interests: "interests", hobbies: "hobbies", goals: "goals", fun: "fun_fact", linkedin: "linkedin_url" };
    Object.entries(m).forEach(([id, k]) => ($("#me-" + id).value = ME[k] || ""));
    PREFS.me = [...(ME.committee_prefs || [])]; renderPrefs("me"); $("#me-avail").value = ME.availability || "";
    $("#me-photo").value = "";
    $("#me-photo-preview").hidden = !ME.photo_url; $("#me-photo-ph").hidden = !!ME.photo_url;
    if (ME.photo_url) $("#me-photo-preview").src = ME.photo_url;
    $("#me-photo-remove").hidden = !ME.photo_url;
    $("#me-share").checked = ME.share_with_cohort; $("#me-error").hidden = true;
    meDialog.showModal();
  });
  $("#me-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { first_name: $("#me-first").value, last_name: $("#me-last").value, phone: $("#me-phone").value, slack_email: $("#me-email").value,
      city: $("#me-city").value, state: $("#me-state").value, school: $("#me-school").value, interests: $("#me-interests").value,
      hobbies: $("#me-hobbies").value, goals: $("#me-goals").value, fun_fact: $("#me-fun").value,
      linkedin_url: $("#me-linkedin").value, share_with_cohort: $("#me-share").checked,
      committee_prefs: PREFS.me, availability: $("#me-avail").value };
    try {
      ME = (await api("/api/me", { method: "PUT", body })).user;
      if ($("#me-photo").files[0]) ME = await uploadPhoto($("#me-photo").files[0]);
      meDialog.close(); toast("Profile saved"); $("#who-name").textContent = `${ME.first_name} ${ME.last_name}`; loadCohort(); }
    catch (err) { $("#me-error").textContent = err.message; $("#me-error").hidden = false; }
  });
  $("#pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#pw-error"); err.hidden = true;
    if (!rulesPass("pw-new")) { err.textContent = RULES_MSG; err.hidden = false; return; }
    try {
      await api("/api/me/password", { method: "POST", body: { current_password: $("#pw-current").value, new_password: $("#pw-new").value } });
      $("#pw-form").reset(); resetRules("pw-new"); toast("Password changed. Other devices have been signed out.");
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  });

  $("#me-photo-remove").addEventListener("click", async () => {
    await api("/api/me/photo", { method: "DELETE" });
    ME = (await api("/api/me")).user; $("#me-photo-preview").hidden = true; $("#me-photo-ph").hidden = false;
    $("#me-photo-remove").hidden = true; toast("Photo removed"); loadCohort();
  });
  $$("#me-dialog .dialog-close, #intern-dialog .dialog-close").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

  // ---------- admin: interns + access code ----------
  let INTERNS = [];
  async function loadInterns() { INTERNS = await api("/api/admin/interns"); renderInterns(); }
  function renderInterns() {
    const q = $("#intern-q").value.trim().toLowerCase();
    const list = INTERNS.filter((u) => !q || Object.values(u).join(" ").toLowerCase().includes(q));
    $("#intern-count").textContent = `· ${INTERNS.length} registered`;
    $("#intern-table").innerHTML = `<thead><tr><th></th><th>Name</th><th>Location</th><th>Email</th><th>Phone</th><th>Interests</th><th>Joined</th><th></th></tr></thead><tbody>${
      list.map((u) => `<tr class="clickable" data-intern="${u.id}">
        <td>${initAvatar(u, "sm")}</td>
        <td><strong>${esc(u.first_name)} ${esc(u.last_name)}</strong>${u.is_sample ? ' <span class="badge sample">Sample</span>' : ""}${u.school ? `<br><span class="muted small">${esc(u.school)}</span>` : ""}</td>
        <td>${esc(u.city)}, ${esc(u.state)}</td><td>${esc(u.slack_email)}</td><td style="white-space:nowrap">${esc(u.phone)}</td>
        <td>${esc(u.interests.slice(0, 90))}${u.interests.length > 90 ? "…" : ""}</td>
        <td style="white-space:nowrap">${esc((u.created_at || "").slice(0, 10))}</td>
        <td><button class="btn small" type="button" data-view="${u.id}">View</button></td></tr>`).join("") || `<tr><td colspan="8" class="muted">${INTERNS.length ? "No match." : "No interns have registered yet. Share the access code above."}</td></tr>`}</tbody>`;
  }
  $("#intern-q").addEventListener("input", renderInterns);
  $("#intern-table").addEventListener("click", (e) => {
    const row = e.target.closest("[data-intern]"); if (!row) return;
    const u = INTERNS.find((x) => x.id == row.dataset.intern);
    const row_ = (k, v) => `<dt>${k}</dt><dd>${esc(v || "—")}</dd>`;
    $("#intern-body").innerHTML = `<div class="intern-detail">
      <div class="pd-head">${u.photo_url ? `<img class="photo-lg" src="${esc(u.photo_url)}" alt="">` : initAvatar(u, "lg")}<div><h2>${esc(u.first_name)} ${esc(u.last_name)}</h2><div class="muted">${esc(u.city)}, ${esc(u.state)}</div></div></div>
      <dl>${row_("Cell phone", u.phone)}${row_("Preferred email", u.slack_email)}${row_("School / employer", u.school)}
      <dt>LinkedIn</dt><dd>${u.linkedin_url ? `<a href="${esc(u.linkedin_url)}" target="_blank" rel="noopener">${esc(u.linkedin_url.replace(/^https:\/\/(www\.)?/, ""))}</a>` : "—"}</dd>
      ${row_("Interests", u.interests)}${row_("Hobbies", u.hobbies)}${row_("Program goals", u.goals)}${row_("Fun fact", u.fun_fact)}${row_("Committee picks", (u.committee_prefs || []).map((sl, i) => `${i + 1}. ${(COMMITTEES_PUBLIC.find((c) => c.slug === sl) || {}).name || sl}`).join("\n"))}${row_("Availability", u.availability)}
      <dt>Resume</dt><dd>${u.resume_uploaded_at ? `${esc(u.resume_filename || "Resume")} · ${esc(u.resume_uploaded_at.slice(0, 10))} <button class="btn small ghost" type="button" data-view-resume="${u.id}">View resume</button>` : "—"}</dd>
      ${row_("Shares with cohort", u.share_with_cohort ? "Yes" : "No")}${row_("Registered", (u.created_at || "").replace("T", " "))}${row_("Last sign-in", (u.last_login || "").replace("T", " "))}</dl>
      <div class="event-actions"><a class="btn" href="tel:${esc(u.phone)}">Call</a><a class="btn" href="mailto:${esc(u.slack_email)}">Email</a>
      <button class="btn" type="button" data-reset-link="${u.id}">Create password reset link</button>
      <button class="btn danger" type="button" id="del-intern">Delete account</button></div></div>`;
    $("#del-intern").onclick = async () => {
      if (!await modal({ title: `Delete ${u.first_name} ${u.last_name}'s account?`, body: "This removes their profile and sign-in. It can't be undone.", ok: "Delete account", danger: true })) return;
      await api(`/api/admin/interns/${u.id}`, { method: "DELETE" }); $("#intern-dialog").close(); toast("Account deleted"); loadInterns();
    };
    $("#intern-dialog").showModal();
  });
  async function loadResetRequests() {
    const r = await api("/api/admin/reset-requests");
    const box = $("#reset-requests");
    box.hidden = !r.requests.length;
    box.innerHTML = `<b>🔑 Password reset requests (${r.requests.length})</b> <span class="muted small">Create a one-time link and send it to them on Slack.</span>
      <ul>${r.requests.map((q) => `<li><span><strong>${esc(q.first_name)} ${esc(q.last_name)}</strong> · ${esc(q.slack_email)} · <span class="muted">${esc(q.created_at.replace("T", " ").slice(0, 16))}</span></span>
        <button class="btn small primary" type="button" data-reset-link="${q.user_id}">Create reset link</button></li>`).join("")}</ul>`;
  }
  async function createResetLink(userId) {
    const r = await api(`/api/admin/interns/${userId}/reset-link`, { method: "POST" });
    loadResetRequests();
    const p = modal({ title: `Reset link for ${r.name}`, cancel: null, ok: "Done", html: true,
      body: `<p>Send this to <strong>${esc(r.email)}</strong> on Slack. It works once, expires in ${r.hours} hours, and any earlier link for them stops working.</p>
        <div class="link-box"><input id="reset-link-text" readonly value="${esc(r.link)}"><button class="btn small" type="button" id="copy-reset-link">Copy</button></div>` });
    $("#copy-reset-link").onclick = async () => {
      try { await navigator.clipboard.writeText(r.link); toast("Reset link copied"); }
      catch { $("#reset-link-text").select(); toast("Press Cmd+C to copy"); }
    };
    await p;
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-reset-link]");
    if (b) { e.preventDefault(); createResetLink(b.dataset.resetLink).catch((err) => toast(err.message)); }
  });

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-view-resume]"); if (!b) return;
    const r = await api(`/api/admin/interns/${b.dataset.viewResume}/resume`);
    const p = r.profile || {};
    const chips = (label, items) => (items && items.length ? `<div class="group"><b>${label}</b><div class="chips">${items.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div></div>` : "");
    await modal({ title: `${r.name}: resume`, cancel: null, ok: "Close", html: true, body: `
      <p class="muted small">${esc(r.resume ? r.resume.filename : "")} · uploaded ${esc(r.resume ? r.resume.uploaded_at.replace("T", " ").slice(0, 16) : "")}. Lines that looked like instructions to an AI were removed before saving.</p>
      ${p.summary ? `<div class="profile-view"><p class="small">${esc(p.summary)}</p>${chips("Skills", p.skills)}${chips("Interests", p.interests)}${chips("Background", p.background)}</div>` : ""}
      <pre class="resume-text">${esc(r.text || "(no text)")}</pre>` });
  });

  async function loadAccessCode() { $("#access-code").textContent = (await api("/api/admin/access-code")).code; }
  $("#copy-code").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("#access-code").textContent); toast("Access code copied"); } catch { toast("Couldn't copy. Select the code instead."); } });
  $("#rotate-code").addEventListener("click", async () => {
    if (!await modal({ title: "Make a new access code?", body: "The current code stops working for new registrations. Interns who already have accounts aren't affected.", ok: "Rotate code" })) return;
    $("#access-code").textContent = (await api("/api/admin/access-code", { method: "POST" })).code; toast("New access code ready");
  });

  // ---------- committee preferences (registration + profile) ----------
  let COMMITTEES_PUBLIC = [];
  const PREFS = { rg: [], me: [] };
  function renderPrefs(which) {
    const box = $(`#${which}-prefs`);
    if (!box) return;
    box.innerHTML = COMMITTEES_PUBLIC.map((c) => {
      const rank = PREFS[which].indexOf(c.slug);
      return `<label title="${esc(c.description)}"><input type="checkbox" value="${esc(c.slug)}" ${rank >= 0 ? "checked" : ""}>
        ${rank >= 0 ? `<span class="rank">${rank + 1}</span>` : ""}${esc(c.name)}</label>`;
    }).join("");
  }
  ["rg", "me"].forEach((which) => {
    $(`#${which}-prefs`)?.addEventListener("change", (e) => {
      const cb = e.target; if (cb.type !== "checkbox") return;
      const list = PREFS[which];
      if (cb.checked) {
        if (list.length >= 3) { cb.checked = false; toast("Pick up to 3 committees."); return; }
        list.push(cb.value);
      } else { PREFS[which] = list.filter((s) => s !== cb.value); }
      renderPrefs(which);
    });
  });
  async function loadPublicCommittees() {
    try { COMMITTEES_PUBLIC = await api("/api/committees"); } catch { COMMITTEES_PUBLIC = []; }
    renderPrefs("rg");
  }

  // ---------- admin sub-navigation ----------
  const ASEC_LOADED = {};
  function showAdminSection(name) {
    $$("#admin-nav button").forEach((b) => b.classList.toggle("on", b.dataset.asec === name));
    $$("[data-asec-panel]").forEach((p) => (p.hidden = p.dataset.asecPanel !== name));
    store.set("css_admin_sec", name);
    if (name === "placements") loadPlacements();
    if (name === "pulse") loadPulse();
    if (name === "usage") loadUsage();
  }
  $("#admin-nav").addEventListener("click", (e) => { const b = e.target.closest("button[data-asec]"); if (b) showAdminSection(b.dataset.asec); });

  // ---------- demo data ----------
  async function loadDemoStatus() {
    const s = await api("/api/admin/demo");
    $("#demo-status").textContent = s.sample_interns
      ? `${s.sample_interns} fictional interns and ${s.sample_messages} sample Slack messages loaded (marked "Sample").`
      : "Fictional sample interns, Slack activity, and meeting notes for trying the AI agents.";
    $("#demo-remove").hidden = !s.sample_interns;
  }
  $("#demo-load").addEventListener("click", async (e) => {
    e.target.classList.add("busy");
    try { const r = await api("/api/admin/demo", { method: "POST" }); toast(`Demo cohort ready (${r.interns_total} fictional interns).`); }
    finally { e.target.classList.remove("busy"); }
    loadDemoStatus(); loadInterns(); ASEC_LOADED.placements = ASEC_LOADED.pulse = false;
  });
  $("#demo-remove").addEventListener("click", async () => {
    if (!await modal({ title: "Remove demo data?", body: "Deletes the fictional interns, their placements, the sample Slack messages, and the sample meeting notes. Real interns and real data aren't touched.", ok: "Remove demo data", danger: true })) return;
    await api("/api/admin/demo", { method: "DELETE" }); toast("Demo data removed"); loadDemoStatus(); loadInterns();
  });

  // ---------- Placements: Committee & Cyber Coach Matchmaker ----------
  let ADMIN_COMMITTEES = [], ADMIN_COACHES = [], MATCH = null;
  const scoreCls = (n) => (n >= 75 ? "" : n >= 50 ? "mid" : "low");
  async function loadPlacements() {
    [ADMIN_COMMITTEES, ADMIN_COACHES, MATCH] = await Promise.all([api("/api/admin/committees"), api("/api/admin/coaches"), api("/api/admin/placements")]);
    renderCommittees(); renderCoaches(); renderMatrix();
  }
  function renderMatrix() {
    const m = MATCH;
    if (!m || !m.rows.length) {
      $("#match-table").innerHTML = `<tbody><tr><td class="muted">No matches yet. Set committee capacities and meeting times below, then press <b>Run matchmaker</b> (above). (No interns? Load the demo cohort on the Interns tab.)</td></tr></tbody>`;
      $("#match-meta").textContent = ""; $("#match-cost").textContent = ""; $("#match-notes").textContent = ""; $("#match-unplaced").innerHTML = "";
      return;
    }
    const t = m.usage.total;
    $("#match-meta").textContent = `Last run ${m.created_at.replace("T", " ").slice(0, 16)}`;
    $("#match-cost").textContent = `${t.calls} model calls · ${fmt(t.input_tokens)} in / ${fmt(t.output_tokens)} out · ${money(t.cost_usd)} · ${m.usage.cached_profiles} profiles from cache`;
    $("#match-notes").textContent = m.notes || "";
    const committeeOpts = (sel) => ADMIN_COMMITTEES.filter((c) => c.active).map((c) => `<option value="${esc(c.slug)}" ${c.slug === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    const coachOpts = (sel) => ADMIN_COACHES.filter((c) => c.active || c.slug === sel).map((c) => `<option value="${esc(c.slug)}" ${c.slug === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    $("#match-table").innerHTML = `<thead><tr><th>Intern</th><th>Committee</th><th>Cyber Coach</th><th>Why this match works</th><th>Status</th><th></th></tr></thead><tbody>${
      m.rows.map((r) => `<tr data-uid="${r.intern_id}">
        <td><strong>${esc(r.name)}</strong>${r.is_sample ? ' <span class="badge sample">Sample</span>' : ""}<br><span class="muted small">${esc(r.school)}</span></td>
        <td>${(() => { const fit = r.ranked.find((x) => x.committee === r.final_committee); return fit ? `<span class="score ${scoreCls(fit.score)}" title="Fit score for this committee">${fit.score}</span>` : '<span class="score low" title="Not in the AI\'s top 3">—</span>'; })()} <select data-field="committee">${committeeOpts(r.final_committee)}</select>
          <div class="alts">Top fits: ${r.ranked.map((x) => `${esc(x.name)} (${x.score})`).join(" · ")}</div>
          ${r.flags.map((f) => `<div class="flag-line">⚑ ${esc(f)}</div>`).join("")}</td>
        <td><select data-field="coach">${coachOpts(r.final_coach)}</select><div class="alts">${esc(r.coach_why)}</div></td>
        <td class="why">${esc(r.why_this_match)}${r.overridden ? '<div class="alts">Edited by you after the AI run</div>' : ""}</td>
        <td><span class="status-pill ${r.status === "approved" ? "approved" : ""}">${r.status === "approved" ? "Approved" : "Proposed"}</span></td>
        <td style="white-space:nowrap">${r.status === "approved"
          ? `<button class="btn small" data-act="drafts">${r.has_drafts ? "View intros" : "Draft intros"}</button> <button class="btn small ghost" data-act="unapprove">Undo</button>`
          : `<button class="btn small primary" data-act="approve">Approve</button>`}</td>
      </tr>`).join("")}</tbody>`;
    $("#match-unplaced").innerHTML = m.unplaced.length ? `<h3 class="sub">Not placed</h3><ul class="plain-list">${m.unplaced.map((u) => `<li><strong>${esc(u.name)}</strong><span class="muted">${esc(u.reason)}</span></li>`).join("")}</ul>` : "";
  }
  $("#match-table").addEventListener("change", async (e) => {
    const sel = e.target.closest("select[data-field]"); if (!sel) return;
    const uid = sel.closest("tr").dataset.uid;
    await api(`/api/admin/placements/${uid}`, { method: "PUT", body: { [sel.dataset.field]: sel.value } });
    MATCH = await api("/api/admin/placements"); renderMatrix(); toast("Placement updated");
  });
  $("#match-table").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const uid = b.closest("tr").dataset.uid;
    if (b.dataset.act === "approve" || b.dataset.act === "unapprove") {
      await api(`/api/admin/placements/${uid}`, { method: "PUT", body: { status: b.dataset.act === "approve" ? "approved" : "proposed" } });
      MATCH = await api("/api/admin/placements"); renderMatrix();
    }
    if (b.dataset.act === "drafts") openDrafts(uid, b);
  });
  $("#run-match").addEventListener("click", async (e) => {
    const btn = e.currentTarget; btn.classList.add("busy");
    $("#match-meta").textContent = "Extracting profiles and matching… (20-60 s)";
    try { MATCH = await api("/api/admin/placements/run", { method: "POST" }); renderMatrix(); toast(`Matched ${MATCH.rows.length} interns for ${money(MATCH.usage.total.cost_usd)}`); loadUsageQuiet(); }
    catch (err) { $("#match-meta").textContent = ""; toast(err.message); }
    finally { btn.classList.remove("busy"); }
  });

  async function openDrafts(uid, btn) {
    let d = (await api(`/api/admin/placements/${uid}/drafts`)).drafts;
    if (!d) {
      btn.classList.add("busy");
      try { d = (await api(`/api/admin/placements/${uid}/drafts`, { method: "POST" })).drafts; MATCH = await api("/api/admin/placements"); renderMatrix(); }
      catch (err) { toast(err.message); return; }
      finally { btn.classList.remove("busy"); }
    }
    const block = (label, to, subject, body) => `<div class="draft-block"><div class="meta"><span><b>${label}</b>${to ? ` · to ${esc(to)}` : ""}</span>
      <span>${to && subject ? `<a class="btn small ghost" href="mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}">Open in email</a> ` : ""}<button class="btn small" type="button" data-copy="${esc((subject ? "Subject: " + subject + "\n\n" : "") + body)}">Copy</button></span></div>
      ${subject ? `<div><b>Subject:</b> ${esc(subject)}</div>` : ""}<pre>${esc(body)}</pre></div>`;
    $("#drafts-body").innerHTML = `<h2>Intro drafts · ${esc(d.committee)}</h2>
      <p class="muted small">Drafts only. Nothing has been sent. Review, edit in your email or Slack, and send yourself. Director: ${esc(d.director)}.</p>
      ${block("Email to intern", d.intern_email_to, d.intern_email.subject, d.intern_email.body)}
      ${block("Slack DM to intern", "", "", d.intern_slack_dm)}
      ${block("Email to committee director", d.director_email_to, d.director_email.subject, d.director_email.body)}
      ${block("Slack DM to committee director", "", "", d.director_slack_dm)}
      <div class="form-actions"><button class="btn ghost small" type="button" id="redraft">Re-draft with AI</button></div>`;
    $("#redraft").onclick = async (ev) => {
      ev.target.classList.add("busy");
      try { await api(`/api/admin/placements/${uid}/drafts`, { method: "POST" }); $("#drafts-dialog").close(); openDrafts(uid, ev.target); }
      catch (err) { toast(err.message); } finally { ev.target.classList.remove("busy"); }
    };
    $("#drafts-dialog").showModal();
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-copy]"); if (!b) return;
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Copied"); } catch { toast("Couldn't copy. Select the text instead."); }
  });
  $$("#drafts-dialog .dialog-close, #committee-dialog .dialog-close").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

  function renderCommittees() {
    $("#committee-table").innerHTML = `<thead><tr><th>Committee</th><th class="num">Seats</th><th>Director</th><th>Meets</th><th></th></tr></thead><tbody>${
      ADMIN_COMMITTEES.map((c) => `<tr style="${c.active ? "" : "opacity:.5"}"><td><strong>${esc(c.name)}</strong>${c.active ? "" : ' <span class="badge">Off</span>'}</td>
        <td class="num">${c.capacity}</td><td>${esc(c.director_display || "—")}</td>
        <td>${esc([c.meeting_day, c.meeting_time].filter(Boolean).join(" ") || "—")}</td>
        <td><button class="btn small ghost" data-edit-committee="${esc(c.slug)}">Edit</button></td></tr>`).join("")}</tbody>`;
  }
  $("#committee-table").addEventListener("click", (e) => {
    const b = e.target.closest("[data-edit-committee]"); if (!b) return;
    const c = ADMIN_COMMITTEES.find((x) => x.slug === b.dataset.editCommittee);
    const sel = $("#cm-director-slug");
    if (sel.options.length <= 1) sel.insertAdjacentHTML("beforeend", PEOPLE.filter((p) => p.section !== "Emeritus").map((p) => `<option value="${esc(p.slug)}">${esc(p.name)} · ${esc(p.org)}</option>`).join(""));
    $("#cm-slug").value = c.slug; $("#cm-title").textContent = `Edit ${c.name}`;
    $("#cm-name").value = c.name; $("#cm-desc").value = c.description; $("#cm-needs").value = c.needs;
    $("#cm-capacity").value = c.capacity; $("#cm-active").checked = c.active; sel.value = c.director_slug;
    $("#cm-director-name").value = c.director_name; $("#cm-director-email").value = c.director_email;
    $("#cm-day").value = c.meeting_day; $("#cm-time").value = c.meeting_time; $("#cm-note").value = c.meeting_note;
    $("#cm-error").hidden = true; $("#committee-dialog").showModal();
  });
  $("#committee-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { name: $("#cm-name").value, description: $("#cm-desc").value, needs: $("#cm-needs").value,
      capacity: Number($("#cm-capacity").value), active: $("#cm-active").checked, director_slug: $("#cm-director-slug").value,
      director_name: $("#cm-director-name").value, director_email: $("#cm-director-email").value,
      meeting_day: $("#cm-day").value, meeting_time: $("#cm-time").value, meeting_note: $("#cm-note").value };
    try { await api(`/api/admin/committees/${$("#cm-slug").value}`, { method: "PUT", body }); $("#committee-dialog").close(); toast("Committee saved"); loadPlacements(); }
    catch (err) { $("#cm-error").textContent = err.message; $("#cm-error").hidden = false; }
  });

  function renderCoaches() {
    const q = ($("#coach-q").value || "").toLowerCase();
    const active = ADMIN_COACHES.filter((c) => c.active);
    $("#coach-count").textContent = `${active.length} active · ${active.reduce((n, c) => n + c.capacity, 0)} intern slots`;
    $("#coach-list").innerHTML = ADMIN_COACHES.filter((c) => !q || (c.name + c.org + c.title).toLowerCase().includes(q)).map((c) => `<div class="coach-row" data-slug="${esc(c.slug)}">
      <input type="checkbox" ${c.active ? "checked" : ""} aria-label="Active coach">
      <span><strong>${esc(c.name)}</strong><br><small>${esc(c.title || "")}${c.title ? " · " : ""}${esc(c.org)}</small></span>
      <input type="number" min="0" max="10" value="${c.capacity}" aria-label="Interns this coach can take"></div>`).join("");
  }
  $("#coach-q").addEventListener("input", renderCoaches);
  $("#coach-list").addEventListener("change", async (e) => {
    const row = e.target.closest(".coach-row"); if (!row) return;
    const body = e.target.type === "checkbox" ? { active: e.target.checked } : { capacity: Number(e.target.value) };
    await api(`/api/admin/coaches/${row.dataset.slug}`, { method: "PUT", body });
    const c = ADMIN_COACHES.find((x) => x.slug === row.dataset.slug); Object.assign(c, body); renderCoaches();
  });

  // ---------- Cohort Pulse & Monday Brief ----------
  let BRIEF = null, SHOW_ALIAS = false;
  async function loadPulse() {
    const [src, briefs] = await Promise.all([api("/api/admin/pulse/sources"), api("/api/admin/pulse/briefs")]);
    const total = Object.values(src.messages_by_source).reduce((a, b) => a + b, 0);
    const label = { live: "Live Slack sync", export: "Uploaded Slack export", demo: "Demo data (fictional)" };
    $("#pulse-sources").innerHTML = `
      <div class="src"><span><b>Slack live connection</b></span><span>${src.slack_live ? "✓ Connected (bot token set)" : '<span class="muted">Not connected. Add SLACK_BOT_TOKEN to .env.</span>'}</span></div>
      ${Object.entries(src.messages_by_source).map(([k, v]) => `<div class="src"><span>${esc(label[k] || k)}</span><span>${fmt(v)} messages</span></div>`).join("") || '<div class="src"><span class="muted">No Slack messages yet</span><span></span></div>'}
      <div class="src"><span>Check-in channel</span><span>#${esc(src.checkin_channel)}</span></div>
      ${src.notes.length ? `<div><b class="small">Meeting notes</b><ul class="plain-list">${src.notes.map((n) => `<li><span>${esc(n.meeting_date)} · ${esc(n.committee || "—")} · ${esc(n.title || "Notes")}${n.source === "demo" ? ' <span class="badge sample">Sample</span>' : ""}</span><button class="btn small ghost" data-del-note="${n.id}">Remove</button></li>`).join("")}</ul></div>` : '<div class="src"><span class="muted">No meeting notes yet</span><span></span></div>'}`;
    $("#slack-sync").disabled = !src.slack_live;
    $("#run-brief").disabled = !total && !src.notes.length;
    $("#brief-list").innerHTML = briefs.briefs.map((b) => `<li><span>Week of ${esc(b.week_of)} <span class="muted small">· ${esc(b.created_at.replace("T", " ").slice(0, 16))}</span></span><button class="btn small ghost" data-brief="${b.id}">Open</button></li>`).join("") || '<li class="muted">No briefs yet.</li>';
    if (briefs.latest && !BRIEF) { BRIEF = briefs.latest; renderBrief(); }
  }
  $("#pulse-sources").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-del-note]"); if (!b) return;
    await api(`/api/admin/pulse/notes/${b.dataset.delNote}`, { method: "DELETE" }); loadPulse();
  });
  $("#brief-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-brief]"); if (!b) return;
    BRIEF = await api(`/api/admin/pulse/briefs/${b.dataset.brief}`); renderBrief(); $("#brief-card").scrollIntoView({ behavior: "smooth" });
  });
  $("#slack-export").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api("/api/admin/pulse/slack-export", { method: "POST", body: fd }); toast(`Imported ${fmt(r.messages)} messages from ${r.users} Slack members`); }
    catch (err) { toast(err.message); }
    e.target.value = ""; loadPulse();
  });
  $("#slack-sync").addEventListener("click", async (e) => {
    const btn = e.currentTarget; btn.classList.add("busy");
    try { const r = await api("/api/admin/pulse/slack-sync", { method: "POST" }); toast(`Synced ${fmt(r.messages)} messages from ${r.channels} channels`); }
    catch (err) { toast(err.message); } finally { btn.classList.remove("busy"); }
    loadPulse();
  });
  $("#notes-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("committee", $("#nt-committee").value); fd.append("meeting_date", $("#nt-date").value);
    fd.append("title", $("#nt-title").value); fd.append("text", $("#nt-text").value);
    if ($("#nt-file").files[0]) fd.append("file", $("#nt-file").files[0]);
    try { await api("/api/admin/pulse/notes", { method: "POST", body: fd }); $("#notes-form").reset(); $("#nt-error").hidden = true; toast("Notes added"); loadPulse(); }
    catch (err) { $("#nt-error").textContent = err.message; $("#nt-error").hidden = false; }
  });
  $("#run-brief").addEventListener("click", async (e) => {
    const btn = e.currentTarget; btn.classList.add("busy");
    $("#brief-meta").textContent = "Reading Slack and notes through the MCP server… (20-60 s)";
    try { BRIEF = await api("/api/admin/pulse/brief", { method: "POST" }); SHOW_ALIAS = false; renderBrief(); loadPulse(); toast(`Brief ready · ${money(BRIEF.usage.cost_usd)}`); loadUsageQuiet(); }
    catch (err) { toast(err.message); }
    finally { btn.classList.remove("busy"); $("#brief-meta").textContent = ""; }
  });
  $("#brief-toggle-alias").addEventListener("click", () => { SHOW_ALIAS = !SHOW_ALIAS; renderBrief(); });
  function renderBrief() {
    if (!BRIEF) return;
    const x = SHOW_ALIAS ? BRIEF.pseudonymized : BRIEF.brief, u = BRIEF.usage;
    $("#brief-card").hidden = false;
    $("#brief-title").textContent = `Program Lead's Monday Brief · week of ${BRIEF.week_of}`;
    $("#brief-toggle-alias").textContent = SHOW_ALIAS ? "Show real names" : "Show what the AI saw (pseudonyms)";
    $("#brief-download").href = `/api/admin/pulse/briefs/${BRIEF.id}/markdown`;
    const sec = (title, items, fn) => (items.length ? `<h3>${title}</h3>${items.map(fn).join("")}` : "");
    $("#brief-body").innerHTML = `<p class="brief-headline">${esc(x.headline)}</p>
      ${sec("(a) Interns who need a quick 1-on-1 nudge", x.nudges, (n) => `<div class="brief-item nudge"><span class="who">${esc(n.who)}</span>: ${esc(n.reason)}
        <ul>${n.evidence.map((ev) => `<li>${esc(ev)}</li>`).join("")}</ul>
        <div class="dm"><span><b>Suggested DM:</b> ${esc(n.suggested_dm)}</span><button class="btn small" type="button" data-copy="${esc(n.suggested_dm)}">Copy</button></div></div>`)}
      ${sec("(b) Wins to spotlight at all-hands", x.wins, (w) => `<div class="brief-item win"><span class="who">${esc(w.who)}</span>: ${esc(w.what)} <span class="muted small">(${esc(w.evidence)})</span></div>`)}
      ${sec("(c) Blockers to escalate", x.blockers, (b) => `<div class="brief-item blocker"><b>${esc(b.committee)}</b>: ${esc(b.blocker)} <span class="muted small">· waiting ${Math.round(b.waiting_days)} days${b.raised_by ? " · raised by " + esc(b.raised_by) : ""}</span><div class="small">→ Escalate to <b>${esc(b.escalate_to)}</b></div></div>`)}
      ${x.watch.length ? `<h3>Keep an eye on</h3><ul>${x.watch.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      ${x.safety_notes.length ? `<h3>🛡️ Safety notes</h3><div class="brief-safety">${x.safety_notes.map((s) => `<div>${esc(s)}</div>`).join("")}</div>` : ""}
      <div class="brief-foot">${esc(x.coverage)}<br>${u.tool_calls} MCP tool calls · ${fmt(u.input_tokens)} in (${fmt(u.cache_read_tokens)} cached) / ${fmt(u.output_tokens)} out · ${money(u.cost_usd)} · ${(u.latency_ms / 1000).toFixed(0)} s · ${esc(u.model)}</div>`;
  }
  async function loadUsageQuiet() { try { if (!$("[data-asec-panel=usage]").hidden) loadUsage(); } catch { /* ignore */ } }

  // ---------- boot ----------
  async function boot() {
    META = await api("/api/meta");
    fillStates();
    setForgotCopy();
    loadPublicCommittees();
    $("#footer-admin").textContent = `${META.admin_name}, ${META.admin_title}`;
    if (location.hash.startsWith("#reset=")) { await startReset(location.hash.slice(7)); return; }
    if (!META.signed_in) { showGate(); return; }
    ME = (await api("/api/me")).user;
    if (ME && ME.must_change_password) { showForceChange(ME.slack_email); return; }
    $("#gate").hidden = true; $("#app-nav").hidden = false; $("#app-main").hidden = false;
    $("#admin-tab").hidden = !META.is_admin;
    $("#who-name").textContent = ME ? `${ME.first_name} ${ME.last_name}` : `${META.admin_name} · Admin`;
    $("#people-count").textContent = META.people_count;
    $("#footer-admin").textContent = `${META.admin_name}, ${META.admin_title}`;
    $("#model-note").textContent = META.llm_ready ? `AI: ${META.match_model} + ${META.fast_model}` : "AI offline: no API key";
    META.sectors.forEach((s) => $("#dir-sector").insertAdjacentHTML("beforeend", `<option>${esc(s)}</option>`));
    PEOPLE = await api("/api/people");
    PEOPLE.forEach((p) => (PEOPLE_BY_SLUG[p.slug] = p));
    const st = await api("/api/chat/state");
    greet(st.resume);
    try { const f = sessionStorage.getItem("css_flash"); if (f) { sessionStorage.removeItem("css_flash"); setTimeout(() => toast(f), 600); } } catch { /* ignore */ }
    if (st.profile) renderProfile(st.profile, { resume: st.resume });
    showTab();
    loadNotices();
  }
  boot().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="notice">Couldn't load the app: ${esc(e.message)}</div>`); });
})();
