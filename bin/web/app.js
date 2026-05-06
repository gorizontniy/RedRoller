const state = {
  accounts: [],
  status: null,
  editingId: null,
  selectedIsolationAccountId: null,
  isolationSaveToken: 0,
  telegram: null,
};

const $ = (id) => document.getElementById(id);

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 3500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || data.message || "Запрос не выполнен");
  }
  return data;
}

function lineList(value) {
  return String(value || "")
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function selectedZones() {
  return Array.from(document.querySelectorAll("input[name='zones']:checked")).map((item) => item.value);
}

function setZones(zones) {
  const values = new Set(zones && zones.length ? zones : ["ru-central1-a", "ru-central1-e"]);
  document.querySelectorAll("input[name='zones']").forEach((item) => {
    item.checked = values.has(item.value);
  });
}

function selectedRollMode() {
  const checked = document.querySelector("input[name='rollMode']:checked");
  return checked ? checked.value : "";
}

function setRollMode(mode) {
  const value = mode === "project" ? "project" : mode === "cloud" ? "cloud" : "";
  document.querySelectorAll("input[name='rollMode']").forEach((item) => {
    item.checked = item.value === value;
  });
  $("modeConfig").classList.toggle("hidden", !value);
  const projectMode = value === "project";
  document.querySelectorAll(".project-mode-field").forEach((item) => {
    item.classList.toggle("hidden", !projectMode);
  });
  $("cloudModeNote").classList.toggle("hidden", value !== "cloud");
  $("modeSummary").textContent = value === "project" ? "крутка 1 проекта" : value === "cloud" ? "крутка облаков" : "режим не выбран";
  $("targetCloudId").required = projectMode;
  $("folderId").required = projectMode;
  if (!projectMode) {
    $("targetCloudId").value = "";
    $("folderId").value = "";
  }
}

function formPayload() {
  const rollMode = selectedRollMode();
  if (!rollMode) {
    throw new Error("Сначала выберите режим крутки");
  }
  return {
    name: $("name").value,
    roll_mode: rollMode,
    organization_id: $("organizationId").value,
    billing_account_id: $("billingAccountId").value,
    service_cloud_id: $("serviceCloudId").value,
    target_cloud_id: $("targetCloudId").value,
    folder_id: $("folderId").value,
    zones: selectedZones(),
    target_cidrs: lineList($("targetCidrs").value),
    target_ips: lineList($("targetIps").value),
    service_account_json: $("serviceAccountJson").value,
  };
}

function resetForm() {
  state.editingId = null;
  $("accountId").value = "";
  $("accountForm").reset();
  setZones(["ru-central1-a", "ru-central1-e"]);
  setRollMode("");
  $("serviceAccountJson").required = true;
}

function fillForm(account) {
  state.editingId = account.id;
  $("accountId").value = account.id;
  $("name").value = account.name || "";
  setRollMode(account.roll_mode || "cloud");
  $("organizationId").value = account.organization_id || "";
  $("billingAccountId").value = account.billing_account_id || "";
  $("serviceCloudId").value = account.service_cloud_id || "";
  $("targetCloudId").value = account.target_cloud_id || "";
  $("folderId").value = account.folder_id || "";
  $("targetCidrs").value = (account.target_cidrs || []).join("\n");
  $("targetIps").value = (account.target_ips || []).join("\n");
  setZones(account.zones || []);
  $("serviceAccountJson").value = "";
  $("serviceAccountJson").required = false;
  switchTab("accounts");
  $("accountForm").scrollIntoView({ behavior: "smooth", block: "start" });
}

function accountCard(account) {
  const zones = (account.zones || []).join(", ") || "-";
  const protectedCount = (account.protected_cloud_ids || []).length;
  const modeLabel = account.roll_mode === "project" ? "1 проект" : "Облака";
  const card = document.createElement("article");
  card.className = `account-card ${account.is_active ? "active" : ""}`;
  card.innerHTML = `
    ${account.is_active ? '<div class="active-badge">АКТИВЕН</div>' : ""}
    <h3>${escapeHtml(account.name)}</h3>
    <div class="meta">
      <div class="meta-row"><b>Сервисное облако:</b><span class="chip">${escapeHtml(account.service_cloud_masked)}</span></div>
      <div class="meta-row"><b>Организация:</b><span class="chip">${escapeHtml(account.organization_masked)}</span></div>
      <div class="meta-row"><b>Режим:</b><span class="chip">${escapeHtml(modeLabel)}</span></div>
      <div class="meta-row"><b>Папка:</b><span class="chip">${escapeHtml(account.folder_masked)}</span></div>
      <div class="meta-row"><b>Зоны:</b><span class="chip">${escapeHtml(zones)}</span></div>
      <div class="meta-row"><b>Изоляция:</b><span class="chip">${protectedCount ? `${protectedCount} cloud-id` : "-"}</span></div>
    </div>
    <div class="card-actions">
      <button class="secondary-btn edit-btn">Изменить</button>
      <button class="primary-btn activate-btn">${account.is_active ? "Активен" : "Сделать активным"}</button>
      <button class="secondary-btn danger delete-btn">Удалить</button>
    </div>
  `;
  card.querySelector(".edit-btn").addEventListener("click", () => fillForm(account));
  card.querySelector(".activate-btn").disabled = account.is_active;
  card.querySelector(".activate-btn").addEventListener("click", () => activateAccount(account.id));
  card.querySelector(".delete-btn").disabled = account.running;
  card.querySelector(".delete-btn").addEventListener("click", () => deleteAccount(account.id));
  return card;
}

function renderAccounts() {
  const list = $("accountList");
  list.innerHTML = "";
  if (!state.accounts.length) {
    const empty = document.createElement("div");
    empty.className = "account-card";
    empty.innerHTML = "<h3>Аккаунтов нет</h3><div class='meta'>Выберите режим в форме ниже и добавьте данные Yandex Cloud, чтобы подготовить ротацию.</div>";
    list.appendChild(empty);
    return;
  }
  state.accounts.forEach((account) => list.appendChild(accountCard(account)));
  renderIsolationAccountSelect();
}

function reelState(status) {
  if (status.running) return "running";
  if (status.error) return "error";
  const attempts = status.attempts || [];
  if (attempts.some((attempt) => attempt.matched)) return "success";
  return "idle";
}

function maskedReelItems() {
  return Array.from({ length: 7 }, (_, index) => ({ label: `РОЛЛ_${index + 1}`, hidden: true }));
}

function renderReel(status) {
  const reel = $("reel");
  const mode = reelState(status);
  reel.dataset.state = mode;
  reel.classList.toggle("spinning", mode === "running");
  const baseItems = status.reel && status.reel.length ? status.reel : maskedReelItems();
  const items = mode === "running" ? [...maskedReelItems(), ...maskedReelItems()] : baseItems;
  reel.innerHTML = "";
  items.forEach((item, index, arr) => {
    const div = document.createElement("div");
    const visibleLength = mode === "running" ? arr.length / 2 : arr.length;
    const center = index % visibleLength === Math.floor(visibleLength / 2);
    const hidden = item.hidden || mode === "running" || (!item.ip && mode !== "success");
    div.className = `reel-card ${center ? "center" : ""} ${item.matched ? "success" : ""} ${hidden ? "masked" : ""}`;
    div.innerHTML = `
      <span class="reel-label">${escapeHtml(item.label || `РОЛЛ_${index + 1}`)}</span>
      <span class="reel-ip">${hidden ? "•••.•••.•••.•••" : escapeHtml(item.ip || "-")}</span>
    `;
    reel.appendChild(div);
  });
}

function renderAttempts(status) {
  const body = $("attemptRows");
  const attempts = status.attempts || [];
  body.innerHTML = "";
  if (!attempts.length) {
    body.innerHTML = "<tr><td colspan='5'>История пока пуста.</td></tr>";
    return;
  }
  attempts.slice().reverse().forEach((attempt) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${attempt.attempt_number || "-"}</td>
      <td>${escapeHtml((attempt.at || "").slice(11, 19) || "-")}</td>
      <td>${escapeHtml(attempt.ip || "-")}</td>
      <td>${escapeHtml(attempt.zone || "-")}</td>
      <td><span class="status-pill ${attempt.matched ? "success" : "failure"}">${attempt.matched ? "УСПЕХ" : "МИМО"}</span></td>
    `;
    body.appendChild(tr);
  });
}

function renderStatus(status) {
  state.status = status;
  $("currentIp").textContent = status.current_ip || "-";
  $("targetSubnet").textContent = status.target_subnet || "-";
  $("reelError").textContent = status.error || "";
  $("reelError").classList.toggle("hidden", !status.error);
  $("spinBtn").disabled = !status.active_account || status.running;
  $("stopBtn").disabled = !status.running;
  $("recreateBtn").disabled = !status.running;
  $("logTail").textContent = (status.logs || []).slice(-80).join("\n") || "Лог пока пуст.";
  renderReel(status);
  renderAttempts(status);
}

async function loadAccounts() {
  const data = await api("/api/accounts");
  state.accounts = data.accounts || [];
  renderAccounts();
  ensureIsolationSelection();
}

async function loadStatus() {
  const status = await api("/api/status");
  renderStatus(status);
}

async function refreshAll() {
  await Promise.all([loadAccounts(), loadStatus(), loadTelegramSettings()]);
}

async function loadTelegramSettings() {
  const data = await api("/api/settings/telegram");
  state.telegram = data.telegram || {};
  $("telegramEnabled").checked = Boolean(state.telegram.enabled);
  $("telegramChatId").value = state.telegram.chat_id || "";
  $("telegramToken").value = "";
  $("telegramToken").placeholder = state.telegram.has_bot_token ? "Токен сохранён" : "123456:ABCDEF";
  $("clearTelegramToken").checked = false;
  updateTelegramTestButton();
}

function updateTelegramTestButton() {
  const hasToken = Boolean((state.telegram && state.telegram.has_bot_token) || $("telegramToken").value.trim());
  $("testTelegramBtn").disabled = !$("telegramChatId").value.trim() || !hasToken;
}

async function saveTelegramSettings(event) {
  event.preventDefault();
  const payload = {
    enabled: $("telegramEnabled").checked,
    chat_id: $("telegramChatId").value,
    bot_token: $("telegramToken").value,
    clear_bot_token: $("clearTelegramToken").checked,
  };
  const data = await api("/api/settings/telegram", { method: "PUT", body: JSON.stringify(payload) });
  state.telegram = data.telegram || {};
  showToast("Telegram-настройки сохранены");
  await loadTelegramSettings();
}

async function testTelegramSettings() {
  if ($("telegramToken").value.trim() || $("clearTelegramToken").checked) {
    await saveTelegramSettings(new Event("submit"));
  }
  await api("/api/settings/telegram/test", { method: "POST", body: "{}" });
  showToast("Тестовое Telegram-сообщение отправлено");
}

async function activateAccount(id) {
  await api(`/api/accounts/${id}/activate`, { method: "POST", body: "{}" });
  showToast("Аккаунт активирован");
  await refreshAll();
}

async function deleteAccount(id) {
  if (!confirm("Удалить этот аккаунт?")) return;
  await api(`/api/accounts/${id}`, { method: "DELETE" });
  showToast("Аккаунт удалён");
  await refreshAll();
}

async function saveAccount(event) {
  event.preventDefault();
  const payload = formPayload();
  const id = state.editingId;
  const method = id ? "PUT" : "POST";
  const path = id ? `/api/accounts/${id}` : "/api/accounts";
  await api(path, { method, body: JSON.stringify(payload) });
  showToast(id ? "Аккаунт обновлён" : "Аккаунт сохранён");
  resetForm();
  await refreshAll();
}

function activeOrFirstAccount() {
  return state.accounts.find((account) => account.is_active) || state.accounts[0] || null;
}

function selectedIsolationAccount() {
  return state.accounts.find((account) => String(account.id) === String(state.selectedIsolationAccountId)) || null;
}

function renderIsolationAccountSelect() {
  const select = $("isolationAccountSelect");
  const previous = state.selectedIsolationAccountId;
  select.innerHTML = "";
  if (!state.accounts.length) {
    select.innerHTML = "<option value=''>Нет аккаунтов</option>";
    state.selectedIsolationAccountId = null;
    $("saveIsolationBtn").disabled = true;
    return;
  }
  state.accounts.forEach((account) => {
    const option = document.createElement("option");
    option.value = account.id;
    option.textContent = account.is_active ? `${account.name} (активен)` : account.name;
    select.appendChild(option);
  });
  const fallback = activeOrFirstAccount();
  const selected = state.accounts.some((account) => String(account.id) === String(previous))
    ? previous
    : fallback.id;
  state.selectedIsolationAccountId = selected;
  select.value = selected;
  $("saveIsolationBtn").disabled = false;
}

function ensureIsolationSelection() {
  renderIsolationAccountSelect();
  loadIsolationFieldFromSelection();
}

function loadIsolationFieldFromSelection() {
  const account = selectedIsolationAccount();
  $("protectedCloudIds").value = account ? (account.protected_cloud_ids || []).join("\n") : "";
  $("saveIsolationBtn").disabled = !account;
}

async function saveIsolation() {
  const accountId = state.selectedIsolationAccountId;
  const account = selectedIsolationAccount();
  if (!accountId || !account) {
    showToast("Выберите аккаунт для изоляции");
    return;
  }
  const token = ++state.isolationSaveToken;
  $("saveIsolationBtn").disabled = true;
  const isolationPayload = {};
  isolationPayload["protected_cloud_ids"] = lineList($("protectedCloudIds").value);
  const data = await api(`/api/accounts/${accountId}/isolation`, {
    method: "PUT",
    body: JSON.stringify(isolationPayload),
  });
  await refreshAll();
  if (token === state.isolationSaveToken && String(state.selectedIsolationAccountId) === String(accountId)) {
    loadIsolationFieldFromSelection();
  }
  showToast(`Изоляция сохранена для ${data.account.name}`);
}

async function spin() {
  const active = (state.status && state.status.active_account) || state.accounts.find((item) => item.is_active);
  if (!active) {
    showToast("Сначала добавьте и активируйте аккаунт");
    return;
  }
  await api(`/api/accounts/${active.id}/spin`, { method: "POST", body: "{}" });
  showToast("Ротация запущена");
  await refreshAll();
}

async function stopRun() {
  const active = state.status && state.status.active_account;
  if (!active) return;
  await api(`/api/accounts/${active.id}/stop`, { method: "POST", body: "{}" });
  showToast("Остановка запрошена");
  await refreshAll();
}

async function recreateRun() {
  const active = state.status && state.status.active_account;
  if (!active) return;
  await api(`/api/accounts/${active.id}/recreate`, { method: "POST", body: "{}" });
  showToast("Пересоздание запрошено");
}

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${name}`);
  });
}

