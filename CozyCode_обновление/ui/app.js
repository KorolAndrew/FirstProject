"use strict";
(() => {
  const KEY = document.querySelector('meta[name="cozy-key"]').content;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  const PHRASES = {
    idle: ["Привет, Яна! Чем займёмся? ✨", "Я тут, жду идей 🐑", "Бе-е! Напиши Клодику что-нибудь 💕", "Колокольчик готов звенеть 🔔", "Сделаем что-нибудь милое? ✨"],
    sent: ["Отнесла Клодику! 💌", "Позвала Клодика, он скоро прибежит ✨", "Дзынь-дзынь! Клодик, тебе письмо 🔔"],
    queued: ["Клодик ещё занят - передам сразу после ответа 💌"],
    thinking: ["Клодик думает… 🤔", "Тсс, Клодик работает ✨", "Он что-то пишет… я подглядываю 👀"],
    confirm: ["Клодик спрашивает разрешения! 🔔", "Яна, нужно твоё «да» или «нет» 💭"],
    done: ["Клодик закончил! ✨", "Готово! Смотри скорее 💕", "Дзынь! Ответ пришёл 🔔"],
    error: ["Ой… не получилось позвать Клодика 😿", "Что-то пошло не так. Загляни в настройки ⚙️"],
    setup: ["Заполни настройки Notion, чтобы я могла звать Клодика ⚙️"],
    offline: ["CozyCode не отвечает… 💤"],
  };
  const pick = (a) => a[Math.floor(Math.random() * a.length)];

  let S = null;               // последнее состояние
  let current = localStorage.getItem("cozy-project") || "general";
  let lastV = -1, lastEvent = null, msgsKey = "", mood = "", offline = false;
  let pendingFiles = [];
  const unread = JSON.parse(localStorage.getItem("cozy-unread") || "{}");

  async function api(path, body, raw) {
    const opt = {method: body !== undefined || raw ? "POST" : "GET", headers: {"X-Cozy-Key": KEY}};
    if (raw) { opt.body = raw; opt.headers["Content-Type"] = "application/octet-stream"; }
    else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    const r = await fetch(path, opt);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || ("Ошибка " + r.status));
    return data;
  }

  function toast(text, ms = 3200) {
    const t = $("toast"); t.textContent = text; t.classList.remove("hidden");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
  }

  // ---------- Яночка ----------
  function say(kind, text) {
    const b = $("bubble");
    const phrase = text || pick(PHRASES[kind] || PHRASES.idle);
    if (b.textContent === phrase) return;
    b.textContent = phrase; mood = kind;
    if (window.Yanochka) Yanochka.mood($("yana"), kind);
    b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop");
    if (["done", "confirm", "sent"].includes(kind)) { const y = $("yana"); y.classList.remove("hop"); void y.offsetWidth; y.classList.add("hop"); }
  }

  let audio;
  function ding() {
    if (!S || !S.settings.sound) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const t0 = audio.currentTime;
      [[1318.5, 0.0, 0.32], [1975.5, 0.0, 0.12], [1760, 0.16, 0.28], [2637, 0.16, 0.08]].forEach(([f, d, v]) => {
        const o = audio.createOscillator(), g = audio.createGain();
        o.type = "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t0 + d);
        g.gain.exponentialRampToValueAtTime(v, t0 + d + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + d + 1.4);
        o.connect(g).connect(audio.destination); o.start(t0 + d); o.stop(t0 + d + 1.5);
      });
    } catch (e) { /* без звука */ }
  }

  function notify(body) {
    if (!S || !S.settings.notifications) return;
    if (document.hasFocus() && !document.hidden) return;
    // 1.3.0: в своём окне уведомление показывает значок у часов
    if (window.pywebview && window.pywebview.api && window.pywebview.api.notify) { window.pywebview.api.notify(body); return; }
    if (!("Notification" in window)) return;
    if (Notification.permission === "granted") {
      const n = new Notification("Яночка", {body, icon: "/ui/img/avatar.png", silent: true});
      n.onclick = () => { window.focus(); n.close(); };
    }
  }

  // ---------- markdown (безопасный мини-вариант) ----------
  function inline(s) {
    s = esc(s);
    const codes = [];
    s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>").replace(/~~([^~]+)~~/g, "<s>$1</s>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => "<code>" + codes[+i] + "</code>");
  }
  function md(text) {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    let out = "", i = 0, para = [];
    const flush = () => { if (para.length) { out += "<p>" + para.map(inline).join("<br>") + "</p>"; para = []; } };
    while (i < lines.length) {
      const l = lines[i];
      const fence = l.match(/^\s*(`{3,}|~{3,})\s*([\w+-]*)\s*$/);
      if (fence) {
        flush(); const buf = []; i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) buf.push(lines[i++]);
        i++;
        out += '<pre><button class="copy">копировать</button><code>' + esc(buf.join("\n")) + "</code></pre>";
        continue;
      }
      const h = l.match(/^(#{1,4})\s+(.*)$/);
      if (h) { flush(); out += `<h3>${inline(h[2])}</h3>`; i++; continue; }
      if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
        flush(); const ordered = /^\s*\d/.test(l); let items = "";
        while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
          items += "<li>" + inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, "")) + "</li>"; i++;
        }
        out += ordered ? `<ol>${items}</ol>` : `<ul>${items}</ul>`; continue;
      }
      if (!l.trim()) { flush(); i++; continue; }
      para.push(l); i++;
    }
    flush();
    return out;
  }

  // ---------- отрисовка ----------
  const fmtTime = (iso) => (iso || "").slice(11, 16);

  function renderProjects() {
    const nav = $("projects"); nav.innerHTML = "";
    S.projects.forEach((p) => {
      const d = document.createElement("div");
      d.className = "proj" + (p.id === current ? " on" : "");
      let badge = "";
      if (S.working[p.id] !== undefined) badge = '<span class="badge">думает…</span>';
      else if (S.waiting[p.id] !== undefined) badge = '<span class="badge">ждём</span>';
      else if (unread[p.id]) badge = '<span class="badge new">новое</span>';
      d.innerHTML = `<span class="em">${esc(p.emoji || "✨")}</span><span class="nm">${esc(p.name)}</span>${badge}`;
      d.onclick = () => selectProject(p.id);
      nav.appendChild(d);
    });
  }

  function setDot(id, cls, text) { $("d-" + id).className = "dot " + cls; $("t-" + id).textContent = text; }

  function renderStatus() {
    if (!S.bridge.ok) setDot("bridge", "err", "ошибка");
    else if (S.bridge.on) setDot("bridge", "ok", "работает");
    else setDot("bridge", "", "выключен");
    const bb = $("btn-bridge"); bb.textContent = S.bridge.on ? "⏹" : "🔌";
    bb.title = S.bridge.on ? "Выключить мост и туннель (Клодик не сможет работать с ПК)" : "Включить мост и туннель для Клодика";
    const t = S.tunnel;
    if (t.state === "online") setDot("tunnel", t.mode === "ngrok" ? "ok" : "warn", t.mode === "ngrok" ? "ngrok" : "cloudflare");
    else if (t.state === "starting") setDot("tunnel", "busy", "запуск…");
    else if (t.state === "error") setDot("tunnel", "err", "ошибка");
    else setDot("tunnel", "", "выключен");
    const busy = Object.keys(S.working).length > 0, waiting = Object.keys(S.waiting).length > 0;
    if (busy) setDot("agent", "busy", "работает");
    else if (waiting) setDot("agent", "warn", "ждёт «.»");
    else setDot("agent", "ok", "ждёт сообщений");
    const u = S.usage; $("t-usage").textContent = `Запросов за месяц: ${u.requests.toLocaleString("ru")} / ${u.limit.toLocaleString("ru")}`;

    // баннер с важными проблемами
    let banner = "";
    if (!S.bridge.ok) banner = "⚠️ " + esc(S.bridge.error);
    else if (t.state === "error") banner = "⚠️ Туннель: " + esc(t.error);
    else if (t.mode === "cloudflare" && t.error) banner = "⚠️ " + esc(t.error) + " Адрес: <code>" + esc(S.mcp_url) + "</code>";
    const b = $("banner"); b.innerHTML = banner; b.classList.toggle("hidden", !banner);
  }

  function renderHeader() {
    const p = S.projects.find((x) => x.id === current) || S.projects[0];
    $("p-emoji").textContent = p.emoji || "✨"; $("p-name").textContent = p.name;
    $("p-folder").textContent = (p.folder === "." ? "вся рабочая папка" : p.folder) + (p.mode === "ro" ? " · 👀 только просмотр" : p.mode === "none" ? " · ⚠️ нет доступа к папке" : "");
    const w = S.working[current], q = S.waiting[current];
    const work = $("work");
    if (w !== undefined) { work.classList.remove("hidden"); $("work-text").textContent = S.status[current] || "Клодик работает…"; }
    else if (q !== undefined) { work.classList.remove("hidden"); $("work-text").textContent = "Яночка позвала Клодика, ждём…"; }
    else work.classList.add("hidden");
    $("btn-recall").classList.toggle("hidden", !((q !== undefined && q > 120) || (w !== undefined && w > 300)));
  }

  // 1.2.2: изменения в файлах - красное (убрано) и зелёное (добавлено)
  function diffHtml(text) {
    return String(text || "").split("\n").map((l) => {
      const cls = l.startsWith("+++") || l.startsWith("---") ? "dh" : l.startsWith("@@") ? "da" : l.startsWith("+") ? "dp" : l.startsWith("-") ? "dm" : "";
      return `<div class="${cls}">${esc(l) || " "}</div>`;
    }).join("");
  }

  function renderActivity() {
    const box = $("activity");
    if (!S.activity.length) { box.innerHTML = '<div class="act-empty">Пока тихо. Здесь появится, что Клодик делает на ПК 🐑</div>'; return; }
    box.innerHTML = S.activity.slice().reverse().map((a) =>
      `<div class="act ${a.state}${a.diff_id ? " has-diff" : ""}"${a.diff_id ? ` data-diff="${esc(a.diff_id)}"` : ""} title="${a.diff_id ? "Нажми, чтобы посмотреть изменения" : esc(a.detail)}"><span class="tm">${esc(a.time)}</span><span>${esc(a.emoji)}</span><span class="tx">${esc(a.text)}${a.diff_id ? ` <span class="dn"><b>+${a.plus}</b> <i>−${a.minus}</i></span>` : ""}</span></div>`).join("");
  }

  async function loadMessages(force) {
    const {messages} = await api("/api/messages?project=" + encodeURIComponent(current));
    const confirms = S ? S.confirms : [];
    const key = current + "|" + messages.length + "|" + (messages.at(-1)?.id || "") + "|" + confirms.map((c) => c.id).join(",");
    if (key === msgsKey && !force) return;
    msgsKey = key;
    const box = $("messages");
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    if (!messages.length && !confirms.length) {
      box.innerHTML = `<div class="empty"><img src="/ui/img/chibi.png" alt=""><div>Здесь пока пусто. Напиши Клодику, что будем делать - Яночка ему передаст ✨</div></div>`;
    } else {
      box.innerHTML = messages.map((m) => {
        const files = (m.files || []).length ? `<div class="files">${m.files.map((f) => `<span class="file">📄 ${esc(f)}</span>`).join("")}</div>` : "";
        const yn = m.who === "yanka";
        const ava = m.role === "user" ? '<div class="ava"><img src="/ui/img/avatar.png" alt=""></div>'
          : yn ? `<div class="ava yn-ava">${window.Yanochka ? Yanochka.svg : "🐑"}</div>` : '<div class="ava">К</div>';
        const body = m.role === "user" ? "<p>" + inline(m.text).replace(/\n/g, "<br>") + "</p>" : md(m.text);
        const who = m.role === "user" ? "Яна" + (m.to === "yanka" ? " → Янке" : m.route ? " → Клодику" : "") : yn ? "Янка" : "Клодик";
        const why = yn && m.meta ? `<div class="why" title="${esc(m.meta.why || "")}">🧠 ${esc(m.meta.model || "")}${m.meta.tools && m.meta.tools.length ? " · 🔧 " + esc(m.meta.tools.join(", ")) : ""} · ${esc(String(m.meta.seconds || 0))} с · бесплатно, на ПК</div>` : "";
        const route = m.role === "user" && m.route ? `<div class="why">🧭 ${esc(m.route)}</div>` : "";
        return `<div class="msg ${m.role}">${ava}<div><div class="body">${body}${files}</div><div class="meta">${who} · ${fmtTime(m.time)}</div>${why}${route}</div></div>`;
      }).join("") + confirms.map((c) => c.kind === "question" ? questionHtml(c) : `
        <div class="confirm" data-id="${esc(c.id)}"><h4>🔔 ${esc(c.title)}</h4><pre>${esc(c.message)}</pre>${c.diff ? `<div class="diff">${diffHtml(c.diff)}</div>` : ""}
          <div class="row"><button class="btn ghost" data-allow="0">Отклонить</button>${c.rule ? `<button class="btn ghost" data-allow="always" title="${c.rule.kind === "mcp" ? `Больше не спрашивать про «${esc(c.rule.prefix)}» в ${esc(c.rule.folder)}` : `Больше не спрашивать про «${esc(c.rule.prefix)} …» в папке ${esc(c.rule.folder)}`}">✅ Всегда «${esc(c.rule.prefix)}» ${c.rule.kind === "mcp" ? "в " + esc(c.rule.folder) : "здесь"}</button>` : ""}<button class="btn" data-allow="1">Разрешить</button></div></div>`).join("");
    }
    if (atBottom || force) box.scrollTop = box.scrollHeight;
  }

  function handleEvents() {
    const evs = S.events || [];
    if (lastEvent === null) { lastEvent = evs.length ? evs.at(-1).n : 0; return; }
    evs.filter((e) => e.n > lastEvent).forEach((e) => {
      if (e.type === "reply") {
        say("done"); ding();
        const p = S.projects.find((x) => x.id === e.project);
        notify((e.who === "yanka" ? "Янка ответила 🐑" : "Клодик закончил! ✨") + (p ? " (" + p.name + ")" : ""));
        if (e.project !== current) { unread[e.project] = 1; localStorage.setItem("cozy-unread", JSON.stringify(unread)); }
      } else if (e.type === "confirm") { say("confirm"); ding(); notify("Клодик спрашивает разрешения 🔔"); }
      else if (e.type === "picked") say("thinking");
      else if (e.type === "signal") say("sent");
      else if (e.type === "queued") say("queued");
      else if (e.type === "signal_error") say("error");
      else if (e.type === "status" && e.project === current) say("thinking", "💬 " + e.text);
    });
    if (evs.length) lastEvent = Math.max(lastEvent, evs.at(-1).n);
  }

  async function poll() {
    try {
      const st = await api("/api/state");
      if (offline) { offline = false; say("idle"); }
      S = st;
      if (!S.projects.find((p) => p.id === current)) current = S.projects[0].id;
      if (S.v !== lastV) {
        lastV = S.v;
        handleEvents();
        renderProjects(); renderStatus(); renderHeader(); renderActivity(); renderMoon();
        await loadMessages();
        if (!mood || mood === "setup") say("idle");
      } else renderHeader();
    } catch (e) {
      if (!offline) { offline = true; say("offline"); setDot("bridge", "err", "нет связи"); }
    }
    setTimeout(poll, document.hidden ? 2500 : 1200);
  }

  function selectProject(id) {
    current = id; localStorage.setItem("cozy-project", id);
    delete unread[id]; localStorage.setItem("cozy-unread", JSON.stringify(unread));
    msgsKey = ""; pendingFiles = []; renderChips();
    if (S) { renderProjects(); renderHeader(); }
    loadMessages(true);
  }

  // ---------- отправка ----------
  const ta = $("text");
  const autosize = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px"; };
  ta.addEventListener("input", autosize);
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  $("btn-send").onclick = send;
  // 1.2.2: просмотр изменений из журнала
  $("activity").addEventListener("click", async (e) => {
    const a = e.target.closest("[data-diff]"); if (!a) return;
    try {
      const r = await api("/api/diff", {id: a.dataset.diff});
      $("md-path").textContent = "· " + r.path;
      $("md-body").innerHTML = diffHtml(r.diff || "(без изменений)");
      $("m-diff").classList.remove("hidden");
    } catch (er) { toast(er.message); }
  });

  // 1.2.2: 🌙 выключить ПК, когда Клодик закончит
  let moonDeadline = null;
  function renderMoon() {
    const sd = (S && S.shutdown) || {};
    $("btn-moon").classList.toggle("on", !!sd.armed);
    if (sd.left != null) { if (!moonDeadline) moonDeadline = Date.now() + sd.left * 1000; }
    else moonDeadline = null;
    tickMoon();
  }
  function tickMoon() {
    const bar = $("moonbar");
    if (!moonDeadline) { bar.classList.add("hidden"); return; }
    $("moon-left").textContent = Math.max(0, Math.round((moonDeadline - Date.now()) / 1000));
    bar.classList.remove("hidden");
  }
  setInterval(tickMoon, 1000);
  $("moon-cancel").onclick = async () => {
    try { await api("/api/shutdown", {action: "cancel"}); moonDeadline = null; tickMoon(); toast("Отменила, ПК не выключится 💕"); }
    catch (e) { toast(e.message); }
  };
  $("btn-moon").onclick = async () => {
    const armed = !((S && S.shutdown) || {}).armed;
    try {
      await api("/api/shutdown", {action: armed ? "arm" : "disarm"});
      if (S) S.shutdown = {...(S.shutdown || {}), armed};
      renderMoon();
      toast(armed ? "🌙 Когда Клодик закончит, ПК выключится через минуту. Отменить можно здесь же." : "Хорошо, ПК выключать не буду", 5000);
    } catch (e) { toast(e.message); }
  };

  async function send() {
    const text = ta.value.trim();
    if (!text && !pendingFiles.length) return;
    $("btn-send").disabled = true;
    try {
      const r = await api("/api/send", {project: current, text, files: pendingFiles});
      ta.value = ""; autosize(); pendingFiles = []; renderChips();
      if ("Notification" in window && S?.settings.notifications && Notification.permission === "default") Notification.requestPermission();
      await loadMessages(true);
    } catch (e) { toast(e.message); }
    $("btn-send").disabled = false; ta.focus();
  }

  // ---------- файлы ----------
  function renderChips() {
    $("chips").innerHTML = pendingFiles.map((f, i) => `<span class="chip">📄 ${esc(f)}<b data-rm="${i}">✕</b></span>`).join("");
  }
  $("chips").onclick = (e) => { const i = e.target.dataset.rm; if (i !== undefined) { pendingFiles.splice(+i, 1); renderChips(); } };
  async function upload(files) {
    for (const f of files) {
      try {
        const r = await api(`/api/upload?project=${encodeURIComponent(current)}&name=${encodeURIComponent(f.name)}`, undefined, f);
        pendingFiles.push(r.path); renderChips();
      } catch (e) { toast("Не удалось загрузить " + f.name + ": " + e.message); }
    }
  }
  $("btn-attach").onclick = () => $("file").click();
  $("file").onchange = (e) => { upload([...e.target.files]); e.target.value = ""; };
  let dragDepth = 0;
  const chat = $("chat");
  chat.addEventListener("dragenter", (e) => { if (e.dataTransfer.types.includes("Files")) { dragDepth++; $("drop").classList.remove("hidden"); e.preventDefault(); } });
  chat.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("drop").classList.add("hidden"); } });
  chat.addEventListener("dragover", (e) => e.preventDefault());
  chat.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("drop").classList.add("hidden"); upload([...e.dataTransfer.files]); });
  ta.addEventListener("paste", (e) => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); upload(fs); } });

  // ---------- клики в чате ----------
  $("messages").addEventListener("click", async (e) => {
    const q = e.target.closest(".question");  // 1.2.5: ответ на вопрос Клодика
    if (q && (e.target.dataset.answer !== undefined || e.target.dataset.qsend || e.target.dataset.qskip)) {
      const d = e.target.dataset;
      const answer = d.answer !== undefined ? d.answer : d.qskip ? "(Яна пропустила вопрос - реши сам)" : q.querySelector("textarea").value.trim();
      if (!answer) { toast("Напиши ответ или выбери вариант 🐑"); return; }
      q.querySelectorAll("button, textarea").forEach((b) => (b.disabled = true));
      try { await api("/api/confirm", {id: q.dataset.id, answer}); say("thinking", "Передала ответ Клодику ✨"); }
      catch (er) { toast(er.message); }
      return;
    }
    const c = e.target.closest(".confirm");
    if (c && e.target.dataset.allow !== undefined) {
      c.querySelectorAll("button").forEach((b) => (b.disabled = true));
      const a = e.target.dataset.allow, yes = a !== "0";
      await api("/api/confirm", {id: c.dataset.id, allow: a === "always" ? "always" : a === "1"}).catch((er) => toast(er.message));
      say(yes ? "thinking" : "idle", a === "always" ? "Запомнила! Такие команды тут больше не спрашиваю ✨" : yes ? "Разрешила! Клодик продолжает ✨" : "Хорошо, не будем 🐑");
      return;
    }
    if (e.target.classList.contains("copy")) {
      const code = e.target.parentElement.querySelector("code").innerText;
      navigator.clipboard.writeText(code).then(() => { e.target.textContent = "скопировано ✓"; setTimeout(() => (e.target.textContent = "копировать"), 1500); });
    }
  });
  document.addEventListener("click", (e) => { if (e.target.dataset.open === "settings") openSettings(); });
  $("btn-folder").onclick = () => api("/api/open_folder", {project: current}).catch((e) => toast(e.message));
  $("btn-recall").onclick = async () => {
    const r = await api("/api/recall", {project: current}).catch((e) => toast(e.message));
    if (r) toast("Пришли Клодику «.» в чате Notion 🐑");
  };

  // ---------- модалки ----------
  document.querySelectorAll(".modal").forEach((m) => {
    m.addEventListener("mousedown", (e) => { if (e.target === m) m.classList.add("hidden"); });
    m.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => m.classList.add("hidden")));
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { document.querySelectorAll(".modal").forEach((m) => m.classList.add("hidden")); $("pet-game").classList.add("hidden"); } });

  // ---------- 1.2.4: окно ведёт себя как приложение, а не как вкладка браузера ----------
  {
    const keepMenu = (el) => !!(el && el.closest && el.closest("input, textarea, select, [contenteditable], a[href]"));
    // меню по правой кнопке - только в полях ввода, на ссылках и на выделенном тексте (чтобы копировать)
    document.addEventListener("contextmenu", (e) => {
      const sel = window.getSelection();
      if (keepMenu(e.target) || (sel && !sel.isCollapsed && String(sel).trim())) return;
      e.preventDefault();
    });
    // обновить, печать, сохранить, исходный код, открыть файл, история, закладка, масштаб.
    // e.code - физическая клавиша, поэтому работает и на русской раскладке. Ctrl+0 оставлен - вернуть обычный размер.
    const ctrlKeys = new Set(["KeyR", "KeyP", "KeyS", "KeyU", "KeyO", "KeyH", "KeyD", "Equal", "Minus", "NumpadAdd", "NumpadSubtract"]);
    const navKeys = new Set(["F5", "F7", "BrowserBack", "BrowserForward", "BrowserRefresh", "BrowserHome"]);
    document.addEventListener("keydown", (e) => {
      if (navKeys.has(e.key) || (e.ctrlKey && !e.altKey && ctrlKeys.has(e.code))
          || (e.altKey && !e.ctrlKey && ["ArrowLeft", "ArrowRight", "Home"].includes(e.key))) e.preventDefault();
    });
    // Ctrl+колёсико и щипок на тачпаде не меняют масштаб
    window.addEventListener("wheel", (e) => { if (e.ctrlKey) e.preventDefault(); }, {passive: false});
    // боковые кнопки мыши «назад/вперёд» не уводят со страницы
    ["mousedown", "mouseup", "auxclick"].forEach((t) => window.addEventListener(t, (e) => { if (e.button === 3 || e.button === 4) e.preventDefault(); }));
    // файл, брошенный мимо чата, не открывается вместо CozyCode
    const hasFiles = (e) => !!(e.dataTransfer && [...(e.dataTransfer.types || [])].includes("Files"));
    window.addEventListener("dragover", (e) => { if (hasFiles(e) && !e.defaultPrevented) { e.preventDefault(); e.dataTransfer.dropEffect = "none"; } });
    window.addEventListener("drop", (e) => { if (hasFiles(e)) e.preventDefault(); });
  }
  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === t));
    document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("hidden", p.id !== t.dataset.tab));
  }));

  const CHECKS = ["confirm_commands", "confirm_deletes", "confirm_edits", "sound", "notifications", "autostart", "native_window", "bridge_autostart"];
  const FIELDS = ["tunnel", "ngrok_domain", "history_messages"];
  function openSettings() {
    if (!S) return;
    const st = S.settings;
    FIELDS.forEach((k) => ($("s-" + k).value = st[k] ?? ""));
    CHECKS.forEach((k) => ($("s-" + k).checked = !!st[k]));
    $("s-mcp").value = S.mcp_url;
    $("s-mcptoken").value = st.mcp_token || "";
    $("s-mcptoken").type = "password";
    $("s-workspace").textContent = "Рабочая папка Клодика: " + S.workspace;
    $("s-note").textContent = "";
    renderFolders(); renderRules(); renderPrograms(true); loadBackups();
    $("m-settings").classList.remove("hidden");
  }
  function collectSettings() {
    const body = {};
    FIELDS.forEach((k) => (body[k] = $("s-" + k).value.trim()));
    body.history_messages = Math.max(0, Math.min(50, parseInt(body.history_messages || "10", 10) || 0));
    CHECKS.forEach((k) => (body[k] = $("s-" + k).checked));
    return body;
  }
  $("btn-settings").onclick = openSettings;
  $("btn-showkey").onclick = () => { const i = $("s-mcptoken"); i.type = i.type === "password" ? "text" : "password"; };
  document.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => {
    const v = $(b.dataset.copy).value;
    try { await navigator.clipboard.writeText(v); }
    catch (e) { const t = document.createElement("textarea"); t.value = v; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }
    toast("Скопировано 📋");
  }));
  $("btn-ssave").onclick = async () => {
    const body = collectSettings();
    if (body.notifications && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
    try {
      const r = await api("/api/settings", body);
      $("m-settings").classList.add("hidden");
      toast(r.restart_needed ? "Сохранено. Настройки туннеля заработают после перезапуска CozyCode." : "Сохранено ✨");
      mood = "";
    } catch (e) { $("s-note").textContent = e.message; }
  };

  // проекты
  const EMOJIS = ["✨", "🐑", "🎮", "🌸", "🌙", "🎨", "🧩", "🎀", "🔔", "🍓", "🐾", "🚀", "📚", "🎵", "🛠️", "🌈"];
  let editing = null, chosenEmoji = "✨";
  function openProject(p) {
    editing = p || null; chosenEmoji = p ? p.emoji : "✨";
    $("mp-title").textContent = p ? "✏️ Проект" : "✨ Новый проект";
    $("mp-name").value = p ? p.name : ""; $("mp-folder").value = ""; $("mp-ro").checked = false; folderHint();
    $("mp-folder-l").classList.toggle("hidden", !!p);
    $("mp-remove").classList.toggle("hidden", !p || p.id === "general");
    $("mp-save").textContent = p ? "Сохранить" : "Создать";
    $("mp-emojis").innerHTML = EMOJIS.map((e) => `<button class="${e === chosenEmoji ? "on" : ""}">${e}</button>`).join("");
    $("m-project").classList.remove("hidden"); $("mp-name").focus();
  }
  $("mp-emojis").onclick = (e) => {
    if (e.target.tagName !== "BUTTON") return;
    chosenEmoji = e.target.textContent;
    $("mp-emojis").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === e.target));
  };
  $("btn-new").onclick = () => openProject(null);
  $("btn-pedit").onclick = () => openProject(S.projects.find((p) => p.id === current));
  $("mp-name").addEventListener("keydown", (e) => { if (e.key === "Enter") $("mp-save").click(); });
  $("mp-save").onclick = async () => {
    const name = $("mp-name").value.trim();
    if (!name) return $("mp-name").focus();
    try {
      if (editing) await api("/api/project/update", {id: editing.id, name, emoji: chosenEmoji});
      else { const r = await api("/api/project", {name, emoji: chosenEmoji, folder: $("mp-folder").value.trim(), readonly: $("mp-ro").checked}); current = r.project.id; }
      $("m-project").classList.add("hidden"); lastV = -1; selectProject(current);
    } catch (e) { toast(e.message); }
  };
  $("mp-remove").onclick = async () => {
    if (!confirm("Убрать проект из списка? Файлы и переписка останутся на месте.")) return;
    await api("/api/project/remove", {id: editing.id}).catch((e) => toast(e.message));
    $("m-project").classList.add("hidden"); lastV = -1; selectProject("general");
  };

  // ---------- 1.2: папка проекта ----------
  const isAbs = (s) => /^[a-zA-Z]:[\\/]|^\\\\/.test(s);
  function folderHint() {
    const v = $("mp-folder").value.trim(), abs = isAbs(v);
    $("mp-ro-l").classList.toggle("hidden", !abs);
    $("mp-folder-hint").textContent = abs ? "Эта папка вне рабочей - CozyCode добавит её в разрешённые (⚙️ Настройки → 📁 Папки). Удалённое там попадает в Корзину." : "";
  }
  $("mp-folder").addEventListener("input", folderHint);
  $("mp-browse").onclick = async () => {
    $("mp-browse").disabled = true;
    try {
      const r = await api("/api/pick_folder", {});
      if (r.path) {
        $("mp-folder").value = r.path;
        if (!$("mp-name").value.trim()) $("mp-name").value = r.path.split(/[\\/]/).filter(Boolean).pop() || "";
        folderHint();
      }
    } catch (e) { toast(e.message); }
    $("mp-browse").disabled = false;
  };

  // ---------- 1.2: скилы ----------
  let skEditing = null;
  async function loadSkills() {
    const box = $("sk-list");
    try {
      const r = await api("/api/skills", {project: current});
      const group = (title, items) => !items.length ? "" : `<div class="label">${title}</div>` + items.map((s) => `
        <div class="item${s.enabled ? "" : " off"}">
          <label class="switch" title="Включить / выключить"><input type="checkbox" data-sk-toggle="${esc(s.key)}" ${s.enabled ? "checked" : ""}><i></i></label>
          <div class="grow"><b>${esc(s.name)}</b>${s.source && /^https?:/.test(s.source) ? ' <span class="tag" title="Из интернета">🌐</span>' : ""}<div class="hint">${esc(s.description)}</div></div>
          <button class="btn ghost small" data-sk-open="${esc(s.path)}">Открыть</button>
        </div>`).join("");
      const html = group("Общие - для всех проектов", r.skills.filter((s) => s.scope === "global"))
        + group("Только этого проекта", r.skills.filter((s) => s.scope === "project"));
      box.innerHTML = html || '<div class="hint">Скилов пока нет - попроси Клодика сделать первый ✨</div>';
      box.dataset.hasProject = r.project_dir ? "1" : "";
    } catch (e) { box.innerHTML = `<div class="hint">❌ ${esc(e.message)}</div>`; }
  }
  async function openSkills() {
    const p = S.projects.find((x) => x.id === current) || S.projects[0];
    $("sk-proj").textContent = "· " + p.name;
    $("sk-edit").classList.add("hidden");
    $("sk-list").innerHTML = '<div class="hint">Загружаю…</div>';
    $("m-skills").classList.remove("hidden");
    await loadSkills();
  }
  $("sk-list").addEventListener("change", async (e) => {
    const key = e.target.dataset.skToggle; if (!key) return;
    try { await api("/api/skill/toggle", {key, enabled: e.target.checked}); e.target.closest(".item").classList.toggle("off", !e.target.checked); }
    catch (er) { toast(er.message); }
  });
  $("sk-list").addEventListener("click", async (e) => {
    const path = e.target.dataset.skOpen; if (!path) return;
    try { const r = await api("/api/skill/get", {path}); editSkill(path, r.content); } catch (er) { toast(er.message); }
  });
  const SKILL_NEW = "---\nname: Название скила\ndescription: Когда Клодику его применять\nsource: Яна\n---\n\nПравила:\n- \n";
  function editSkill(path, content) {
    skEditing = path;
    $("sk-edit-title").textContent = path ? "✏️ " + path : "✏️ Новый скил";
    $("sk-scope-l").classList.toggle("hidden", !!path || !$("sk-list").dataset.hasProject);
    $("sk-scope").checked = false;
    $("sk-del").classList.toggle("hidden", !path);
    $("sk-text").value = content; $("sk-edit").classList.remove("hidden"); $("sk-text").focus();
  }
  $("sk-new").onclick = () => editSkill(null, SKILL_NEW);
  $("sk-cancel").onclick = () => $("sk-edit").classList.add("hidden");
  $("sk-save").onclick = async () => {
    try {
      await api("/api/skill/save", {path: skEditing, project: current, scope: $("sk-scope").checked ? "project" : "global", content: $("sk-text").value});
      $("sk-edit").classList.add("hidden"); toast("Скил сохранён 🧠"); loadSkills();
    } catch (e) { toast(e.message); }
  };
  $("sk-del").onclick = async () => {
    if (!confirm("Удалить скил? Копия останется в ⚙️ Настройки → ↩️ Откат.")) return;
    try { await api("/api/skill/delete", {path: skEditing}); } catch (e) { toast(e.message); }
    $("sk-edit").classList.add("hidden"); loadSkills();
  };
  function prefill(text) { $("m-skills").classList.add("hidden"); ta.value = text; autosize(); ta.focus(); ta.setSelectionRange(text.length, text.length); }
  $("sk-ask").onclick = () => prefill("Сделай скил: ");
  $("sk-web").onclick = () => prefill("Найди в интернете скил для: ");
  $("btn-skills").onclick = openSkills;

  // ---------- 1.2: память проекта ----------
  $("btn-memory").onclick = async () => {
    try {
      const r = await api("/api/memory", {project: current});
      $("mm-path").textContent = "· " + r.path; $("mm-text").value = r.content; $("mm-note").textContent = "";
      $("m-memory").classList.remove("hidden");
    } catch (e) { toast(e.message); }
  };
  $("mm-save").onclick = async () => {
    try { await api("/api/memory", {project: current, content: $("mm-text").value}); $("mm-note").textContent = "Сохранено ✓"; }
    catch (e) { $("mm-note").textContent = "❌ " + e.message; }
  };

  // ---------- 1.2: папки и откат в настройках ----------
  function renderFolders() {
    const list = (S && S.folders) || [];
    $("folders").innerHTML = list.length ? list.map((f) => `
      <div class="item">
        <div class="grow"><b>📁 ${esc(f.path)}</b><div class="hint">${f.mode === "ro" ? "👀 только просмотр" : "✍️ можно менять"}</div></div>
        <button class="btn ghost small" data-fmode="${esc(f.path)}" data-ro="${f.mode === "ro" ? "" : "1"}">${f.mode === "ro" ? "Разрешить изменения" : "Только просмотр"}</button>
        <button class="btn ghost small danger" data-fdel="${esc(f.path)}">Убрать</button>
      </div>`).join("") : '<div class="hint">Пока только рабочая папка.</div>';
  }
  function renderRules() {
    const list = (S && S.cmd_rules) || [];
    $("cmdrules").innerHTML = list.length ? list.map((r) => `
      <div class="item">
        <div class="grow"><b>⚡ ${esc(r.prefix)} …</b><div class="hint">📁 ${esc(r.folder)}</div></div>
        <button class="btn ghost small danger" data-rprefix="${esc(r.prefix)}" data-rfolder="${esc(r.folder)}">Убрать</button>
      </div>`).join("") : '<div class="hint">Пока нет - Клодик спрашивает про каждую команду.</div>';
  }
  $("cmdrules").addEventListener("click", async (e) => {
    const d = e.target.dataset; if (!d.rprefix) return;
    try {
      await api("/api/cmdrules", {prefix: d.rprefix, folder: d.rfolder});
      S.cmd_rules = (S.cmd_rules || []).filter((r) => !(r.prefix === d.rprefix && r.folder === d.rfolder));
      renderRules();
    } catch (er) { toast(er.message); }
  });
  $("folders").addEventListener("click", async (e) => {
    const d = e.target.dataset;
    try {
      if (d.fmode) {
        await api("/api/folders", {action: "mode", path: d.fmode, readonly: !!d.ro});
        S.folders.forEach((f) => { if (f.path === d.fmode) f.mode = d.ro ? "ro" : "rw"; });
      } else if (d.fdel) {
        if (!confirm("Убрать папку из разрешённых? Клодик потеряет к ней доступ, файлы останутся на месте.")) return;
        await api("/api/folders", {action: "remove", path: d.fdel});
        S.folders = S.folders.filter((f) => f.path !== d.fdel);
      } else return;
      renderFolders();
    } catch (er) { toast(er.message); }
  });
  // ---------- 1.2.5: 💭 вопросы Клодика и 🧩 Программы (Blender, Roblox Studio и др.) ----------
  function questionHtml(c) {
    const opts = (c.options || []).map((o) => `<button class="btn ghost small" data-answer="${esc(o)}">${esc(o)}</button>`).join("");
    return `
        <div class="confirm question" data-id="${esc(c.id)}"><h4>💭 ${esc(c.title)}</h4><pre>${esc(c.message)}</pre>
          ${opts ? `<div class="row wrap">${opts}</div>` : ""}
          <textarea rows="2" placeholder="Или напиши свой ответ…"></textarea>
          <div class="row"><button class="btn ghost" data-qskip="1">Пропустить</button><button class="btn" data-qsend="1">Ответить</button></div></div>`;
  }
  const PST = {on: "🟢 работает", starting: "⏳ запускается…", error: "🔴 ошибка", off: "⚪ не запущена"};
  let progKey = "";
  function renderPrograms(force) {
    const box = $("programs");
    if (!box) return;
    const list = (S && S.programs) || [];
    const key = JSON.stringify(list);
    if (!force && key === progKey) return;
    progKey = key;
    box.innerHTML = list.length ? list.map((p) => `
      <div class="item prog">
        <div class="grow"><b>${esc(p.emoji)} ${esc(p.name)}</b> <span class="hint">${PST[p.state] || esc(p.state)}${p.tools != null ? " · инструментов: " + p.tools : ""}${p.calls ? " · вызовов: " + p.calls : ""}</span>
          <div class="hint"><code>${esc(p.line)}</code></div>
          ${p.error ? `<div class="hint">⚠️ ${esc(p.error)}</div>` : ""}
          ${p.state !== "on" && p.help ? `<div class="hint">💡 ${esc(p.help)}</div>` : ""}
          <div class="hint">${p.ask ? "🔔 спрашивает тебя перед действиями" : "⚡ действует без вопросов"}${p.allowed.length ? " · всегда можно: " + esc(p.allowed.join(", ")) : ""}</div>
          <div class="row wrap">
            <button class="btn ghost small" data-pact="test" data-pid="${esc(p.id)}">${p.state === "on" ? "🔄 Перезапустить" : "▶️ Проверить"}</button>
            ${p.state === "on" ? `<button class="btn ghost small" data-pact="stop" data-pid="${esc(p.id)}">⏹ Стоп</button>` : ""}
            <button class="btn ghost small" data-pact="ask" data-pid="${esc(p.id)}" data-val="${p.ask ? "" : "1"}">${p.ask ? "Не спрашивать" : "Спрашивать"}</button>
            ${p.allowed.length ? `<button class="btn ghost small" data-pact="reset" data-pid="${esc(p.id)}">Сбросить «всегда»</button>` : ""}
            <button class="btn ghost small danger" data-pact="remove" data-pid="${esc(p.id)}">Убрать</button>
          </div>
        </div>
      </div>`).join("") : '<div class="hint">Пока ни одной программы - добавь готовую кнопкой ниже.</div>';
  }
  async function programAction(body) {
    try {
      const r = await api("/api/programs", body);
      if (r.programs && S) { S.programs = r.programs; renderPrograms(true); }
      if (r.note) toast(r.note, 7000);
      return true;
    } catch (er) { toast(er.message); return false; }
  }
  $("programs").addEventListener("click", async (e) => {
    const d = e.target.dataset;
    if (!d.pact) return;
    if (d.pact === "remove" && !confirm("Убрать программу из CozyCode? Сама программа останется на ПК.")) return;
    if (d.pact === "ask" && !d.val && !confirm("Клодик будет действовать в этой программе без вопросов. Точно?")) return;
    await programAction({action: d.pact, id: d.pid, ask: !!d.val});
  });
  document.querySelectorAll("[data-padd]").forEach((b) => b.addEventListener("click", () => programAction({action: "add_preset", preset: b.dataset.padd})));
  $("p-save").addEventListener("click", async () => {
    const name = $("p-name").value.trim(), cmdline = $("p-cmd").value.trim();
    if (!name || !cmdline) { toast("Впиши название и команду запуска 🐑"); return; }
    if (await programAction({action: "save", name, cmdline})) { $("p-name").value = ""; $("p-cmd").value = ""; }
  });
  setInterval(() => { if (!$("m-settings").classList.contains("hidden")) renderPrograms(); }, 1500);

  async function loadBackups() {
    const box = $("backups"); box.innerHTML = '<div class="hint">Загружаю…</div>';
    try {
      const r = await api("/api/backups", {});
      box.innerHTML = r.items.length ? r.items.map((b) => `
        <div class="item${b.restored ? " off" : ""}">
          <div class="grow"><b>${b.is_dir ? "📁" : "📄"} ${esc(b.name)}</b><div class="hint">${esc((b.time || "").slice(0, 16))} · ${esc(b.action)} · ${esc(b.path)}</div></div>
          <button class="btn ghost small" data-restore="${esc(b.id)}">↩️ Вернуть</button>
        </div>`).join("") : '<div class="hint">Копий пока нет.</div>';
    } catch (e) { box.innerHTML = `<div class="hint">❌ ${esc(e.message)}</div>`; }
  }
  $("backups").addEventListener("click", async (e) => {
    const id = e.target.dataset.restore; if (!id) return;
    if (!confirm("Вернуть эту версию? Текущая версия тоже сохранится в копиях.")) return;
    try { const r = await api("/api/backups/restore", {id}); toast(r.result || "Вернула ✨"); loadBackups(); } catch (er) { toast(er.message); }
  });

  // 1.3.0: тамагочи с Яночкой
  function openPet() {
    const f = $("pet-frame");
    if (!f.src) f.src = "/ui/tamagotchi.html";
    $("pet-game").classList.remove("hidden");
    setTimeout(() => { try { f.contentWindow.focus(); } catch (e) { /* не страшно */ } }, 50);
  }
  function closePet() { $("pet-game").classList.add("hidden"); }
  $("btn-pet").onclick = openPet;
  $("yana").onclick = openPet;
  $("pet-game").onclick = (e) => { if (e.target === $("pet-game")) closePet(); };
  window.addEventListener("message", (e) => { if (e.origin === location.origin && e.data && e.data.cozy === "close-pet") closePet(); });

  // ---------- 1.3.0: Янка ----------
  const whoSel = $("who");
  whoSel.onchange = async () => {
    try { await api("/api/settings", {yanka_mode: whoSel.value}); S.settings.yanka_mode = whoSel.value; }
    catch (e) { toast(e.message); }
    toast({auto: "🐑✨ Разговоры - Янке на ПК, код и файлы - Клодику", local: "🐑 Отвечает только Янка (на ПК, бесплатно)", off: "✨ Всё - Клодику, как раньше"}[whoSel.value]);
    ta.placeholder = whoSel.value === "off" ? "Напиши Клодику…  (Enter - отправить, Shift+Enter - новая строка)" : "Напиши Янке или Клодику…  (@клодик - сразу Клодику, @пк - сразу Янке)";
  };
  setInterval(() => { if (S && S.settings && S.settings.yanka_mode && document.activeElement !== whoSel && whoSel.value !== S.settings.yanka_mode) whoSel.value = S.settings.yanka_mode; }, 1500);

  const Y = {state: null};
  const yapi = (what, body = {}) => api("/api/yanka/" + what, body);
  document.querySelectorAll(".ytab").forEach((t) => (t.onclick = () => {
    document.querySelectorAll(".ytab").forEach((x) => x.classList.toggle("on", x === t));
    document.querySelectorAll(".ypane").forEach((p) => p.classList.toggle("hidden", p.id !== t.dataset.y));
    if (t.dataset.y === "y-memory") yMemory();
  }));
  $("btn-yanka").onclick = async () => { $("m-yanka").classList.remove("hidden"); await yLoad(); };
  async function yLoad() {
    try { Y.state = await yapi("state", {project: current}); } catch (e) { toast(e.message); return; }
    const st = Y.state, L = st.local || {}, R = L.resources || {}, lm = L.lmstudio || {};
    $("y-sub").textContent = `потрачено в этом месяце: $${(st.spent.month || 0).toFixed(4)} · ${st.spent.tokens_month || 0} токенов`;
    $("y-res").innerHTML = `🖥️ ${esc(R.gpu || "видеокарта не найдена")} · ${R.vram_gb || 0} ГБ (свободно ${R.vram_free_gb || 0}) · память ${R.ram_gb || 0} ГБ · LM Studio: ${lm.installed ? (lm.server ? "🟢 работает" : "⚪ выключен") : "не установлен"}` +
      (lm.loaded && lm.loaded.length ? ` · загружена: <b>${esc(lm.loaded.join(", "))}</b>` : "");
    const WHERE = {gpu: "целиком в видеокарте", split: "часть на процессоре (медленнее)", no: "не поместится"};
    $("y-local").innerHTML = (lm.models || []).map((m) => `<div class="item"><div class="grow"><b>${esc(m.name || m.key)}</b>
        <span class="tag ${m.fit.where}">${WHERE[m.fit.where]}</span>${m.tools ? '<span class="tag">🔧 инструменты</span>' : ""}
        <div class="hint">${esc(m.key)} · ${m.size_gb} ГБ · ${esc(m.quant || "")} · нужно ~${m.fit.need_gb} ГБ${m.fit.notes.length ? " · " + esc(m.fit.notes.join("; ")) : ""}</div></div>
        ${m.fit.where !== "no" ? `<button class="btn small" data-yload="${esc(m.key)}">${st.roles.chat === m.key ? "⭐ основная" : "Сделать основной"}</button>` : ""}</div>`).join("") || '<div class="hint">Скачанных моделей нет.</div>';
    $("y-prov").innerHTML = st.providers.map((p) => `<div class="item${p.enabled ? "" : " off"}"><div class="grow"><b>${esc(p.name)}</b>${p.local ? '<span class="tag gpu">на ПК</span>' : '<span class="tag">облако</span>'}
        <div class="hint">${esc(p.model || "модель не выбрана")}${p.has_key ? " · ключ " + esc(p.key_hint) : p.local ? "" : " · нет ключа"}${p.health ? (p.health.ok ? " · 🟢 отвечает" : " · ⚪ " + esc(p.health.error || "")) : ""}</div></div>
        <button class="btn ghost small" data-yprov="${esc(p.id)}" data-on="${p.enabled ? 0 : 1}">${p.enabled ? "Выключить" : "Включить"}</button>${p.local ? "" : `<button class="btn ghost small" data-yprm="${esc(p.id)}">🗑️</button>`}</div>`).join("");
    if (document.activeElement !== $("y-bday")) $("y-bday").value = st.budget.daily_usd;
    if (document.activeElement !== $("y-bmonth")) $("y-bmonth").value = st.budget.monthly_usd;
    const pidSel = $("y-pid"), keep = pidSel.value;
    pidSel.innerHTML = Object.entries(st.templates).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    if (keep) pidSel.value = keep;
    const TS = {pending: "⏳ ждёт", running: "⚙️ делаю", done: "✅ готово", failed: "❌ не вышло", waiting: "🙋 жду Клодика", skipped: "⏭️"};
    const EX = st.executors || {};
    $("y-tlist").innerHTML = st.tasks.map((t) => `<div class="item" style="display:block"><div class="row"><div class="grow"><b>${esc(t.title)}</b><div class="hint">${esc(TS[t.status] || t.status)} · ${esc(t.progress)} · ${esc(t.updated.slice(0, 16).replace("T", " "))}</div></div>
        ${t.running ? `<button class="btn ghost small" data-ytstop="${esc(t.id)}">⏹ Стоп</button>` : t.status === "waiting" ? `<button class="btn small" data-ytdone="${esc(t.id)}">✅ Готово</button>` : t.status !== "done" ? `<button class="btn small" data-ytrun="${esc(t.id)}">▶️ ${t.status === "pending" && t.progress.startsWith("0/") ? "Выполнить" : "Продолжить"}</button>` : ""}
        <button class="btn ghost small" data-ytdel="${esc(t.id)}">🗑️</button></div>
        <ol class="steps">${t.steps.map((s) => `<li><span class="tag">${esc(TS[s.status] || s.status)}</span> ${esc(s.text)} <span class="hint">· ${esc(EX[s.executor] || s.executor)}</span>${s.result ? `<details><summary class="hint">результат</summary><div class="hint">${esc(s.result)}</div></details>` : ""}${s.error ? `<div class="hint">⚠️ ${esc(s.error)}</div>` : ""}</li>`).join("")}</ol></div>`).join("") || '<div class="hint">Задач пока нет.</div>';
    clearTimeout(Y.t); if (st.tasks.some((t) => t.running) && !$("m-yanka").classList.contains("hidden")) Y.t = setTimeout(yLoad, 3000);
    const D = {allowed: "✅ можно", approved: "✅ Яна разрешила", rejected: "🚫 Яна отказала", denied: "⛔ запрещено"};
    $("y-jlist").innerHTML = st.journal.map((j) => `<div class="item"><div class="grow"><b>${esc(D[j.decision] || j.decision)}</b> · ${esc(j.brief || j.tool)}<div class="hint">${esc((j.t || "").slice(0, 16).replace("T", " "))} · ${esc(j.who || "")} · ${esc(j.kind || "")}${j.why ? " · " + esc(j.why) : ""}</div></div>${(j.backups || []).length ? `<button class="btn ghost small" data-yundo="${esc(j.id)}">↩️ Вернуть</button>` : ""}</div>`).join("") || '<div class="hint">Янка ещё ничего не делала на ПК.</div>';
    $("y-mkind").innerHTML = Object.entries(st.memory_kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  }
  async function yMemory() {
    try {
      const r = await yapi("memory", {q: $("y-mq").value});
      const K = (Y.state && Y.state.memory_kinds) || {};
      $("y-mlist").innerHTML = r.items.map((m) => `<div class="item"><div class="grow">${m.pinned ? "📌 " : ""}<span class="tag">${esc(K[m.kind] || m.kind)}</span> ${esc(m.text)}<div class="hint">${esc(m.updated.slice(0, 16).replace("T", " "))} · ${esc(m.source || "")}${m.project ? " · " + esc(m.project) : ""}</div></div>
        <button class="btn ghost small" data-ypin="${m.id}" data-on="${m.pinned ? 0 : 1}" title="Закрепить - Янка помнит всегда">${m.pinned ? "📌" : "📍"}</button>
        <button class="btn ghost small" data-yedit="${m.id}">✏️</button><button class="btn ghost small" data-ymdel="${m.id}">🗑️</button></div>`).join("") || '<div class="hint">Пока пусто.</div>';
    } catch (e) { toast(e.message); }
  }
  $("y-mfind").onclick = yMemory;
  $("y-mq").onkeydown = (e) => { if (e.key === "Enter") yMemory(); };
  $("y-madd").onclick = async () => {
    try { await yapi("memory/save", {kind: $("y-mkind").value, text: $("y-mtext").value, project: ""}); $("y-mtext").value = ""; toast("Запомнила 💜"); yMemory(); }
    catch (e) { toast(e.message); }
  };
  $("y-padd").onclick = async () => {
    try {
      await yapi("provider", {id: $("y-pid").value, model: $("y-pmodel").value.trim(), key: $("y-pkey").value.trim(), enabled: true});
      $("y-pkey").value = ""; toast("Сохранила 🔐 Пока лимит $0, Янка всё равно в облако не ходит"); yLoad();
    } catch (e) { toast(e.message); }
  };
  $("y-bsave").onclick = async () => {
    const day = +$("y-bday").value || 0, month = +$("y-bmonth").value || 0;
    if ((day > 0 || month > 0) && !confirm(`Разрешить Янке тратить до $${day} в день и $${month} в месяц на облачные модели?`)) return;
    try { await yapi("budget", {daily_usd: day, monthly_usd: month}); toast("Лимит сохранён"); yLoad(); } catch (e) { toast(e.message); }
  };
  $("y-tplan").onclick = async () => {
    const goal = $("y-tgoal").value.trim(); if (!goal) return;
    $("y-tplan").disabled = true; toast("🧠 Янка составляет план… 10–30 секунд");
    try { const r = await yapi("task/plan", {goal, project: current}); $("y-tgoal").value = ""; toast(`План готов: ${r.steps} шагов. Нажми «▶️ Выполнить»`); yLoad(); }
    catch (e) { toast(e.message); }
    $("y-tplan").disabled = false;
  };

  $("y-refresh").onclick = async () => { try { await yapi("local"); } catch (e) { toast(e.message); } yLoad(); };
  $("y-unload").onclick = async () => { try { await yapi("unload"); toast("Видеокарта свободна 🎮"); } catch (e) { toast(e.message); } yLoad(); };
  $("m-yanka").addEventListener("click", async (e) => {
    const d = e.target.dataset;
    try {
      if (d.yload) { toast("Загружаю модель… это 10–30 секунд"); await yapi("start", {model: d.yload}); toast("Готово 🐑"); }
      else if (d.yprov) await yapi("provider", {id: d.yprov, enabled: d.on === "1"});
      else if (d.yprm) { if (!confirm("Убрать провайдера вместе с ключом?")) return; await yapi("provider/remove", {id: d.yprm}); }
      else if (d.ytrun) { await yapi("task/run", {id: d.ytrun}); toast("▶️ Янка взялась за задачу"); }
      else if (d.ytstop) await yapi("task/stop", {id: d.ytstop});
      else if (d.ytdone) await yapi("task/done", {id: d.ytdone});
      else if (d.ytdel) { if (!confirm("Удалить задачу из списка?")) return; await yapi("task/delete", {id: d.ytdel}); }
      else if (d.yundo) { if (!confirm("Вернуть файлы как было до этого действия?")) return; const r = await yapi("undo", {id: d.yundo}); toast((r.result || []).join(" ") || "Вернула ✨"); }
      else if (d.ymdel) { if (!confirm("Забыть это?")) return; await yapi("memory/delete", {id: d.ymdel}); return yMemory(); }
      else if (d.ypin) { await yapi("memory/save", {id: d.ypin, pinned: d.on === "1"}); return yMemory(); }
      else if (d.yedit) {
        const cur = e.target.closest(".item").querySelector(".grow").childNodes;
        const old = [...cur].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
        const text = prompt("Поправь запись:", old); if (text === null) return;
        await yapi("memory/save", {id: d.yedit, text}); return yMemory();
      } else return;
      yLoad();
    } catch (er) { toast(er.message); }
  });

  $("btn-bridge").onclick = async () => {
    const on = !S.bridge.on;
    try { await api("/api/bridge", {on}); toast(on ? "🔌 Мост и туннель включены - Клодик может работать с ПК" : "⏹ Мост и туннель выключены. Включатся сами, когда напишешь Клодику"); }
    catch (e) { toast(e.message); }
  };

  $("btn-quit").onclick = async () => {
    if (!confirm("Выключить CozyCode? Клодик не сможет работать с ПК, пока приложение выключено.")) return;
    await api("/api/quit", {}).catch(() => {});
    $("off").classList.remove("hidden");
    if (!window.pywebview) setTimeout(() => window.close(), 1500);
  };

  setInterval(() => { if (mood === "idle" && !document.hidden) say("idle"); }, 45000);
  poll();
  ta.focus();
})();
