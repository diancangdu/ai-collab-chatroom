const connEl = document.getElementById("conn");
const messagesEl = document.getElementById("messages");
const form = document.getElementById("sendForm");
const whoEl = document.getElementById("who");
const textEl = document.getElementById("text");
const projectSel = document.getElementById("projectSel");
const commanderSel = document.getElementById("commanderSel");
const memberCodex = document.getElementById("memberCodex");
const memberZCode = document.getElementById("memberZCode");
const memberOpenCode = document.getElementById("memberOpenCode");
const controlDlg = document.getElementById("controlDlg");
const panelBtn = document.getElementById("panelBtn");
const closePanel = document.getElementById("closePanel");
const serviceStatus = document.getElementById("serviceStatus");
const startupContract = document.getElementById("startupContract");
const projectInput = document.getElementById("projectInput");
const portInput = document.getElementById("portInput");
const autoStartApps = document.getElementById("autoStartApps");
const codexAppInput = document.getElementById("codexAppInput");
const zcodeAppInput = document.getElementById("zcodeAppInput");
const opencodeAppInput = document.getElementById("opencodeAppInput");
const rosterStatus = document.getElementById("rosterStatus");
const rosterBox = document.getElementById("rosterBox");
const rosterTasksPre = document.getElementById("rosterTasksPre");
const sessionsPre = document.getElementById("sessionsPre");
const saveConfigBtn = document.getElementById("saveConfig");
const shutdownBtn = document.getElementById("shutdownBtn");
const imageBtn = document.getElementById("imageBtn");
const imageInput = document.getElementById("imageInput");

const AVATAR_TEXT = {
  boss: "大",
  second: "二",
  third: "三",
  user: "你",
  system: "系",
};

let project = new URLSearchParams(location.search).get("project") || "cs2";
let lastId = 0;
let nearBottom = true;
let pendingImage = null;

const ROSTER_LABELS = {
  Codex: "大哥 Codex",
  ZCode: "二哥 ZCode",
  OpenCode: "三弟 OpenCode",
};

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
    '<div class="bubble">' + escapeHtml(m.text) +
    (m.image ? '<a class="chat-image-link" href="' + escapeHtml(m.image) + '" target="_blank" rel="noopener"><img class="chat-image" src="' + escapeHtml(m.image) + '" alt="' + escapeHtml(m.image_name || "图片") + '"></a>' : "") +
    "</div></div>";
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

function setImage(file) {
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) {
    alert("图片不能超过 8MB");
    return;
  }
  pendingImage = file;
  imageBtn.textContent = file.name || "图";
  imageBtn.title = "待发送：" + (file.name || "图片");
}

function clearImage() {
  pendingImage = null;
  imageInput.value = "";
  imageBtn.textContent = "图";
  imageBtn.title = "发送图片";
}

async function uploadImage() {
  const body = await pendingImage.arrayBuffer();
  const params = new URLSearchParams({
    name: whoEl.value,
    filename: pendingImage.name || "image.png",
    text: textEl.value.trim(),
  });
  const res = await fetch(apiUrl("/api/upload") + "&" + params.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body,
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "上传失败");
  return data.message;
}

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

async function refreshControl() {
  try {
    const [serviceRes, configRes, sessionRes, rosterRes, workloadRes, commanderRes] = await Promise.all([
      fetch("/api/service", { cache: "no-store" }),
      fetch("/api/config", { cache: "no-store" }),
      fetch("/api/sessions", { cache: "no-store" }),
      fetch("/api/roster", { cache: "no-store" }),
      fetch(apiUrl("/api/workload"), { cache: "no-store" }),
      fetch(apiUrl("/api/commander"), { cache: "no-store" }),
    ]);
    const service = await serviceRes.json();
    const config = await configRes.json();
    const sessions = await sessionRes.json();
    const roster = await rosterRes.json();
    const workload = await workloadRes.json();
    const commander = await commanderRes.json();
    if (!service.ok || !config.ok || !sessions.ok || !roster.ok || !workload.ok || !commander.ok) return;

    const state = service.state || {};
    const children = state.children || [];
    const childPids = children.map((item) => item.pid).join(", ") || "无";
    serviceStatus.textContent = service.running
      ? "后台隐藏服务运行中 · PID " + state.server_pid
      : "后台隐藏服务未托管（聊天室仍可运行）";
    startupContract.textContent = state.auto_connect_on_start
      ? "开工自动连接：已启用 · " + (state.server_url || "http://127.0.0.1:8787")
      : "开工自动连接：已停用";

    if (document.activeElement !== projectInput) projectInput.value = config.config.project || "";
    if (document.activeElement !== portInput) portInput.value = config.config.port || 8787;
    autoStartApps.checked = Boolean(config.config.auto_start_apps);
    if (document.activeElement !== codexAppInput) codexAppInput.value = config.config.codex_app || "";
    if (document.activeElement !== zcodeAppInput) zcodeAppInput.value = config.config.zcode_app || "";
    if (document.activeElement !== opencodeAppInput) opencodeAppInput.value = config.config.opencode_app || "";
    renderCommander(commander.commander || "Codex");
    renderRoster(roster.active_agents || []);
    renderRosterTasks(workload.state || {});
    sessionsPre.textContent = JSON.stringify({
      sessions: sessions.agents,
      service: { server_pid: state.server_pid, project: state.project, children: childPids },
    }, null, 2);
  } catch (err) {
    serviceStatus.textContent = "控制信息读取失败";
    sessionsPre.textContent = String(err);
  }
}

