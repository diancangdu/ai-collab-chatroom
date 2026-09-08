#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || index + 1 >= process.argv.length) {
    throw new Error(`missing_argument_${name.replace(/^--/, "")}`);
  }
  return process.argv[index + 1];
}

async function findPage(port, sessionId) {
  const response = await fetch(`http://127.0.0.1:${port}/json/list`);
  if (!response.ok) throw new Error(`cdp_http_${response.status}`);
  const pages = await response.json();
  const page = pages.find(item => item.type === "page" && item.url.includes(sessionId));
  if (!page) throw new Error("cdp_page_not_found");
  return page.webSocketDebuggerUrl;
}

async function main() {
  const port = Number(argumentValue("--port"));
  const sessionFile = argumentValue("--session-file");
  const cwd = argumentValue("--cwd");
  const prompt = argumentValue("--prompt");
  const imagePath = process.argv.includes("--image") ? process.argv[process.argv.indexOf("--image") + 1] : null;
  const timeoutMs = Number(argumentValue("--timeout-ms") || "90000");
  const returnAfterSend = process.argv.includes("--return-after-send");
  const sessionId = fs.readFileSync(sessionFile, "ascii").trim();
  const socketUrl = await findPage(port, sessionId);
  const socket = new WebSocket(socketUrl);
  globalThis.__qoderBridgeSocket = socket;
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    const deferred = pending.get(message.id);
    if (!deferred) return;
    pending.delete(message.id);
    if (message.error) deferred.reject(new Error(message.error.message));
    else deferred.resolve(message.result);
  });
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", () => reject(new Error("cdp_websocket_failed")), { once: true });
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  await call("Runtime.enable");
  let attachments = [];
  if (imagePath) {
    const preview = await call("Runtime.evaluate", {
      expression: `window.qoderDesktop.previewChatImageAttachment(${JSON.stringify({ path: imagePath })})`,
      awaitPromise: true,
      returnByValue: true,
    });
    if (preview.exceptionDetails) throw new Error("qoder_image_preview_failed");
    attachments = [{
      id: crypto.randomUUID(),
      kind: "image",
      name: path.basename(imagePath),
      path: imagePath,
      size: fs.statSync(imagePath).size,
      mimeType: "image/png",
      previewDataUrl: preview.result.value.previewDataUrl,
      source: "picker",
      status: "ready",
    }];
  }
  await call("Runtime.evaluate", {
    expression: "window.__qoderBridgeBatches = []; window.qoderDesktop.onChatSessionMessageStreamBatch(batch => window.__qoderBridgeBatches.push(batch));",
    returnByValue: true,
  });
  await call("Runtime.evaluate", {
    expression: `window.qoderDesktop.subscribeChatSessionMessageStream(${JSON.stringify(sessionId)}, ${JSON.stringify(crypto.randomUUID())});`,
    awaitPromise: true,
    returnByValue: true,
  });
  const expression = `window.qoderDesktop.sendChatMessage(${JSON.stringify({
    sessionId,
    agentId: "agent:default",
    cwd,
    prompt,
    attachments,
    selectedSkillNames: [],
    selectedPluginIds: [],
    selectedConnectorIds: [],
    selectedAgentNames: [],
    selectedCapabilityCommands: [],
    referencedChatSessions: [],
  })})`;
  const result = await call("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(`qoder_send_failed:${JSON.stringify(result.exceptionDetails).slice(0, 2000)}`);

  if (returnAfterSend) {
    const sendDeadline = Date.now() + Math.min(timeoutMs, 30000);
    while (Date.now() < sendDeadline) {
      const scan = await call("Runtime.evaluate", {
        expression: "(() => [...window.__qoderBridgeBatches.flatMap(batch => batch.events)].reverse().find(event => event.type === 'message-upsert' && event.metadata.role === 'user') || null)()",
        returnByValue: true,
      });
      const userEvent = scan.result.value;
      if (userEvent?.metadata?.turnId) {
        process.stdout.write(JSON.stringify({
          sent: true,
          turnId: userEvent.metadata.turnId,
        }));
        socket.close();
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error("qoder_sent_turn_timeout");
  }

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const scan = await call("Runtime.evaluate", {
      expression: "(() => window.__qoderBridgeBatches.flatMap(batch => batch.events).find(event => event.type === 'message-upsert' && event.metadata.role === 'assistant' && event.metadata.status === 'completed') || null)()",
      returnByValue: true,
    });
    const completedEvent = scan.result.value;
    if (completedEvent) {
      const turnId = completedEvent.metadata.turnId;
      const history = await call("Runtime.evaluate", {
        expression: `window.qoderDesktop.loadChatHistoryAround(${JSON.stringify(sessionId)}, ${JSON.stringify(cwd)}, ${JSON.stringify(turnId)}, 10, 9)`,
        awaitPromise: true,
        returnByValue: true,
      });
      const message = (history.result.value?.messages || []).find(item => item.turnId === turnId && item.role === "assistant" && String(item.text || "").trim());
      if (message) {
        process.stdout.write(JSON.stringify({ turnId, text: String(message.text).trim().slice(0, 1200) }));
        await call("Runtime.evaluate", {
          expression: `window.qoderDesktop.unsubscribeChatSessionMessageStream(${JSON.stringify(sessionId)}, "${completedEvent.streamId || ""}")`,
          awaitPromise: true,
          returnByValue: true,
        });
        socket.close();
        return;
      }
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error("qoder_reply_timeout");
}

main().catch(error => {
  if (globalThis.__qoderBridgeSocket) globalThis.__qoderBridgeSocket.close();
  process.stderr.write(`${error.message || error}\n`);
  process.exitCode = 1;
});
