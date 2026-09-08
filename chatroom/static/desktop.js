(() => {
  "use strict";

  const state = { project: "", since: 0, sending: false, stream: null, streamLive: false, flushing: false };
  const $ = (id) => document.getElementById(id);
  const elements = {
    projectSel: $("projectSel"), agentCards: $("agentCards"), taskSummary: $("taskSummary"),
    taskList: $("taskList"), queueSummary: $("queueSummary"), serviceSummary: $("serviceSummary"), projectTitle: $("projectTitle"),
    projectMeta: $("projectMeta"), commander: $("commander"), conn: $("conn"), messages: $("messages"),
    sendForm: $("sendForm"), who: $("who"), text: $("text"), sendBtn: $("sendBtn"),
    imageBtn: $("imageBtn"), imageInput: $("imageInput"), toast: $("toast"), refreshBtn: $("refreshBtn")
  };
  const agents = [
    { key: "Codex", label: "大哥 Codex", role: "总指挥", cls: "boss" },
    { key: "ZCode", label: "二哥 ZCode", role: "技术参谋", cls: "second" },
    { key: "OpenCode", label: "三弟 OpenCode", role: "执行者", cls: "third" }
    ,{ key: "Qoder", label: "四哥 Qoder", role: "执行者", cls: "fourth" }
  ];

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));

  const setConn = (online) => {
    elements.conn.className = online ? "conn online" : "conn offline";
    elements.conn.innerHTML = `<i class="dot"></i>${online ? "已连接" : "连接异常"}`;
  };

  const toast = (text) => {
    elements.toast.textContent = text;
    elements.toast.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { elements.toast.hidden = true; }, 2600);
  };

  const jsonFetch = async (url, options = {}) => {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  };

  const currentProject = () => {
    const params = new URLSearchParams(location.search);
    return chatutilNormalize(params.get("project") || localStorage.getItem("chatroom.project") || "allagentstudy");
  };

  const chatutilNormalize = (value) => {
    const normalized = String(value || "").trim().toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff_-]+/g, "-").replace(/^-+|-+$/g, "");
    return normalized || "allagentstudy";
  };

  const setProject = (project, pushUrl = true) => {
    state.project = chatutilNormalize(project);
    localStorage.setItem("chatroom.project", state.project);
    elements.projectSel.value = state.project;
    if (!elements.projectSel.value) {
      const option = document.createElement("option");
      option.value = state.project;
      option.textContent = state.project;
      elements.projectSel.append(option);
      elements.projectSel.value = state.project;
    }
    elements.projectTitle.textContent = state.project;
    elements.projectMeta.textContent = "本地 · http://127.0.0.1:8787";
    if (pushUrl) {
      const params = new URLSearchParams({ project: state.project });
      history.replaceState(null, "", `/desktop?${params.toString()}`);
    }
  };

  const roleClass = (name) => {
    if (name === "Codex") return "boss";
    if (name === "ZCode") return "second";
    if (name === "OpenCode") return "third";
    if (name === "Qoder") return "fourth";
    if (name === "system") return "system";
    return "user";
  };

  const renderMessage = (message) => {
    const item = document.createElement("article");
    item.className = `msg ${roleClass(message.name)}`;
    if (message.name === "你") item.classList.add("me");
    const link = message.image ? `<a href="${esc(message.image)}" target="_blank" rel="noreferrer"><img src="${esc(message.image)}" alt="聊天附件"></a>` : "";
    item.innerHTML = `
      <div class="meta"><b>${esc(message.name)}</b><time>${esc(message.ts)}</time></div>
      <div class="bubble">${esc(message.text)}${link}</div>
    `;
    elements.messages.append(item);
  };

  const renderAgents = (workload) => {
    const agentStates = workload?.agents || {};
    elements.agentCards.innerHTML = agents.map(({ key, label, role, cls }) => {
      const item = agentStates[key] || {};
      const online = item.presence === "online";
      return `
        <div class="agent ${cls}">
          <span class="avatar">${key.slice(0, 1)}</span>
          <span><b>${label}</b><small>${item.status || "未知"} · ${online ? "在线" : "离线"} · ${role}</small></span>
        </div>
      `;
    }).join("");
  };

  const renderTasks = (workload) => {
    const tasks = Object.values(workload?.tasks || {});
    const open = tasks.filter((task) => task.status !== "done").length;
    elements.taskSummary.textContent = `总任务 ${tasks.length} · 未完成 ${open}`;
    elements.taskList.innerHTML = tasks.slice(-6).reverse().map((task) => `
      <div class="task ${esc(task.status)}">
        <b>#${esc(task.id)} · ${esc(task.owner)}</b>
        <div>${esc(task.title || "未命名任务")}</div>
      </div>
    `).join("") || `<div class="summary">暂无任务</div>`;
  };

  const queueKey = () => `chatroom.queue.${state.project}`;
  const readQueue = () => {
    try { return JSON.parse(localStorage.getItem(queueKey()) || "[]"); } catch { return []; }
  };
  const writeQueue = (items) => localStorage.setItem(queueKey(), JSON.stringify(items));
  const updateQueueSummary = () => {
    elements.queueSummary.textContent = `离线队列 ${readQueue().length}`;
  };

  const flushQueue = async () => {
    if (state.flushing) return;
    state.flushing = true;
    try {
      let queue = readQueue();
      while (queue.length) {
        const item = queue[0];
        await jsonFetch(`/api/send?project=${encodeURIComponent(state.project)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(item)
        });
        queue = queue.slice(1);
        writeQueue(queue);
        updateQueueSummary();
        await loadMessages();
      }
    } catch {
    } finally {
      state.flushing = false;
      updateQueueSummary();
    }
  };

  const loadProjects = async () => {
    const config = await jsonFetch("/api/config");
    const projects = await jsonFetch("/api/projects");
    const known = [...new Set([...(projects.projects || []), "allagentstudy", "main", config.config.project || ""].filter(Boolean))];
    elements.projectSel.innerHTML = known.map((project) => `<option value="${esc(project)}">${esc(project)}</option>`).join("");
    setProject(currentProject(), false);
  };

  const loadMessages = async (reset = false) => {
    if (reset) state.since = 0;
    const data = await jsonFetch(`/api/messages?project=${encodeURIComponent(state.project)}&since=${state.since}`);
    if (reset) elements.messages.innerHTML = "";
    for (const message of data.messages || []) renderMessage(message);
    if ((data.messages || []).length) {
      state.since = Math.max(state.since, ...data.messages.map((message) => message.id || 0));
      elements.messages.scrollTop = elements.messages.scrollHeight;
    }
  };

  const loadWorkload = async () => {
    const data = await jsonFetch(`/api/workload?project=${encodeURIComponent(state.project)}`);
    const workload = data.state;
    renderAgents(workload);
    renderTasks(workload);
    elements.commander.textContent = `指挥：${workload?.commander || "Codex"}`;
  };

  const loadService = async () => {
    const data = await jsonFetch("/api/service");
    const running = data.running ? "运行中" : "停止";
    const auto = data.auto_release_disabled ? "自动解除已关闭" : "自动解除开启";
    elements.serviceSummary.textContent = `${running}\n${auto}`;
  };

  const refresh = async (reset = false) => {
    try {
      await Promise.all([(state.streamLive && !reset ? Promise.resolve() : loadMessages(reset)), loadWorkload(), loadService()]);
      setConn(true);
      await flushQueue();
    } catch (error) {
      setConn(false);
      toast(`通信失败：${error.message}`);
    }
  };

  const sendMessage = async (event) => {
    event.preventDefault();
    const text = elements.text.value.trim();
    if (!text || state.sending) return;
    state.sending = true;
    elements.sendBtn.disabled = true;
    try {
      await jsonFetch(`/api/send?project=${encodeURIComponent(state.project)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: elements.who.value, text })
      });
      elements.text.value = "";
      await loadMessages();
    } catch (error) {
      const queue = readQueue();
      queue.push({ name: elements.who.value, text });
      writeQueue(queue);
      updateQueueSummary();
      elements.text.value = "";
      toast(`发送失败：${error.message}`);
    } finally {
      state.sending = false;
      elements.sendBtn.disabled = false;
    }
  };

  const startStream = () => {
    if (!window.EventSource || state.stream) return;
    const source = new EventSource(`/api/stream?project=${encodeURIComponent(state.project)}&since=${state.since}`);
    state.stream = source;
    source.addEventListener("message", (event) => {
      state.streamLive = true;
      const data = JSON.parse(event.data);
      if (data.id > state.since) {
        renderMessage(data);
        state.since = data.id;
        elements.messages.scrollTop = elements.messages.scrollHeight;
      }
    });
    source.addEventListener("open", () => {
      state.streamLive = true;
      setConn(true);
    });
    source.addEventListener("error", () => {
      state.streamLive = false;
      source.close();
      state.stream = null;
      setTimeout(startStream, 2000);
    });
  };

  const sendImage = async (file) => {
    if (!file) return;
    const query = new URLSearchParams({ project: state.project, name: elements.who.value, filename: file.name, text: "" });
    try {
      await fetch(`/api/upload?${query.toString()}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
      await loadMessages();
    } catch (error) {
      toast(`图片发送失败：${error.message}`);
    }
  };

  elements.projectSel.addEventListener("change", () => {
    if (state.stream) {
      state.stream.close();
      state.stream = null;
      state.streamLive = false;
    }
    setProject(elements.projectSel.value);
    refresh(true);
    startStream();
  });
  elements.sendForm.addEventListener("submit", sendMessage);
  elements.imageBtn.addEventListener("click", () => elements.imageInput.click());
  elements.imageInput.addEventListener("change", () => {
    sendImage(elements.imageInput.files[0]);
    elements.imageInput.value = "";
  });
  elements.refreshBtn.addEventListener("click", () => refresh(true));
  elements.text.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      elements.sendForm.requestSubmit();
    }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  refresh(true);
  startStream();
  setInterval(() => refresh(), 2000);
})();
