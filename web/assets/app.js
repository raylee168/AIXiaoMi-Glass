const state = {
  selectedFlowId: null,
  timer: null,
  pollMs: 3000,
  lastItems: [],
};

const $ = (id) => document.getElementById(id);

function fmtTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[ch]));
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadFlows() {
  const userId = $("user-filter").value.trim();
  const url = new URL("/api/scenarios/moments-album/flows", window.location.origin);
  url.searchParams.set("limit", "50");
  if (userId) url.searchParams.set("user_id", userId);

  const data = await fetchJson(url);
  const items = data.items || [];
  state.lastItems = items;

  if (state.selectedFlowId && !items.some((item) => item.flow_id === state.selectedFlowId)) {
    state.selectedFlowId = null;
  }
  if (!state.selectedFlowId && items.length) {
    state.selectedFlowId = items[0].flow_id;
  }
  renderFlows(items);
}

function renderFlows(items) {
  $("flow-count").textContent = items.length;
  if (!items.length) {
    $("flow-list").innerHTML = `<div class="empty">还没有找到相册流程。</div>`;
    return;
  }

  $("flow-list").innerHTML = items.map((item) => {
    const progress = Math.round((item.progress || 0) * 100);
    return `
      <button class="flow-item ${item.flow_id === state.selectedFlowId ? "active" : ""}" data-flow-id="${escapeHtml(item.flow_id)}">
        <div class="flow-item-top">
          <span class="flow-id">${escapeHtml(item.flow_id)}</span>
          <span>${progress}%</span>
        </div>
        <div class="flow-meta">${escapeHtml(item.user_id)} / ${escapeHtml(item.upload_type || "-")} / ${fmtTime(item.created_at)}</div>
        <div class="flow-meta">${escapeHtml((item.current_stage || {}).label || "-")}：${escapeHtml(statusLabel((item.current_stage || {}).status))}</div>
        <div class="progress"><span style="width:${progress}%"></span></div>
      </button>
    `;
  }).join("");

  document.querySelectorAll(".flow-item").forEach((el) => {
    el.addEventListener("click", async () => {
      state.selectedFlowId = el.dataset.flowId;
      renderFlows(state.lastItems);
      await loadFlow();
    });
  });
}

async function loadFlow() {
  if (!state.selectedFlowId) {
    renderEmptyDetail();
    return;
  }
  const data = await fetchJson(`/api/scenarios/moments-album/flows/${encodeURIComponent(state.selectedFlowId)}`);
  renderFlow(data);
}

function renderEmptyDetail() {
  $("flow-title").textContent = "暂无相册流程";
  $("overall-state").textContent = "等待";
  $("stage-track").innerHTML = "";
  $("photo-summary").textContent = "-";
  $("photo-grid").innerHTML = `<div class="empty">上传照片后这里会显示每张照片的状态。</div>`;
  $("decision-box").innerHTML = "";
  $("billing-box").innerHTML = "";
  $("log-box").textContent = "暂无日志。";
}

function renderFlow(data) {
  $("flow-title").textContent = `${data.flow_id} / ${data.batch.user_id}`;
  $("overall-state").textContent = statusLabel(data.overall_status);
  $("refresh-state").textContent = fmtTime(data.refreshed_at);
  renderStages(data.stages || []);
  renderPhotos(data.photos || []);
  renderDecision(data);
  renderBilling(data);
  renderLogs(data.logs || []);
}

function statusLabel(status) {
  return {
    done: "完成",
    running: "运行中",
    waiting: "等待中",
    failed: "失败",
  }[status] || status || "-";
}

function renderStages(stages) {
  $("stage-track").innerHTML = stages.map((stage) => `
    <div class="stage ${escapeHtml(stage.status)}">
      <div class="lamp"></div>
      <div class="stage-label">${escapeHtml(stage.label)}</div>
      <div class="stage-summary">${escapeHtml(statusLabel(stage.status))} / ${escapeHtml(stage.summary)}</div>
      <div class="stage-time">${fmtTime(stage.time)}</div>
    </div>
  `).join("");
}