function renderCommander(name) {
  if (!commanderSel.options.length) {
    for (const agent of Object.keys(ROSTER_LABELS)) {
      const option = document.createElement("option");
      option.value = agent;
      option.textContent = ROSTER_LABELS[agent];
      commanderSel.appendChild(option);
    }
  }
  commanderSel.value = name;
  const defaults = { Codex: "执行位", ZCode: "技术参谋", OpenCode: "执行者" };
  const members = { Codex: memberCodex, ZCode: memberZCode, OpenCode: memberOpenCode };
  for (const [agent, member] of Object.entries(members)) {
    member.querySelector("small").textContent = agent === name ? "总指挥" : defaults[agent];
  }
}

async function saveCommander() {
  const res = await fetch(apiUrl("/api/commander"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ commander: commanderSel.value }),
  });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "保存失败");
    return;
  }
  commanderSel.value = data.commander;
}

function renderRosterTasks(state) {
  const tasks = Object.values(state.tasks || {}).filter((task) =>
    ["open", "claimed", "supporting", "paused"].includes(task.status));
  rosterTasksPre.textContent = JSON.stringify(tasks, null, 2);
}

function renderRoster(activeAgents) {
  if (document.activeElement && rosterBox.contains(document.activeElement)) return;
  rosterBox.innerHTML = "";
  for (const name of Object.keys(ROSTER_LABELS)) {
    const label = document.createElement("label");
    label.className = "roster-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = name;
    checkbox.checked = activeAgents.includes(name);
    checkbox.addEventListener("change", saveRoster);
    label.appendChild(checkbox);
    label.appendChild(document.createTextNode(ROSTER_LABELS[name]));
    rosterBox.appendChild(label);
  }
  rosterStatus.textContent = activeAgents.length
    ? "开工中：" + activeAgents.map((name) => ROSTER_LABELS[name]).join("、")
    : "已暂停：没有兄弟纳入开工";
}

async function saveRoster() {
  const active = [...rosterBox.querySelectorAll("input:checked")].map((item) => item.value);
  const res = await fetch("/api/roster", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active_agents: active }),
  });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "保存失败");
    return;
  }
  renderRoster(data.active_agents || []);
}

async function saveConfig() {
  const body = {
    project: projectInput.value.trim() || "AllAgentStudy",
    port: Number(portInput.value || 8787),
    auto_start_apps: autoStartApps.checked,
    codex_app: codexAppInput.value.trim(),
    zcode_app: zcodeAppInput.value.trim(),
    opencode_app: opencodeAppInput.value.trim(),
  };
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  alert(data.ok ? "已保存。端口或项目变更需要重启后台服务。" : data.error || "保存失败");
}

async function shutdownService() {
  if (!confirm("确认强制关闭隐藏服务器？三兄弟桥接也会一起停止。")) return;
  const res = await fetch("/api/shutdown", { method: "POST" });
  const data = await res.json();
  alert(data.ok ? "已发送强制关闭命令。" : data.error || "关闭失败");
  serviceStatus.textContent = "已请求强制关闭";
}

projectSel.addEventListener("change", () => {
  project = projectSel.value || "cs2";
  const url = new URL(location.href);
  url.searchParams.set("project", project);
  history.replaceState(null, "", url);
  document.title = "三模型聊天室 · " + project;
  messagesEl.innerHTML = "";
  lastId = 0;
  refresh();
  refreshControl();
});

panelBtn.addEventListener("click", () => {
  controlDlg.showModal();
  refreshControl();
});
closePanel.addEventListener("click", () => controlDlg.close());
saveConfigBtn.addEventListener("click", saveConfig);
shutdownBtn.addEventListener("click", shutdownService);
commanderSel.addEventListener("change", saveCommander);

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
  if (!text && !pendingImage) return;
  try {
    const data = pendingImage ? await uploadImage() : await (await fetch(apiUrl("/api/send"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: whoEl.value, text }),
    })).json();
    if (data.ok) {
      clearImage();
      textEl.value = "";
      textEl.style.height = "auto";
      refresh();
    }
  } catch (err) {
    connEl.className = "conn offline";
    connEl.lastChild.textContent = "离线";
  }
});

imageBtn.addEventListener("click", () => imageInput.click());
imageInput.addEventListener("change", () => setImage(imageInput.files[0]));

document.addEventListener("paste", (e) => {
  const file = Array.from(e.clipboardData?.files || []).find((item) => item.type.startsWith("image/"));
  if (file) setImage(file);
});

document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  const file = Array.from(e.dataTransfer?.files || []).find((item) => item.type.startsWith("image/"));
  if (file) setImage(file);
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
refreshControl();
setInterval(refresh, 1000);
setInterval(() => {
  if (controlDlg.open) refreshControl();
}, 2000);
