import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import os from "node:os";
import readline from "node:readline";
import { createRequire } from "node:module";

let chromium;
try {
  ({ chromium } = await import("playwright-core"));
} catch {
  const moduleBase = process.env.TEAMNOT_PLAYWRIGHT_REQUIRE_FROM
    || `file:///C:/Users/${os.userInfo().username}/OpenClawTools/browser-control.mjs`;
  const requireFromOpenClawTools = createRequire(moduleBase);
  ({ chromium } = requireFromOpenClawTools("playwright-core"));
}

const args = process.argv.slice(2);
const getArg = (name, fallback = "") => {
  const i = args.indexOf(name);
  return i >= 0 ? (args[i + 1] ?? fallback) : fallback;
};
const hasArg = (name) => args.includes(name);

const cdp = getArg("--cdp", process.env.TEAMNOT_CDP_URL || "http://127.0.0.1:18801");
const browserName = getArg("--browser", "chrome").toLowerCase();
const sessionId = getArg("--session-id", `teamnot-${Date.now()}`);
const safeSessionId = sessionId.replace(/[^a-zA-Z0-9_.-]+/g, "-");

let browser = null;
let context = null;
let page = null;
let rawMode = false;
let rawFallbackReason = "";
let rawTarget = null;
let rawSocket = null;
let rawCounter = 0;
const rawPending = new Map();

function write(obj) {
  process.stdout.write(`${JSON.stringify(obj)}\n`);
}

function cdpVersionUrl() {
  return cdp.replace(/\/$/, "") + "/json/version";
}

function cdpBaseUrl() {
  return cdp.replace(/\/$/, "");
}

async function probeCdp() {
  try {
    const res = await fetch(cdpVersionUrl(), { signal: AbortSignal.timeout(1500) });
    if (!res.ok) return { ok: false, status: res.status };
    return { ok: true, info: await res.json() };
  } catch (err) {
    return { ok: false, error: String(err?.message || err) };
  }
}

function browserExe() {
  if (browserName === "brave") return "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe";
  if (browserName === "edge") return "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe";
  return "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
}

function cdpPort() {
  try {
    return new URL(cdp).port || "18800";
  } catch {
    return "18800";
  }
}

const userDataDir = getArg(
  "--user-data-dir",
  path.join(os.homedir(), "OpenClawTools", `teamnot-chrome-cdp-profile-${cdpPort()}-${safeSessionId}`),
);

function profileDirForReport(ensured) {
  return ensured?.started?.userDataDir ? userDataDir : "";
}