function renderPhotos(photos) {
  $("photo-summary").textContent = `${photos.length} 张照片`;
  if (!photos.length) {
    $("photo-grid").innerHTML = `<div class="empty">这个流程还没有照片记录。</div>`;
    return;
  }

  $("photo-grid").innerHTML = photos.map((photo) => {
    const pp = photo.preprocess || {};
    const tags = Array.isArray(pp.scene_tags_json) ? pp.scene_tags_json : [];
    const quality = pp.quality_score === undefined || pp.quality_score === null ? "" : `<span class="tag">质量 ${Number(pp.quality_score).toFixed(2)}</span>`;
    return `
      <div class="photo-card">
        <div class="photo-name">${escapeHtml(photo.original_filename || photo.photo_id)}</div>
        <div class="photo-stats">
          <span class="tag">${escapeHtml(photo.preprocess_status || "未预处理")}</span>
          <span class="tag">${escapeHtml(photo.cleanup_status || "未清理")}</span>
          <span class="tag">${escapeHtml(photo.width || "-")}x${escapeHtml(photo.height || "-")}</span>
          ${quality}
          ${tags.slice(0, 2).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
        </div>
      </div>
    `;
  }).join("");
}

function renderDecision(data) {
  const latestDecision = (data.decisions || []).slice(-1)[0] || {};
  const tasks = data.generation_tasks || [];
  const results = data.results || [];
  $("decision-box").innerHTML = [
    ["判断结果", latestDecision.decision_result || latestDecision.status || "-"],
    ["判断原因", latestDecision.decision_reason || "-"],
    ["置信度", latestDecision.confidence ?? "-"],
    ["生成任务", `${tasks.filter((task) => task.status === "success").length}/${tasks.length}`],
    ["结果文件", `${results.length} 个`],
    ["相册标题", results.map((result) => result.album_title).filter(Boolean).join(" / ") || "-"],
  ].map(kvRow).join("");
}

function renderBilling(data) {
  const pushes = data.pushes || [];
  const holds = data.holds || [];
  const costs = data.costs || [];
  const wallet = data.wallet || {};
  const total = costs.reduce((sum, row) => sum + Number(row.charged_tokens || 0), 0);
  $("billing-box").innerHTML = [
    ["钱包余额", wallet.balance_tokens === undefined ? "-" : Number(wallet.balance_tokens).toLocaleString("zh-CN")],
    ["冻结额度", `${holds.filter((hold) => ["frozen", "settled"].includes(hold.status)).length}/${holds.length}`],
    ["结算记录", `${holds.filter((hold) => hold.status === "settled").length}/${holds.length}`],
    ["计费明细", `${costs.length} 条`],
    ["Token 合计", total.toLocaleString("zh-CN")],
    ["推送状态", `${pushes.filter((push) => push.status === "success").length}/${pushes.length}`],
    ["消息 ID", pushes.map((push) => push.message_id).filter(Boolean).join(" / ") || "-"],
  ].map(kvRow).join("");
}

function kvRow([key, value]) {
  return `<div class="kv"><span>${escapeHtml(key)}</span><span>${escapeHtml(value)}</span></div>`;
}

function renderLogs(logs) {
  $("log-box").textContent = logs.length
    ? logs.map((row) => `[${row.unit}] ${row.line}`).join("\n")
    : "当前流程没有匹配到最近日志。";
}

async function tick() {
  try {
    await loadFlows();
    await loadFlow();
  } catch (error) {
    $("refresh-state").textContent = `刷新失败：${error.message}`;
  }
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(tick, state.pollMs);
}

$("refresh-btn").addEventListener("click", async () => {
  state.selectedFlowId = null;
  await tick();
});

$("user-filter").addEventListener("keydown", async (event) => {
  if (event.key === "Enter") {
    state.selectedFlowId = null;
    await tick();
  }
});

tick();
startPolling();
