// The side panel's views besides the activity feed: what's being worked on, Claude Code sessions, Claude usage,
// projects, the PC, and what Jarvis remembers. Loaded after the main script; uses its TOKEN, T, PLAIN, agents,
// esc() and openDrawer(). Each view is fetched from /insights when shown, and refreshed while it stays open.
(() => {
  const VIEWS = [["feed", "Feed"], ["tasks", "Tasks"], ["sessions", "Sessions"], ["usage", "Usage"],
                 ["projects", "Projects"], ["system", "PC"], ["memory", "Memory"]];
  const REFRESH = { tasks: 5000, sessions: 8000, usage: 30000, projects: 30000, system: 3000, memory: 20000 };
  const aside = document.querySelector("aside"), nav = document.getElementById("views"), panel = document.getElementById("panel");
  let view = "feed", timer = null, wide = false;
  const opened = new Set();   // <details> left open, kept open across refreshes

  const get = tab => fetch(`/insights?tab=${tab}&token=${encodeURIComponent(TOKEN)}`).then(r => r.json());
  const n = x => x >= 1e6 ? (x / 1e6).toFixed(x >= 1e7 ? 0 : 1) + "M" : x >= 1e3 ? (x / 1e3).toFixed(x >= 1e4 ? 0 : 1) + "k" : String(x || 0);
  const color = s => (T.status && T.status[s]) || "var(--accent)";
  const pctColor = p => p >= 90 ? "var(--bad)" : p >= 70 ? "var(--you)" : "var(--good)";
  const h3 = (title, note = "") => `<h3>${esc(title)}${note ? `<small>${note}</small>` : ""}</h3>`;
  const none = text => `<div class="none">${esc(text)}</div>`;
  const kpi = (value, label, c) => `<div class="kpi"${c ? ` style="--c:${c}"` : ""}><b>${value}</b><span>${esc(label)}</span></div>`;
  const bar = (p, c) => `<div class="bar" style="--c:${c || pctColor(p)}"><i style="width:${Math.max(0, Math.min(100, p))}%"></i></div>`;
  const details = (key, summary, body) =>
    `<details data-key="${esc(key)}"${opened.has(key) ? " open" : ""}><summary>${summary}</summary>${body}</details>`;
  const KIND = { coder: "Coder", reviewer: "Reviewer", standing: "★ Always on", agent: "Agent" };
  function when(iso) {   // "today 12:00", "tomorrow 8:30", "Tue 9:00"
    const d = new Date(iso), now = new Date(), days = Math.round((new Date(d).setHours(0, 0, 0, 0) - new Date(now).setHours(0, 0, 0, 0)) / 864e5);
    const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return days === 0 ? `today ${time}` : days === 1 ? `tomorrow ${time}` : `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
  }
  function resets(value) {   // an ISO time or epoch seconds, as "in 2h 10m"
    if (!value) return "";
    const t = typeof value === "number" ? value * 1000 : Date.parse(value);
    if (!t) return "";
    const mins = Math.round((t - Date.now()) / 6e4);
    if (mins <= 0) return "resets now";
    const h = Math.floor(mins / 60), d = Math.floor(h / 24);
    return "resets in " + (d >= 1 ? `${d}d ${h % 24}h` : h ? `${h}h ${mins % 60}m` : `${mins}m`);
  }
  const elapsed = secs => { const m = Math.floor((Date.now() / 1000 - secs) / 60); return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`; };

  // ---------- views ----------
  const RENDER = {
    tasks(d) {
      const needs = d.agents.filter(a => a.status === "needs you").length + d.approvals;
      const next = d.scheduled[0];
      let html = `<div class="kpis">${kpi(d.agents.filter(a => ["working", "starting"].includes(a.status)).length, "agents working", color("working"))}` +
        kpi(d.background.length, "background tasks") + kpi(needs, "need you", needs ? "var(--bad)" : "") +
        kpi(d.scheduled.length ? esc(when(next.when).replace(/^today /, "")) : "—", next ? `next: ${next.agent}` : "nothing scheduled") + "</div>";
      html += h3("Working on now", `${d.agents.length} active`);
      html += d.agents.length ? d.agents.map(a => `<div class="card click" data-agent="${esc(a.id)}" style="--c:${color(a.status)}">
          <div class="top"><span class="dotc ${["working", "starting", "needs you"].includes(a.status) ? "live" : ""}"></span><b>${esc(a.name)}</b>
          <span class="tag soft">${esc(KIND[a.kind] || a.kind)}</span><span class="meta">${esc(PLAIN[a.status] || a.status)}</span>
          <span class="meta" style="margin-left:auto">${a.steps} steps · ${elapsed(a.started)}</span></div>
          <div class="clamp">${esc(a.task)}</div>
          ${a.doing ? `<div class="meta clamp">↳ ${esc(a.doing)}</div>` : ""}
          ${a.repo ? `<div class="meta">⎇ ${esc(a.repo)} · ${esc(a.branch)}</div>` : ""}</div>`).join("")
        : none("No agents are working. Ask Jarvis to put a team on something.");
      if (d.background.length) {
        html += h3("Background tasks");
        html += d.background.map(t => `<div class="card" style="--c:var(--accent)"><div class="top"><span class="dotc live"></span><b>${esc(t.title)}</b>
          <span class="meta" style="margin-left:auto">since ${esc(t.started)} · ${t.steps} steps</span></div><div class="meta clamp">${esc(t.task)}</div></div>`).join("");
      }
      html += h3("Coming up", "scheduled jobs");
      html += d.scheduled.length ? `<table>${d.scheduled.map(j => `<tr><td style="white-space:nowrap">${esc(when(j.when))}</td><td><b>${esc(j.agent)}</b>
          <div class="meta clamp">${esc(j.task)}</div></td></tr>`).join("")}</table>` : none("No scheduled jobs.");
      if (d.reminders.length) html += h3("Reminders") + `<table>${d.reminders.map(r => `<tr><td style="white-space:nowrap">${esc(when(r.due))}</td><td>${esc(r.message)}</td></tr>`).join("")}</table>`;
      const done = [...d.finished.map(a => ({ name: a.name, status: a.status, what: a.task })),
                    ...d.background_done.map(t => ({ name: t.title, status: t.status, what: `background · ${t.finished}` }))];
      if (done.length) html += h3("Finished recently") + done.map(f => `<div class="card" style="--c:${color(f.status === "cancelled" ? "stopped" : f.status)}">
          <div class="top"><b>${esc(f.name)}</b><span class="meta">${esc(PLAIN[f.status] || f.status)}</span></div><div class="meta clamp">${esc(f.what)}</div></div>`).join("");
      return html;
    },

    sessions(d) {
      const mine = d.processes.filter(p => !p.jarvis), jarvis = d.processes.filter(p => p.jarvis);
      let html = `<div class="kpis">${kpi(d.active.length, "your sessions active", d.active.length ? "var(--good)" : "")}` +
        kpi(mine.length, "Claude Code windows open") + kpi(jarvis.length, "Jarvis Claude processes") +
        kpi(d.agents_working.length, "agents thinking", color("working")) + "</div>";
      const row = s => `<div class="card" style="--c:${s.active ? "var(--good)" : "var(--line)"}">
          <div class="top">${s.active ? '<span class="dotc live" style="--c:var(--good)"></span>' : ""}<b class="clamp">${esc(s.title)}</b></div>
          <div class="meta">📁 ${esc(s.project)}${s.branch ? ` · ⎇ ${esc(s.branch)}` : ""} · ${esc(s.model.replace(/^claude-/, ""))}</div>
          <div class="meta">last activity ${esc(s.last)} · started ${esc(s.started)} · ${s.prompts} prompts · ${n(s.tokens_today)} tokens today (${n(s.tokens)} total)</div></div>`;
      html += h3("Your Claude Code, active now") + (d.active.length ? d.active.map(row).join("") : none("No Claude Code session has been active in the last 15 minutes."));
      html += h3("Jarvis's Claude sessions", "one per agent at work");
      const team = [...d.agents_working.map(a => ({ who: a.name, what: a.doing || a.task, kind: KIND[a.kind] || a.kind })),
                    ...d.background.map(b => ({ who: b.title, what: "background task", kind: "Background" }))];
      html += team.length ? team.map(t => `<div class="card" style="--c:${color("working")}"><div class="top"><span class="dotc live"></span><b>${esc(t.who)}</b>
          <span class="tag soft">${esc(t.kind)}</span></div><div class="meta clamp">${esc(t.what)}</div></div>`).join("") : none("Only Jarvis's own conversation is open.");
      if (d.processes.length) {
        html += h3("Claude Code processes on this PC");
        html += `<table><tr><th>Who</th><th>Folder</th><th class="num">Up</th><th class="num">RAM</th></tr>${d.processes.map(p =>
          `<tr class="${p.jarvis ? "mine" : ""}"><td>${esc(p.who)}</td><td class="meta">${esc(p.folder ? p.folder.split(/[\\/]/).slice(-2).join("/") : p.jarvis ? "Jarvis" : "")}</td>
           <td class="num">${esc(p.uptime)}</td><td class="num">${p.memory_mb} MB</td></tr>`).join("")}</table>`;
      }
      html += h3("Recent sessions", "last 8 days") + (d.recent.length ? d.recent.map(row).join("") : none("No other Claude Code sessions recently."));
      return html;
    },

    usage(d) {
      const caveman = r => {
        const until = new Date(r.end * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
        const row = (c, title) => {
          const a = c.caveman, b = c.normal, pct = v => `${v > 0 ? "+" : ""}${Math.round(v)}%`;
          return `<div class="card"><div class="top"><b>${esc(title)}</b><span class="meta" style="margin-left:auto">${esc(c.verdict)}</span></div>
            <div class="meta">${a.turns && b.turns ? `per turn $${a.cost.toFixed(4)} with vs $${b.cost.toFixed(4)} without (${pct(c.change)}) ·
            output ${n(Math.round(a.out))} vs ${n(Math.round(b.out))} tokens (${pct(c.out_change)}) · ` : ""}${a.turns} vs ${b.turns} turns</div></div>`;
        };
        return h3("Caveman test", r.done ? "finished" : `running until ${esc(until)}`) +
          row(r.all, "Everything") + row(r.jarvis, "Your chats with Jarvis") + row(r.agents, "Agents and tasks");
      };
      let html = h3("Your Claude plan");
      const windows = (d.plan && d.plan.windows) || [];
      if (windows.length) html += windows.map(w => `<div class="card" style="--c:${pctColor(w.percent)}"><div class="top"><b>${esc(w.name)}</b>
          <span style="margin-left:auto;font-weight:800">${w.percent}%</span></div>${bar(w.percent)}<div class="meta">${esc(resets(w.resets))}</div></div>`).join("");
      else html += `<div class="none">Plan limits aren't available: ${esc((d.plan && d.plan.error) || "unknown")}.</div>`;
      for (const [kind, l] of Object.entries(d.limits || {})) {
        const until = typeof l.resetsAt === "number" ? l.resetsAt * 1000 : Date.parse(l.resetsAt || "");
        if (l.status === "rejected" && until > Date.now()) html += `<div class="card" style="--c:var(--bad)"><b>Limit reached: ${esc(kind.replace(/_/g, " "))}</b>
          <div class="meta">${esc(l.status)} · ${esc(resets(l.resetsAt))}</div></div>`;
      }
      const t = d.totals;
      html += h3("Tokens used", "input + output");
      html += `<div class="kpis">${kpi(n(t.jarvis.today), "Jarvis + agents today", "var(--accent)")}${kpi(n(t.you.today), "your Claude Code today")}` +
        `${kpi(n(t.jarvis["5h"] + t.you["5h"]), "last 5 hours")}${kpi(n(t.jarvis.week + t.you.week), "last 7 days")}</div>`;
      const max = Math.max(1, ...d.days.map((_, i) => d.by_day.jarvis[i] + d.by_day.you[i]));
      html += `<div class="chart">${d.days.map((day, i) => `<div class="col" title="${esc(day)}: Jarvis ${n(d.by_day.jarvis[i])}, you ${n(d.by_day.you[i])}">
          <i style="height:${d.by_day.you[i] / max * 100}%;background:var(--accent);opacity:.4"></i><i style="height:${d.by_day.jarvis[i] / max * 100}%;background:var(--accent)"></i>
          <em>${esc(day)}</em></div>`).join("")}</div>
        <div class="key"><span style="--c:var(--accent)">Jarvis &amp; agents</span><span class="faint" style="--c:var(--accent)">Your Claude Code</span></div>`;
      if (d.caveman) html += caveman(d.caveman);
      html += h3("Who used it", "this week");
      const top = Math.max(1, ...d.by_who.map(w => w.week));
      html += d.by_who.length ? d.by_who.map(w => `<div class="card"><div class="top"><b>${esc(w.who)}</b><span class="meta" style="margin-left:auto">
          ${n(w.today)} today · ${n(w.week)} this week · ${w.turns} turns today</span></div>${bar(w.week / top * 100, "var(--accent)")}</div>`).join("")
        : none("Jarvis hasn't used Claude since this update (it's counted from now on).");
      return html;
    },

    projects(list) {
      if (!list.length) return none("No projects yet.");
      const KINDS = { jarvis: "Jarvis", repo: "Code repo", project: "Project", claude: "Claude Code" };
      return h3("Projects", `${list.length}`) + list.map(p => {
        const g = p.git || {}, live = p.agents.length || p.sessions.some(s => s.active);
        let body = `<div class="meta">📁 ${esc(p.folder)}</div>`;
        if (p.description) body += `<div class="meta">${esc(p.description)}</div>`;
        const facts = [];
        if (g.branch) facts.push(`⎇ ${esc(g.branch)}`);
        if (p.git) facts.push(`${g.week} commit${g.week === 1 ? "" : "s"} this week`);
        if (g.changed) facts.push(`${g.changed} uncommitted change${g.changed === 1 ? "" : "s"}`);
        if (p.session_count) facts.push(`${p.session_count} Claude Code session${p.session_count === 1 ? "" : "s"} (last ${esc(p.last_session)})`);
        if (facts.length) body += `<div style="margin:4px 0">${facts.join(" · ")}</div>`;
        if (p.agents.length) body += `<div class="top" style="margin:4px 0">${p.agents.map(a => `<span class="tag" style="--c:${color(a.status)}" title="${esc(a.task)}">${esc(a.name)} · ${esc(PLAIN[a.status] || a.status)}</span>`).join("")}</div>`;
        for (const s of p.sessions.filter(s => s.active)) body += `<div class="meta">● now in Claude Code: ${esc(s.title)}</div>`;
        if (p.prs && p.prs.length) body += details(p.name + ":prs", `<b>${p.prs.length} open pull request${p.prs.length === 1 ? "" : "s"}</b>`,
          `<ul>${p.prs.map(pr => `<li><a href="${esc(pr.url)}" target="_blank" rel="noopener">#${pr.number}</a> ${esc(pr.title)} ${pr.draft ? '<span class="tag soft">draft</span>' : ""}${pr.team ? ' <span class="tag soft">agents</span>' : ""}</li>`).join("")}</ul>`);
        if (g.commits && g.commits.length) body += details(p.name + ":log", `Recent commits <span class="meta">(latest ${esc(g.commits[0].ago)})</span>`,
          `<ul>${g.commits.map(c => `<li><code>${esc(c.sha)}</code> ${esc(c.subject)} <span class="meta">· ${esc(c.author)}, ${esc(c.ago)}</span></li>`).join("")}</ul>`);
        if (g.agent_branches && g.agent_branches.length) body += `<div class="meta">Agent branches: ${g.agent_branches.map(esc).join(", ")}</div>`;
        if (p.commands.length) body += `<div class="meta">Commands Jarvis can run: ${p.commands.map(esc).join(", ")}</div>`;
        if (p.sessions.length && !p.sessions.some(s => s.active)) body += `<div class="meta">Last Claude Code session: ${esc(p.sessions[0].title)} (${esc(p.sessions[0].last)})</div>`;
        return `<div class="card" style="--c:${live ? "var(--good)" : g.week ? "var(--accent)" : "var(--line)"}"><div class="top">${live ? '<span class="dotc live" style="--c:var(--good)"></span>' : ""}
          <b>${esc(p.name)}</b><span class="tag soft">${esc(KINDS[p.kind] || p.kind)}</span></div>${body}</div>`;
      }).join("");
    },

    system(d) {
      let html = `<div class="kpis">${kpi(Math.round(d.cpu) + "%", `CPU · ${d.cores} cores`, pctColor(d.cpu))}${kpi(Math.round(d.memory.percent) + "%", `RAM · ${d.memory.used_gb} of ${d.memory.total_gb} GB`, pctColor(d.memory.percent))}` +
        (d.disk ? kpi(Math.round(d.disk.percent) + "%", `disk · ${d.disk.free_gb} GB free`, pctColor(d.disk.percent)) : "") +
        (d.battery ? kpi(d.battery.percent + "%", d.battery.plugged ? "battery, charging" : "battery", d.battery.percent < 20 ? "var(--bad)" : "") : "") +
        kpi(esc(d.uptime), "since restart") + kpi(d.jarvis_memory_mb >= 1024 ? (d.jarvis_memory_mb / 1024).toFixed(1) + " GB" : d.jarvis_memory_mb + " MB", "RAM Jarvis uses") + "</div>";
      const table = (rows, sortBy) => `<table><tr><th>Process</th><th class="num">CPU</th><th class="num">RAM</th></tr>${rows.map(r =>
        `<tr class="${r.jarvis ? "mine" : ""}"><td>${esc(r.label ? `${r.label} (${r.name})` : r.name)} <span class="meta">${r.pid}</span></td>
         <td class="num">${r.cpu.toFixed(1)}%</td><td class="num">${n(r.memory_mb)} MB</td></tr>`).join("")}</table>`;
      html += h3("Jarvis's processes", "Jarvis, the team service, Claude Code and tools") + table(d.jarvis);
      html += `<div class="row2"><div>${h3("Busiest (CPU)")}${table(d.top_cpu)}</div><div>${h3("Biggest (memory)")}${table(d.top_memory)}</div></div>`;
      return html + `<div class="meta" style="margin-top:8px">${d.processes} processes running · CPU is measured between refreshes</div>`;
    },

    memory(d) {
      let html = h3("Things Jarvis remembers", `${d.facts.length}`);
      html += d.facts.length ? `<div class="card"><ul>${d.facts.map(f => `<li>${esc(f.fact)} <span class="meta">· ${esc(f.added || "")}</span></li>`).join("")}</ul></div>`
        : none('Nothing yet. Say "remember that..." and it shows up here.');
      const lists = Object.entries(d.lists || {});
      if (lists.length) html += h3("Lists") + lists.map(([name, items]) => `<div class="card">${details("list:" + name, `<b>${esc(name)}</b> <span class="meta">${items.length} items</span>`,
        `<ul>${items.map(i => `<li>${esc(i)}</li>`).join("")}</ul>`)}</div>`).join("");
      if (d.reminders.length) html += h3("Reminders") + `<div class="card"><ul>${d.reminders.map(r => `<li>${esc(when(r.due))}: ${esc(r.message)}</li>`).join("")}</ul></div>`;
      html += h3("Agents' notebooks", "their long-term memory");
      html += d.notebooks.map(b => `<div class="card">${details("nb:" + b.agent, `<b>${esc(b.agent)}</b> <span class="meta">${b.chars ? `${n(b.chars)} characters · updated ${esc(b.updated)}` : "empty so far"}</span>`,
        b.text ? `<pre>${esc(b.text)}</pre>` : none("Nothing written yet."))}</div>`).join("");
      const tr = d.trackers, jobs = Object.entries(tr.jobs || {});
      html += h3("Trackers");
      html += `<div class="kpis">${kpi(d.conversation_turns, "messages in Jarvis's recent memory")}${kpi(jobs.reduce((s, [, c]) => s + c, 0), "jobs tracked")}` +
        `${kpi(tr.trips.length, "trips saved")}${kpi(tr.holdings, "holdings")}</div>`;
      if (jobs.length) html += `<div class="meta">Jobs: ${jobs.map(([s, c]) => `${c} ${esc(s)}`).join(" · ")}</div>`;
      if (tr.trips.length) html += `<div class="meta">Trips: ${tr.trips.map(t => `${esc(t.name)} (${esc(t.status)})`).join(" · ")}</div>`;
      return html;
    },
  };

  // ---------- switching and refreshing ----------
  function renderNav() {
    nav.innerHTML = VIEWS.map(([key, label]) => `<button data-view="${key}" class="${key === view ? "on" : ""}">${label}</button>`).join("");
  }
  async function load() {
    clearTimeout(timer);
    if (view === "feed") return;
    const shown = view;
    try {
      const data = await get(shown);
      if (shown !== view) return;
      if (data && data.error && !Array.isArray(data)) panel.innerHTML = `<div class="err">Couldn't load this: ${esc(data.error)}</div>`;
      else {
        const top = panel.scrollTop;
        panel.innerHTML = `<div class="ptools">${TOKEN ? "" : '<form method="post" action="/logout"><button class="grow">Sign out</button></form>'}` +
          `<button class="grow" data-grow="1">${wide ? "⇥ narrower" : "⇤ wider"}</button></div>` + RENDER[shown](data);
        panel.scrollTop = top;
      }
    } catch (e) {
      if (shown === view) panel.innerHTML = `<div class="err">Couldn't reach Jarvis (${esc(e.message)}). Retrying…</div>`;
    }
    if (shown === view && !document.hidden) timer = setTimeout(load, REFRESH[shown]);
  }
  function show(next) {
    view = next;
    try { localStorage.setItem("jarvis-view", view); } catch (e) {}
    aside.classList.toggle("panel", view !== "feed");
    aside.classList.toggle("big", wide && view !== "feed");
    if (view !== "feed") panel.innerHTML = '<div class="none">Loading…</div>';
    renderNav(); load();
    window.dispatchEvent(new Event("resize"));   // the map's width may have changed
  }
  nav.addEventListener("click", ev => { const b = ev.target.closest("button"); if (b) show(b.dataset.view); });
  panel.addEventListener("click", ev => {
    if (ev.target.closest("[data-grow]")) {
      wide = !wide;
      try { localStorage.setItem("jarvis-panel-wide", wide ? "1" : "0"); } catch (e) {}
      return show(view);
    }
    const card = ev.target.closest("[data-agent]");
    if (card && agents[card.dataset.agent]) openDrawer(card.dataset.agent);
  });
  panel.addEventListener("toggle", ev => {
    const key = ev.target.dataset && ev.target.dataset.key;
    if (key) ev.target.open ? opened.add(key) : opened.delete(key);
  }, true);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  document.addEventListener("keydown", ev => {   // shift + 1-7 picks a view
    if (ev.target.tagName === "INPUT" || !ev.shiftKey) return;
    const i = "!@#$%^&".indexOf(ev.key);
    if (i >= 0 && VIEWS[i]) { ev.preventDefault(); show(VIEWS[i][0]); }
  });

  // the header chip: how much of the plan's tightest window is used
  const chip = document.getElementById("claude-chip");
  chip.addEventListener("click", () => show("usage"));
  async function updateChip() {
    try {
      const s = await get("summary"), w = s.window;
      if (w) {
        chip.style.display = "inline-flex";
        chip.innerHTML = `Claude <span class="mini" style="--c:${pctColor(w.percent)}"><i style="width:${Math.min(100, w.percent)}%"></i></span><b>${Math.round(w.percent)}%</b>`;
        chip.title = `${w.name}: ${w.percent}% used, ${resets(w.resets)} (click for details)`;
      } else if (s.today) {
        chip.style.display = "inline-flex";
        chip.innerHTML = `Claude today <b>${n(s.today)}</b>`;
        chip.title = "Tokens used today by Jarvis, its agents and your Claude Code (click for details)";
      }
    } catch (e) {}
    setTimeout(updateChip, 60000);
  }

  try { wide = localStorage.getItem("jarvis-panel-wide") === "1"; view = localStorage.getItem("jarvis-view") || "feed"; } catch (e) {}
  if (!VIEWS.some(([k]) => k === view)) view = "feed";
  show(view);
  updateChip();
})();
