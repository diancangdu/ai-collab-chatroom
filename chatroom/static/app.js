const connEl = document.getElementById("conn");
const messagesEl = document.getElementById("messages");
const form = document.getElementById("sendForm");
const whoEl = document.getElementById("who");
const textEl = document.getElementById("text");
const projectSel = document.getElementById("projectSel");

const AVATAR_TEXT = {
  boss: "大",
  second: "二",
  third: "三",
  user: "你",
  system: "系",
};

let project = new URLSearchParams(location.search).get("project") || "main";
let lastId = 0;
let nearBottom = true;

function apiUrl(path) {
  const sep = path.includes("?") ? "&" : "?";
  return path + sep + "project=" + encodeURIComponent(project);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function roleOf(name) {
  const n = String(name || "").toLowerCase();
  if (n.startsWith("codex") || n.includes("大哥")) return "boss";
  if (n.startsWith("zcode") || n.includes("二哥")) return "second";
  if (n.startsWith("opencode") || n.includes("三哥")) return "third";
  if (n.startsWith("system") || n.includes("系统")) return "system";
  return "user";
}

function renderMessage(m) {
  const role = m.role || roleOf(m.name);
  const el = document.createElement("div");
  el.className = "msg " + role + (role === "user" ? " me" : "");
  el.dataset.id = m.id;
  el.innerHTML =
    '<span class="avatar">' + escapeHtml(AVATAR_TEXT[role] || "?") + "</span>" +
    '<div><div class="head"><b>' + escapeHtml(m.name || "未知") + "</b>" +
    "<time>" + escapeHtml(m.ts || "") + "</time></div>" +
    '<div class="bubble">' + escapeHtml(m.text) + "</div></div>";
  return el;
}

function renderMessages(messages) {
  const frag = document.createDocumentFragment();
  for (const m of messages) {
    if (m.id && m.id > lastId) {
      frag.appendChild(renderMessage(m));
      lastId = m.id;
    }
  }
  if (frag.childNodes.length > 0) {
    messagesEl.appendChild(frag);
    if (nearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

messagesEl.addEventListener("scroll", () => {
  const gap = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
  nearBottom = gap < 80;
});

async function loadProjects() {
  try {
    const res = await fetch("/api/projects", { cache: "no-store" });
    const data = await res.json();
    if (data.ok) {
      projectSel.innerHTML = "";
      const list = data.projects || [];
      if (!list.includes(project)) list.push(project);
      for (const p of list) {
        const opt = document.createElement("option");
        opt.value = p;
        opt.textContent = p;
        opt.selected = p === project;
        projectSel.appendChild(opt);
      }
      document.title = "三模型聊天室 · " + project;
    }
  } catch (err) {
    // 服务未就绪时保留空选择器，刷新会自动重试
  }
}

projectSel.addEventListener("change", () => {
  project = projectSel.value || "main";
  const url = new URL(location.href);
  url.searchParams.set("project", project);
  history.replaceState(null, "", url);
  document.title = "三模型聊天室 · " + project;
  messagesEl.innerHTML = "";
  lastId = 0;
  refresh();
});

async function refresh() {
  try {
    const res = await fetch(apiUrl("/api/messages") + "&since=" + lastId, { cache: "no-store" });
    const data = await res.json();
    if (data.ok) {
      connEl.className = "conn online";
      connEl.lastChild.textContent = "在线";
      renderMessages(data.messages || []);
    } else {
      connEl.className = "conn offline";
      connEl.lastChild.textContent = "异常";
    }
  } catch (err) {
    connEl.className = "conn offline";
    connEl.lastChild.textContent = "离线";
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = textEl.value.trim();
  if (!text) return;
  try {
    const res = await fetch(apiUrl("/api/send"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: whoEl.value, text }),
    });
    const data = await res.json();
    if (data.ok) {
      textEl.value = "";
      textEl.style.height = "auto";
      refresh();
    }
  } catch (err) {
    connEl.className = "conn offline";
    connEl.lastChild.textContent = "离线";
  }
});

textEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

textEl.addEventListener("input", () => {
  textEl.style.height = "auto";
  textEl.style.height = Math.min(textEl.scrollHeight, 130) + "px";
});

loadProjects();
refresh();
setInterval(refresh, 1000);