function toggleLog(show) {
  $("logPanel").classList.toggle("hidden", !show);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function attachEvents() {
  $("accountForm").addEventListener("submit", (event) => saveAccount(event).catch((error) => showToast(error.message)));
  $("telegramForm").addEventListener("submit", (event) => saveTelegramSettings(event).catch((error) => showToast(error.message)));
  $("newAccountBtn").addEventListener("click", resetForm);
  $("cancelEditBtn").addEventListener("click", resetForm);
  document.querySelectorAll("input[name='rollMode']").forEach((item) => {
    item.addEventListener("change", () => setRollMode(selectedRollMode()));
  });
  $("telegramChatId").addEventListener("input", updateTelegramTestButton);
  $("telegramToken").addEventListener("input", updateTelegramTestButton);
  $("clearTelegramToken").addEventListener("change", updateTelegramTestButton);
  $("testTelegramBtn").addEventListener("click", () => testTelegramSettings().catch((error) => showToast(error.message)));
  $("refreshBtn").addEventListener("click", () => refreshAll().catch((error) => showToast(error.message)));
  $("spinBtn").addEventListener("click", () => spin().catch((error) => showToast(error.message)));
  $("stopBtn").addEventListener("click", () => stopRun().catch((error) => showToast(error.message)));
  $("recreateBtn").addEventListener("click", () => recreateRun().catch((error) => showToast(error.message)));
  $("logToggleBtn").addEventListener("click", () => toggleLog(true));
  $("logCloseBtn").addEventListener("click", () => toggleLog(false));
  $("saveIsolationBtn").addEventListener("click", () => saveIsolation().catch((error) => {
    $("saveIsolationBtn").disabled = !selectedIsolationAccount();
    showToast(error.message);
  }));
  $("isolationAccountSelect").addEventListener("change", (event) => {
    state.selectedIsolationAccountId = event.target.value || null;
    state.isolationSaveToken += 1;
    loadIsolationFieldFromSelection();
  });
  document.querySelectorAll(".tab-btn").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
}

function attachSse() {
  const events = new EventSource("/api/events");
  events.addEventListener("status", (event) => {
    try {
      renderStatus(JSON.parse(event.data));
    } catch (error) {
      console.error(error);
    }
  });
  events.onerror = () => {
    setTimeout(() => loadStatus().catch(() => {}), 1500);
  };
}

attachEvents();
resetForm();
refreshAll().catch((error) => showToast(error.message));
attachSse();