async function startBrowser() {
  await fs.mkdir(userDataDir, { recursive: true });
  const child = spawn(browserExe(), [
    "--remote-debugging-address=127.0.0.1",
    `--remote-debugging-port=${cdpPort()}`,
    `--user-data-dir=${userDataDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "about:blank",
  ], { detached: true, stdio: "ignore" });
  child.unref();
  return { pid: child.pid, userDataDir };
}

async function ensureCdp() {
  let probe = await probeCdp();
  if (probe.ok) return { started: false, probe };
  const started = await startBrowser();
  const deadline = Date.now() + Number(getArg("--startup-timeout-ms", "12000"));
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    probe = await probeCdp();
    if (probe.ok) return { started, probe };
  }
  throw new Error(`Browser started but CDP did not become ready: ${JSON.stringify({ cdp, started, lastProbe: probe })}`);
}

async function ensureSession() {
  const ensured = await ensureCdp();
  if (!browser) {
    try {
      if (hasArg("--cleanup-targets")) await cleanupCdpTargets().catch(() => {});
      browser = await chromium.connectOverCDP(cdp, {
        timeout: Number(getArg("--connect-timeout-ms", "15000")),
      });
    } catch (err) {
      if (!isConnectFallbackError(err)) throw err;
      rawFallbackReason = String(err?.message || err).slice(0, 500);
      await ensureRawSession(ensured);
      return ensured;
    }
  }
  context = browser.contexts()[0] || await browser.newContext();
  if (!page || page.isClosed()) {
    page = await context.newPage();
    await page.addInitScript((id) => {
      window.__TEAMNOT_BROWSER_SESSION_ID__ = id;
    }, sessionId).catch(() => {});
    await page.goto("about:blank", { waitUntil: "domcontentloaded", timeout: 10000 }).catch(() => {});
  }
  return ensured;
}

async function currentPage() {
  await ensureSession();
  return page;
}

function isConnectFallbackError(err) {
  const text = String(err?.message || err).toLowerCase();
  return text.includes("connectovercdp")
    || text.includes("timeout")
    || text.includes("connection closed")
    || text.includes("websocket");
}

function socketRequest(socket, method, params = {}, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const id = Math.floor(Math.random() * 1_000_000_000);
    const timer = setTimeout(() => reject(new Error(`${method} timed out after ${timeoutMs}ms`)), timeoutMs);
    const onMessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== id) return;
      clearTimeout(timer);
      socket.removeEventListener("message", onMessage);
      if (message.error) reject(new Error(`${method} failed: ${message.error.message || JSON.stringify(message.error)}`));
      else resolve(message);
    };
    socket.addEventListener("message", onMessage);
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function withBrowserSocket(fn) {
  const ready = await ensureCdp();
  const browserWs = ready?.probe?.info?.webSocketDebuggerUrl;
  if (!browserWs) return null;
  const socket = new WebSocket(browserWs);
  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = reject;
  });
  try {
    return await fn(socket);
  } finally {
    socket.close();
  }
}

async function cleanupCdpTargets() {
  return await withBrowserSocket(async (socket) => {
    const targets = await socketRequest(socket, "Target.getTargets").then((msg) => msg.result?.targetInfos || []);
    let keptBlankPage = false;
    const closable = targets.filter((target) => {
      if (target.type === "service_worker") return true;
      if (target.type !== "page") return false;
      const url = String(target.url || "");
      if (url === "about:blank" && !keptBlankPage) {
        keptBlankPage = true;
        return false;
      }
      return true;
    });
    let closed = 0;
    for (const target of closable) {
      await socketRequest(socket, "Target.closeTarget", { targetId: target.targetId }, 3000).catch(() => {});
      closed += 1;
    }
    if (!targets.some((target) => target.type === "page" && String(target.url || "") === "about:blank")) {
      await fetch(`${cdpBaseUrl()}/json/new?${encodeURIComponent("about:blank")}`, { method: "PUT" }).catch(() => {});
    }
    return { inspected: targets.length, closed };
  });
}

async function ensureRawSession(ensured = null) {
  rawMode = true;
  if (rawSocket && rawSocket.readyState === WebSocket.OPEN) return ensured || await ensureCdp();
  const ready = ensured || await ensureCdp();
  const tabRes = await fetch(`${cdpBaseUrl()}/json/new?${encodeURIComponent("about:blank")}`, { method: "PUT" });
  if (!tabRes.ok) throw new Error(`Raw CDP page could not be created: ${tabRes.status} ${tabRes.statusText}`);
  rawTarget = await tabRes.json();
  rawSocket = new WebSocket(rawTarget.webSocketDebuggerUrl);
  rawSocket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && rawPending.has(msg.id)) {
      rawPending.get(msg.id)(msg);
      rawPending.delete(msg.id);
    }
  });
  await new Promise((resolve, reject) => {
    rawSocket.onopen = resolve;
    rawSocket.onerror = reject;
  });
  await rawSend("Runtime.enable");
  await rawSend("Page.enable");
  await rawSend("Network.enable").catch(() => {});
  await rawSend("Runtime.evaluate", {
    expression: `window.__TEAMNOT_BROWSER_SESSION_ID__ = ${JSON.stringify(sessionId)}`,
    returnByValue: true,
  }).catch(() => {});
  return ready;
}

function rawSend(method, params = {}, timeoutMs = 15000) {
  if (!rawSocket || rawSocket.readyState !== WebSocket.OPEN) {
    return Promise.reject(new Error("raw CDP socket is not open"));
  }
  return new Promise((resolve) => {
    const id = ++rawCounter;
    const timer = setTimeout(() => {
      if (!rawPending.has(id)) return;
      rawPending.delete(id);
      resolve({ error: { message: `${method} timed out after ${timeoutMs}ms in raw CDP fallback` } });
    }, timeoutMs);
    rawPending.set(id, (message) => {
      clearTimeout(timer);
      resolve(message);
    });
    rawSocket.send(JSON.stringify({ id, method, params }));
  }).then((response) => {
    if (response.error) throw new Error(`${method} failed: ${response.error.message || JSON.stringify(response.error)}`);
    return response;
  });
}

async function rawEvaluate(expr, timeout = 3000) {
  await new Promise((resolve) => setTimeout(resolve, Math.min(timeout, 3000)));
  const response = await rawSend("Runtime.evaluate", {
    expression: expr,
    returnByValue: true,
    awaitPromise: true,
  });
  return response.result?.result?.value;
}

async function rawPageInfo() {
  const value = await rawEvaluate(`(() => ({ url: location.href, title: document.title }))()`, 250);
  return value || { url: rawTarget?.url || "", title: rawTarget?.title || "" };
}

function normalizeCookieForCdp(cookie) {
  const normalized = {
    name: String(cookie.name || ""),
    value: String(cookie.value || ""),
  };
  if (cookie.url) normalized.url = String(cookie.url);
  if (cookie.domain) normalized.domain = String(cookie.domain);
  if (cookie.path) normalized.path = String(cookie.path);
  if (cookie.expires !== undefined && cookie.expires !== null) normalized.expires = Number(cookie.expires);
  if (cookie.httpOnly !== undefined) normalized.httpOnly = Boolean(cookie.httpOnly);
  if (cookie.secure !== undefined) normalized.secure = Boolean(cookie.secure);
  if (cookie.sameSite) normalized.sameSite = String(cookie.sameSite);
  return normalized;
}

async function rawSetLocalStorageEntries(entries) {
  let applied = 0;
  for (const entry of entries) {
    if (!entry.origin || !entry.values || typeof entry.values !== "object") continue;
    await rawSend("Page.navigate", { url: entry.origin });
    await new Promise((resolve) => setTimeout(resolve, 1000));
    await rawSend("Runtime.evaluate", {
      expression: `(() => {
        const values = ${JSON.stringify(entry.values)};
        for (const [key, value] of Object.entries(values)) localStorage.setItem(key, String(value));
        return true;
      })()`,
      returnByValue: true,
      awaitPromise: true,
    });
    applied += Object.keys(entry.values).length;
  }
  return applied;
}

async function rawImportStorageState(command) {
  if (!command.path) throw new Error("Missing path");
  let parsed;
  try {
    parsed = JSON.parse(await fs.readFile(command.path, "utf8"));
  } catch (err) {
    return {
      ok: false,
      action: "importStorageState",
      cdp,
      sessionId,
      rawCdpFallback: true,
      unsupportedBlocker: `storageState could not be read safely: ${String(err?.message || err).slice(0, 240)}`,
    };
  }
  const cookies = Array.isArray(parsed.cookies) ? parsed.cookies.map(normalizeCookieForCdp).filter((c) => c.name) : [];
  const origins = Array.isArray(parsed.origins) ? parsed.origins : [];
  if (cookies.length) await rawSend("Network.setCookies", { cookies });
  const localStorageValuesApplied = await rawSetLocalStorageEntries(
    origins.map((origin) => ({
      origin: origin.origin,
      values: Object.fromEntries((origin.localStorage || []).map((item) => [item.name, item.value])),
    })),
  );
  const info = await rawPageInfo();
  return {
    ok: true,
    action: "importStorageState",
    cdp,
    sessionId,
    rawCdpFallback: true,
    seededStateApplied: cookies.length > 0 || localStorageValuesApplied > 0,
    cookiesApplied: cookies.length,
    localStorageValuesApplied,
    url: info.url,
  };
}

async function rawPerformLogin(command) {
  if (!command.email) throw new Error("Missing email");
  if (!command.password) throw new Error("Missing password");
  const targetUrl = command.loginUrl || (await rawPageInfo()).url || "about:blank";
  await rawSend("Page.navigate", { url: targetUrl });
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const before = await rawPageInfo();
  const filled = await rawEvaluate(`(({ email, password }) => {
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const inputs = Array.from(document.querySelectorAll("input,textarea")).filter(visible);
    const labelFor = (el) => [
      el.getAttribute("type") || "",
      el.getAttribute("name") || "",
      el.getAttribute("id") || "",
      el.getAttribute("autocomplete") || "",
      el.getAttribute("placeholder") || "",
      el.getAttribute("aria-label") || "",
    ].join(" ").toLowerCase();
    const emailInput = inputs.find((el) => {
      const label = labelFor(el);
      return label.includes("email") || label.includes("username") || el.type === "email";
    }) || inputs[0];
    const passwordInput = inputs.find((el) => {
      const label = labelFor(el);
      return label.includes("password") || el.type === "password";
    });
    const setValue = (el, value) => {
      if (!el) return false;
      el.focus();
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    };
    return {
      emailFilled: setValue(emailInput, email),
      passwordFilled: setValue(passwordInput, password),
      inputCount: inputs.length,
    };
  })(${JSON.stringify({ email: command.email, password: command.password })})`, 500);
  if (!filled?.emailFilled || !filled?.passwordFilled) {
    const info = await rawPageInfo();
    return {
      ok: false,
      action: "login",
      cdp,
      sessionId,
      rawCdpFallback: true,
      seededStateApplied: false,
      unsupportedBlocker: `Login form could not be filled in raw CDP fallback: emailFilled=${Boolean(filled?.emailFilled)}, passwordFilled=${Boolean(filled?.passwordFilled)}, inputCount=${filled?.inputCount || 0}`,
      url: info.url,
      title: info.title,
    };
  }
  const submitted = await rawEvaluate(`(() => {
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const controls = Array.from(document.querySelectorAll("button,input[type=submit],input[type=button],[role=button]")).filter(visible);
    const submitter = controls.find((el) => {
      const text = String(el.innerText || el.value || "") + " " + String(el.getAttribute("aria-label") || "");
      return /log.?in|sign.?in|continue|submit|start|register|join/i.test(text);
    }) || controls[0];
    if (submitter) {
      submitter.click();
      return { clicked: true, label: submitter.innerText || submitter.value || submitter.getAttribute("aria-label") || "" };
    }
    const form = document.querySelector("form");
    if (form) {
      form.requestSubmit ? form.requestSubmit() : form.submit();
      return { clicked: false, submittedForm: true };
    }
    return { clicked: false, submittedForm: false };
  })()`, 500);
  await new Promise((resolve) => setTimeout(resolve, Math.min(Number(command.timeout || 30000), 8000)));
  const after = await rawPageInfo();
  const authState = await rawEvaluate(`(() => {
    const text = (document.body?.innerText || "").slice(0, 3000);
    const passwordInputs = document.querySelectorAll('input[type="password"]').length;
    return {
      textSample: text,
      passwordInputs,
      hasLogout: /log out|logout|sign out|signout/i.test(text),
      hasDashboardSignal: /dashboard|settings|account|workspace|profile|team|billing|project|admin/i.test(text),
      hasError: /invalid|incorrect|required|error|failed|try again/i.test(text),
    };
  })()`, 500).catch(() => ({}));
  const successUrl = command.successUrl || "";
  const reachedSuccessUrl = successUrl && after.url.startsWith(successUrl);
  const urlChanged = before.url !== after.url;
  const seededStateApplied = Boolean(
    reachedSuccessUrl
    || (urlChanged && !authState.hasError)
    || authState.hasLogout
    || (authState.hasDashboardSignal && authState.passwordInputs === 0)
  );
  return {
    ok: seededStateApplied,
    action: "login",
    cdp,
    sessionId,
    rawCdpFallback: true,
    seededStateApplied,
    loginAttempted: true,
    loginUrl: targetUrl,
    beforeUrl: before.url,
    afterUrl: after.url,
    beforeTitle: before.title,
    afterTitle: after.title,
    submitted,
    authState,
    workspaceId: command.workspaceId || "",
    unsupportedBlocker: seededStateApplied ? "" : "Login was attempted in raw CDP fallback but authenticated state could not be verified.",
  };
}

async function rawUpload(command) {
  if (!command.selector) throw new Error("Missing selector");
  if (!command.file) throw new Error("Missing file");
  await rawSend("DOM.enable").catch(() => {});
  const documentResult = await rawSend("DOM.getDocument", { depth: -1, pierce: true });
  const rootNodeId = documentResult.result?.root?.nodeId;
  if (!rootNodeId) throw new Error("DOM.getDocument did not return a root node");
  const queryResult = await rawSend("DOM.querySelector", {
    nodeId: rootNodeId,
    selector: command.selector,
  });
  const nodeId = queryResult.result?.nodeId;
  if (!nodeId) {
    return {
      ok: false,
      action: "upload",
      cdp,
      sessionId,
      rawCdpFallback: true,
      unsupportedBlocker: `File input selector not found in raw CDP fallback: ${command.selector}`,
    };
  }
  await rawSend("DOM.setFileInputFiles", {
    nodeId,
    files: [command.file],
  }, Number(command.timeout || 30000));
  const verification = await rawEvaluate(`((selector) => {
    const input = document.querySelector(selector);
    if (!input) return { found: false, fileCount: 0, filenames: [] };
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return {
      found: true,
      fileCount: input.files ? input.files.length : 0,
      filenames: Array.from(input.files || []).map((file) => file.name),
    };
  })(${JSON.stringify(command.selector)})`, 500);
  const info = await rawPageInfo();
  const uploaded = Boolean(verification?.found && verification.fileCount > 0);
  return {
    ok: uploaded,
    action: "upload",
    cdp,
    sessionId,
    rawCdpFallback: true,
    selector: command.selector,
    files: [command.file],
    verification,
    url: info.url,
    title: info.title,
    unsupportedBlocker: uploaded ? "" : `File input did not expose the selected file after DOM.setFileInputFiles for ${command.selector}`,
  };
}

async function rawHandle(command) {
  const action = command.action || "status";
  const ensured = await ensureRawSession();
  if (action === "status") {
    const pages = await fetch(`${cdpBaseUrl()}/json/list`).then((res) => res.json()).catch(() => []);
    const info = await rawPageInfo();
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: profileDirForReport(ensured),
      dedicatedUrl: info.url,
      contexts: 1,
      pages: Array.isArray(pages) ? pages.filter((item) => item.type === "page").length : undefined,
      ensured,
      rawCdpFallback: true,
      fallbackReason: rawFallbackReason,
    };
  }
  if (action === "navigate") {
    if (!command.url) throw new Error("Missing url");
    await rawSend("Page.navigate", { url: command.url });
    await new Promise((resolve) => setTimeout(resolve, Math.min(Number(command.timeout || 30000), 8000)));
    const info = await rawPageInfo();
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: profileDirForReport(ensured),
      url: info.url,
      title: info.title,
      ensured,
      rawCdpFallback: true,
      fallbackReason: rawFallbackReason,
    };
  }
  if (action === "viewport") {
    await rawSend("Emulation.setDeviceMetricsOverride", {
      width: Number(command.width || 390),
      height: Number(command.height || 844),
      deviceScaleFactor: 1,
      mobile: Number(command.width || 390) <= 500,
    });
    const info = await rawPageInfo();
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      viewport: { width: Number(command.width || 390), height: Number(command.height || 844) },
      url: info.url,
      title: info.title,
      ensured,
      rawCdpFallback: true,
      fallbackReason: rawFallbackReason,
    };
  }
  if (action === "eval") {
    if (!command.expr) throw new Error("Missing expr");
    const result = await rawEvaluate(command.expr, Number(command.settleTimeout || 3000));
    const info = await rawPageInfo();
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      result,
      url: info.url,
      title: info.title,
      ensured,
      rawCdpFallback: true,
      fallbackReason: rawFallbackReason,
    };
  }
  if (action === "screenshot") {
    const out = command.out || path.join(process.env.TEMP || "C:\\Windows\\Temp", `teamnot-browser-${Date.now()}.png`);
    await fs.mkdir(path.dirname(out), { recursive: true });
    const capture = await rawSend("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: Boolean(command.fullPage),
    });
    await fs.writeFile(out, Buffer.from(capture.result.data, "base64"));
    const info = await rawPageInfo();
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: profileDirForReport(ensured),
      path: out,
      url: info.url,
      title: info.title,
      ensured,
      method: "raw-cdp-fallback",
      failedPrimitive: "playwright.connectOverCDP",
      fallbackReason: rawFallbackReason,
      rawCdpFallback: true,
    };
  }
  if (action === "cookies") {
    const urls = Array.isArray(command.urls) ? command.urls : [];
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      ensured,
      rawCdpFallback: true,
      cookies: [],
      unsupportedBlocker: `cookie readback is unavailable in raw CDP fallback mode for ${urls.join(", ") || "current page"}`,
    };
  }
  if (action === "importStorageState") {
    return await rawImportStorageState(command);
  }
  if (action === "setCookies") {
    const cookies = Array.isArray(command.cookies) ? command.cookies.map(normalizeCookieForCdp).filter((c) => c.name) : [];
    if (!cookies.length) {
      return { ok: false, action, cdp, sessionId, rawCdpFallback: true, unsupportedBlocker: "No cookies were provided." };
    }
    await rawSend("Network.setCookies", { cookies });
    const info = await rawPageInfo();
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      rawCdpFallback: true,
      seededStateApplied: true,
      cookiesApplied: cookies.length,
      url: info.url,
    };
  }
  if (action === "setLocalStorage") {
    const entries = Array.isArray(command.entries) ? command.entries : [];
    const applied = await rawSetLocalStorageEntries(entries);
    const info = await rawPageInfo();
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      rawCdpFallback: true,
      seededStateApplied: applied > 0,
      localStorageValuesApplied: applied,
      url: info.url,
    };
  }
  if (action === "loginHint") {
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      rawCdpFallback: true,
      seededStateApplied: false,
      loginHintRecorded: true,
      email: command.email || "",
      loginUrl: command.loginUrl || "",
      workspaceId: command.workspaceId || "",
      unsupportedBlocker: "loginHint records account metadata only; automated credential entry is intentionally not performed in raw CDP fallback.",
    };
  }
  if (action === "login") {
    return await rawPerformLogin(command);
  }
  if (action === "upload") {
    return await rawUpload(command);
  }
  if (action === "reset") {
    await rawSend("Page.navigate", { url: "about:blank" });
    return { ok: true, action, cdp, sessionId, rawCdpFallback: true };
  }
  if (action === "close") {
    if (rawTarget?.id) {
      await withBrowserSocket((socket) => socketRequest(socket, "Target.closeTarget", { targetId: rawTarget.id }, 3000)).catch(() => {});
    }
    if (rawSocket) rawSocket.close();
    rawSocket = null;
    rawTarget = null;
    return { ok: true, action, cdp, sessionId, rawCdpFallback: true };
  }
  throw new Error(`Unknown raw CDP action: ${action}`);
}

async function settlePage(p, timeout = 5000) {
  await p.waitForLoadState("domcontentloaded", { timeout }).catch(() => {});
  await p.waitForLoadState("networkidle", { timeout }).catch(() => {});
  await p.waitForFunction(() => {
    const root = document.querySelector("#root, [data-reactroot], main, body");
    const text = (root?.innerText || document.body?.innerText || "").trim();
    return document.readyState !== "loading" && text.length > 0;
  }, { timeout }).catch(() => {});
  await p.waitForTimeout(750).catch(() => {});
}

async function screenshot(p, out, fullPage) {
  await fs.mkdir(path.dirname(out), { recursive: true });
  let retryCount = 0;
  try {
    await p.screenshot({ path: out, fullPage, timeout: 30000, caret: "hide" });
    return { path: out, method: "playwright", retryCount };
  } catch (err) {
    retryCount += 1;
    await settlePage(p, 3000);
    try {
      await p.screenshot({ path: out, fullPage, timeout: 15000, caret: "hide", animations: "disabled" });
      return {
        path: out,
        method: "playwright-retry",
        retryCount,
        fallbackReason: String(err?.message || err).slice(0, 300),
      };
    } catch (retryErr) {
      const client = await p.context().newCDPSession(p);
      const capture = await client.send("Page.captureScreenshot", {
        format: "png",
        fromSurface: true,
        captureBeyondViewport: fullPage,
      });
      await fs.writeFile(out, Buffer.from(capture.data, "base64"));
      return {
        path: out,
        method: "cdp-fallback",
        retryCount,
        failedPrimitive: "playwright.screenshot",
        fallbackReason: String(retryErr?.message || retryErr).slice(0, 300),
      };
    }
  }
}

async function importStorageState(command) {
  if (!command.path) throw new Error("Missing path");
  let parsed;
  try {
    parsed = JSON.parse(await fs.readFile(command.path, "utf8"));
  } catch (err) {
    return {
      ok: false,
      action: "importStorageState",
      cdp,
      sessionId,
      unsupportedBlocker: `storageState could not be read safely: ${String(err?.message || err).slice(0, 240)}`,
    };
  }
  const cookies = Array.isArray(parsed.cookies) ? parsed.cookies : [];
  if (cookies.length) await context.addCookies(cookies);
  const origins = Array.isArray(parsed.origins) ? parsed.origins : [];
  for (const origin of origins) {
    if (!origin.origin || !Array.isArray(origin.localStorage)) continue;
    await pGotoOrigin(origin.origin);
    await page.evaluate((entries) => {
      for (const entry of entries) localStorage.setItem(entry.name, entry.value);
    }, origin.localStorage).catch(() => {});
  }
  return {
    ok: true,
    action: "importStorageState",
    cdp,
    sessionId,
    seededStateApplied: true,
    cookiesApplied: cookies.length,
    localStorageOriginsApplied: origins.length,
    url: page?.url() || "",
  };
}

async function pGotoOrigin(origin) {
  const p = await currentPage();
  await p.goto(origin, { waitUntil: "domcontentloaded", timeout: 10000 }).catch(() => {});
}

async function handle(command) {
  const action = command.action || "status";
  if (rawMode) {
    return await rawHandle(command);
  }
  const ensured = await ensureSession();
  if (rawMode) {
    return await rawHandle(command);
  }
  const p = await currentPage();

  if (action === "status") {
    const pages = browser.contexts().flatMap((ctx) => ctx.pages());
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: profileDirForReport(ensured),
      dedicatedUrl: p.url(),
      contexts: browser.contexts().length,
      pages: pages.length,
      ensured,
    };
  }
  if (action === "navigate") {
    if (!command.url) throw new Error("Missing url");
    let navigationError = "";
    try {
      await p.goto(command.url, { waitUntil: "domcontentloaded", timeout: Number(command.timeout || 30000) });
      await settlePage(p);
    } catch (err) {
      navigationError = String(err?.message || err).slice(0, 500);
      await p.waitForTimeout(750).catch(() => {});
    }
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: profileDirForReport(ensured),
      url: p.url(),
      title: await p.title().catch(() => ""),
      navigationError,
      pages: browser.contexts().flatMap((ctx) => ctx.pages()).length,
      ensured,
    };
  }
  if (action === "screenshot") {
    const out = command.out || path.join(process.env.TEMP || "C:\\Windows\\Temp", `teamnot-browser-${Date.now()}.png`);
    const captured = await screenshot(p, out, Boolean(command.fullPage));
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: profileDirForReport(ensured),
      url: p.url(),
      title: await p.title().catch(() => ""),
      ensured,
      ...captured,
    };
  }
  if (action === "viewport") {
    await p.setViewportSize({ width: Number(command.width || 390), height: Number(command.height || 844) });
    return { ok: true, action, cdp, sessionId, viewport: p.viewportSize(), url: p.url(), title: await p.title().catch(() => ""), ensured };
  }
  if (action === "upload") {
    if (!command.selector) throw new Error("Missing selector");
    if (!command.file) throw new Error("Missing file");
    await p.setInputFiles(command.selector, command.file, { timeout: Number(command.timeout || 30000) });
    return { ok: true, action, cdp, sessionId, selector: command.selector, files: [command.file], url: p.url(), title: await p.title().catch(() => ""), ensured };
  }
  if (action === "cookies") {
    const urls = Array.isArray(command.urls) ? command.urls : [];
    const cookies = await context.cookies(urls.length ? urls : undefined);
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      ensured,
      cookies: cookies.map((c) => ({
        name: c.name,
        domain: c.domain,
        expires: c.expires,
        httpOnly: c.httpOnly,
        secure: c.secure,
        sameSite: c.sameSite,
      })),
    };
  }
  if (action === "importStorageState") {
    return await importStorageState(command);
  }
  if (action === "setCookies") {
    const cookies = Array.isArray(command.cookies) ? command.cookies : [];
    if (!cookies.length) {
      return { ok: false, action, cdp, sessionId, unsupportedBlocker: "No cookies were provided." };
    }
    await context.addCookies(cookies);
    return { ok: true, action, cdp, sessionId, seededStateApplied: true, cookiesApplied: cookies.length, url: p.url() };
  }
  if (action === "setLocalStorage") {
    const entries = Array.isArray(command.entries) ? command.entries : [];
    let applied = 0;
    for (const entry of entries) {
      if (!entry.origin || !entry.values || typeof entry.values !== "object") continue;
      await p.goto(entry.origin, { waitUntil: "domcontentloaded", timeout: 10000 }).catch(() => {});
      await p.evaluate((values) => {
        for (const [key, value] of Object.entries(values)) localStorage.setItem(key, String(value));
      }, entry.values).catch(() => {});
      applied += Object.keys(entry.values).length;
    }
    return { ok: true, action, cdp, sessionId, seededStateApplied: applied > 0, localStorageValuesApplied: applied, url: p.url() };
  }
  if (action === "loginHint") {
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      seededStateApplied: false,
      loginHintRecorded: true,
      email: command.email || "",
      loginUrl: command.loginUrl || "",
      workspaceId: command.workspaceId || "",
      unsupportedBlocker: "loginHint records account metadata only; automated credential entry is intentionally not performed.",
      url: p.url(),
    };
  }
  if (action === "login") {
    return await performLogin(command);
  }
  if (action === "assistLogin") {
    return await performAssistedLogin(command);
  }
  if (action === "eval") {
    if (!command.expr) throw new Error("Missing expr");
    await settlePage(p, Number(command.settleTimeout || 3000));
    const result = await p.evaluate(command.expr);
    return { ok: true, action, cdp, sessionId, result, url: p.url(), title: await p.title().catch(() => ""), ensured };
  }
  if (action === "reset") {
    await p.goto("about:blank", { waitUntil: "domcontentloaded", timeout: 10000 });
    return { ok: true, action, cdp, sessionId };
  }
  if (action === "close") {
    if (page && !page.isClosed()) await page.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    browser = null;
    context = null;
    page = null;
    return { ok: true, action, cdp, sessionId };
  }
  throw new Error(`Unknown action: ${action}`);
}

async function performAssistedLogin(command) {
  const p = await currentPage();
  const targetUrl = command.loginUrl || command.url || p.url();
  if (targetUrl && targetUrl !== "about:blank") {
    await p.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: Number(command.timeout || 30000) }).catch(() => {});
    await settlePage(p);
  }
  const beforeUrl = p.url();
  const beforeState = await authSnapshot(p);
  if (beforeState.authenticated) {
    return {
      ok: true,
      action: "assistLogin",
      cdp,
      sessionId,
      seededStateApplied: true,
      browserContextAuth: true,
      loginAttempted: false,
      reason: "existing browser context already appears authenticated",
      beforeUrl,
      afterUrl: beforeUrl,
      authState: beforeState,
    };
  }
  const loginClick = await clickAuthControl(p, /google|continue with google|sign in with google|login with google|đăng nhập với google|đăng nhập bằng google|log.?in|sign.?in|đăng nhập|tiếp tục/i);
  if (!loginClick.clicked) {
    return {
      ok: true,
      action: "assistLogin",
      cdp,
      sessionId,
      seededStateApplied: false,
      browserContextAuth: false,
      loginAttempted: false,
      reason: "no visible login control was found",
      beforeUrl,
      afterUrl: p.url(),
      authState: beforeState,
    };
  }
  await settlePage(p, 6000);
  await p.waitForTimeout(1000).catch(() => {});
  let intermediateState = await authSnapshot(p);
  let steps = [loginClick];
  for (let i = 0; i < 4 && !intermediateState.authenticated; i += 1) {
    if (intermediateState.passwordInputs > 0 || intermediateState.mfaLikely) {
      return {
        ok: false,
        action: "assistLogin",
        cdp,
        sessionId,
        seededStateApplied: false,
        browserContextAuth: false,
        loginAttempted: true,
        beforeUrl,
        afterUrl: p.url(),
        authState: intermediateState,
        steps,
        unsupportedBlocker: "Browser-assisted login reached a password, MFA, or verification step that requires the user.",
      };
    }
    const accountClick = await clickAuthControl(
      p,
      command.email ? new RegExp(escapeRegExp(command.email), "i") : /@|gmail|google account|continue|tiếp tục|cho phép|allow|đồng ý/i,
    );
    if (!accountClick.clicked) break;
    steps.push(accountClick);
    await settlePage(p, 8000);
    await p.waitForTimeout(1200).catch(() => {});
    intermediateState = await authSnapshot(p);
  }
  const afterState = await authSnapshot(p);
  const successUrl = command.successUrl || command.url || "";
  const afterUrl = p.url();
  const reachedSuccessUrl = successUrl && afterUrl.startsWith(successUrl);
  const seededStateApplied = Boolean(afterState.authenticated || reachedSuccessUrl);
  return {
    ok: seededStateApplied,
    action: "assistLogin",
    cdp,
    sessionId,
    seededStateApplied,
    browserContextAuth: seededStateApplied,
    loginAttempted: true,
    beforeUrl,
    afterUrl,
    steps,
    authState: afterState,
    unsupportedBlocker: seededStateApplied ? "" : "Browser-assisted login did not reach an authenticated state.",
  };
}

async function authSnapshot(p) {
  return await p.evaluate(() => {
    const text = (document.body?.innerText || "").slice(0, 5000);
    const lower = text.toLowerCase();
    const passwordInputs = document.querySelectorAll('input[type="password"]').length;
    const authenticated = /log out|logout|sign out|signout|dashboard|account|profile|settings|my account|workspace|admin|của tôi|tài khoản|đăng xuất|hồ sơ/i.test(text)
      && !/sign in to continue|log in to continue|please log in|please sign in|vui lòng đăng nhập|đăng nhập để/i.test(text);
    return {
      url: location.href,
      title: document.title || "",
      textSample: text,
      passwordInputs,
      authenticated,
      hasLogin: /log.?in|sign.?in|đăng nhập/i.test(text),
      hasGoogle: /google/i.test(text),
      mfaLikely: /2-step|two-step|verification code|verify it's you|mã xác minh|xác minh|otp|authenticator/i.test(lower),
    };
  }).catch(() => ({ authenticated: false, passwordInputs: 0, textSample: "" }));
}

async function clickAuthControl(p, matcher) {
  const controls = await p.locator("button,a,[role=button],input[type=button],input[type=submit],div[role=link]").evaluateAll((items) => items.map((el, index) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return {
      index,
      text: (el.innerText || el.textContent || el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("value") || "").replace(/\s+/g, " ").trim(),
      visible: rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none",
    };
  })).catch(() => []);
  const candidate = controls.find((item) => item.visible && matcher.test(item.text || ""));
  if (!candidate) return { clicked: false };
  await p.locator("button,a,[role=button],input[type=button],input[type=submit],div[role=link]").nth(candidate.index).click({ timeout: 5000 }).catch(() => {});
  return { clicked: true, text: candidate.text };
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function performLogin(command) {
  if (!command.email) throw new Error("Missing email");
  if (!command.password) throw new Error("Missing password");
  const p = await currentPage();
  const targetUrl = command.loginUrl || p.url();
  await p.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: Number(command.timeout || 30000) });
  await settlePage(p);
  const beforeUrl = p.url();
  const beforeTitle = await p.title().catch(() => "");
  const filled = await p.evaluate(({ email, password }) => {
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const inputs = Array.from(document.querySelectorAll("input,textarea")).filter(visible);
    const labelFor = (el) => [
      el.getAttribute("type") || "",
      el.getAttribute("name") || "",
      el.getAttribute("id") || "",
      el.getAttribute("autocomplete") || "",
      el.getAttribute("placeholder") || "",
      el.getAttribute("aria-label") || "",
    ].join(" ").toLowerCase();
    const emailInput = inputs.find((el) => {
      const label = labelFor(el);
      return label.includes("email") || label.includes("username") || el.type === "email";
    }) || inputs[0];
    const passwordInput = inputs.find((el) => {
      const label = labelFor(el);
      return label.includes("password") || el.type === "password";
    });
    const setValue = (el, value) => {
      if (!el) return false;
      el.focus();
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    };
    return {
      emailFilled: setValue(emailInput, email),
      passwordFilled: setValue(passwordInput, password),
      inputCount: inputs.length,
    };
  }, { email: command.email, password: command.password });
  if (!filled.emailFilled || !filled.passwordFilled) {
    return {
      ok: false,
      action: "login",
      cdp,
      sessionId,
      seededStateApplied: false,
      unsupportedBlocker: `Login form could not be filled: emailFilled=${filled.emailFilled}, passwordFilled=${filled.passwordFilled}, inputCount=${filled.inputCount}`,
      url: p.url(),
      title: await p.title().catch(() => ""),
    };
  }
  const submitted = await p.evaluate(() => {
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const controls = Array.from(document.querySelectorAll("button,input[type=submit],input[type=button],[role=button]")).filter(visible);
    const submitter = controls.find((el) => {
      const text = `${el.innerText || el.value || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase();
      return /log.?in|sign.?in|continue|submit|start|register|join/.test(text);
    }) || controls[0];
    if (submitter) {
      submitter.click();
      return { clicked: true, label: submitter.innerText || submitter.value || submitter.getAttribute("aria-label") || "" };
    }
    const form = document.querySelector("form");
    if (form) {
      form.requestSubmit ? form.requestSubmit() : form.submit();
      return { clicked: false, submittedForm: true };
    }
    return { clicked: false, submittedForm: false };
  });
  await p.waitForLoadState("domcontentloaded", { timeout: Number(command.timeout || 30000) }).catch(() => {});
  await p.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await p.waitForTimeout(1000).catch(() => {});
  const afterUrl = p.url();
  const afterTitle = await p.title().catch(() => "");
  const authState = await p.evaluate(() => {
    const text = (document.body?.innerText || "").slice(0, 3000);
    const passwordInputs = document.querySelectorAll('input[type="password"]').length;
    return {
      textSample: text,
      passwordInputs,
      hasLogout: /log out|logout|sign out|signout/i.test(text),
      hasDashboardSignal: /dashboard|settings|account|workspace|profile|team|billing|project|admin/i.test(text),
      hasError: /invalid|incorrect|required|error|failed|try again/i.test(text),
    };
  }).catch(() => ({}));
  const successUrl = command.successUrl || "";
  const reachedSuccessUrl = successUrl && afterUrl.startsWith(successUrl);
  const urlChanged = beforeUrl !== afterUrl;
  const seededStateApplied = Boolean(
    reachedSuccessUrl
    || (urlChanged && !authState.hasError)
    || authState.hasLogout
    || (authState.hasDashboardSignal && authState.passwordInputs === 0)
  );
  return {
    ok: seededStateApplied,
    action: "login",
    cdp,
    sessionId,
    seededStateApplied,
    loginAttempted: true,
    loginUrl: targetUrl,
    beforeUrl,
    afterUrl,
    beforeTitle,
    afterTitle,
    submitted,
    authState,
    workspaceId: command.workspaceId || "",
    unsupportedBlocker: seededStateApplied ? "" : "Login was attempted but authenticated state could not be verified.",
  };
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  let command = {};
  try {
    command = JSON.parse(line);
    const result = await handle(command);
    write({ id: command.id, ...result });
    if (command.action === "close") process.exit(0);
  } catch (err) {
    write({
      id: command.id,
      ok: false,
      action: command.action || "unknown",
      error: String(err?.message || err),
      stack: String(err?.stack || "").split("\n").slice(0, 6).join("\n"),
    });
  }
}
if (browser) {
  await browser.close().catch(() => {});
}
