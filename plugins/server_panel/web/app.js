const state = {
  token: '',
  tokenKey: 'panel_token',
  panelInfo: null,
  currentPath: '',
  selectedFile: '',
  logSource: null,
  logReconnectTimer: null,
  logReconnectDelay: 1000,
  statusTimer: null,
  consoleLines: [],
  consoleFilter: '',
  schedules: [],
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const VIEW_META = {
  dashboard: ['總覽', '伺服器狀態與資源監控'],
  console: ['主控台', '即時日誌與指令輸入'],
  files: ['檔案管理', '瀏覽、編輯、上傳與搜尋'],
  backups: ['備份', '一鍵打包下載伺服器資料'],
  schedules: ['排程', '定時執行指令或電源操作'],
  settings: ['設定', 'server.properties 與 MCDR 設定'],
  activity: ['活動記錄', '面板操作歷史'],
};

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.remove('hidden');
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.add('hidden'), 2800);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    logout(false);
    throw new Error('登入已過期');
  }
  const isJson = (res.headers.get('Content-Type') || '').includes('application/json');
  if (isJson) {
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '操作失敗');
    return data.data;
  }
  return res;
}

function applyPanelInfo(info) {
  state.panelInfo = info;
  state.tokenKey = `panel_token_${info.port}`;
  state.token = localStorage.getItem(state.tokenKey) || '';
  document.body.className = `role-${info.server_role}`;
  document.title = `${info.server_name} - GRAVITY Panel`;
  $('#login-title').textContent = info.server_name;
  $('#login-subtitle').textContent = `量子級管理 · 埠 ${info.port}`;
  $('#sidebar-title').textContent = info.server_name;
  $('#sidebar-subtitle').textContent = `Port ${info.port}`;
  $('#server-badge').textContent = info.server_label;
  $('#sidebar-badge').textContent = info.server_label;
  $('#power-server-name').textContent = info.server_name;
  $('#power-subtitle').textContent = `${info.server_label} · MCDR`;
}

async function loadPanelInfo() {
  const res = await fetch('/api/panel-info');
  const payload = await res.json();
  if (!payload.ok) throw new Error('無法載入面板資訊');
  applyPanelInfo(payload.data);
}

function logout(showMessage = true) {
  state.token = '';
  localStorage.removeItem(state.tokenKey);
  stopLogStream();
  clearInterval(state.statusTimer);
  $('#app').classList.add('hidden');
  $('#login-screen').classList.remove('hidden');
  if (showMessage) showToast('已登出');
}

async function login(password) {
  const data = await api('/api/login', { method: 'POST', body: { password } });
  state.token = data.token;
  localStorage.setItem(state.tokenKey, state.token);
  $('#login-screen').classList.add('hidden');
  $('#app').classList.remove('hidden');
  await bootstrap();
}

function updatePowerButtons(online) {
  $('#btn-start').disabled = online;
  $('#btn-stop').disabled = !online;
  $('#btn-restart').disabled = !online;
}

function updateStatusUI(data) {
  const online = data.server_running;
  const pill = $('#status-pill');
  pill.textContent = online ? '運行中' : '離線';
  pill.classList.toggle('online', online);
  pill.classList.toggle('offline', !online);
  $('#mc-status-text').textContent = online
    ? (data.server_startup ? '伺服器已完全啟動，可正常遊玩。' : '伺服器啟動中，請稍候...')
    : '伺服器目前未運行。';
  $('#file-root-text').textContent = data.file_root || '-';
  $('#stat-status').textContent = online ? '線上' : '離線';
  $('#stat-port').textContent = `埠 ${data.port || '-'}`;
  updatePowerButtons(online);

  const stats = data.stats || {};
  $('#stat-cpu').textContent = stats.cpu_percent != null ? `${stats.cpu_percent}%` : '—';
  $('#stat-mem').textContent = stats.memory_mb != null ? `${stats.memory_mb} MB` : '—';
  $('#stat-mem-pct').textContent = stats.memory_percent != null ? `${stats.memory_percent}% 程序記憶體` : '需安裝 psutil';
  if (stats.disk_used_gb != null) {
    $('#stat-disk').textContent = `${stats.disk_used_gb} / ${stats.disk_total_gb} GB`;
    $('#stat-disk-pct').textContent = `${stats.disk_percent}% 已使用`;
  } else {
    $('#stat-disk').textContent = '—';
    $('#stat-disk-pct').textContent = '—';
  }
}

async function refreshStatus() {
  const data = await api('/api/status');
  updateStatusUI(data);
  return data;
}

function switchView(view) {
  $$('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
  $$('.view').forEach((section) => section.classList.toggle('hidden', section.id !== `view-${view}`));
  const [title, subtitle] = VIEW_META[view] || ['', ''];
  $('#view-title').textContent = title;
  $('#view-subtitle').textContent = subtitle;
  if (view === 'files') loadFiles(state.currentPath);
  if (view === 'console') startLogStream();
  if (view === 'schedules') loadSchedules();
  if (view === 'settings') loadSettings();
  if (view === 'activity') loadActivity();
  if (view === 'dashboard') loadNetworkInfo();
}

function renderConsole() {
  const output = $('#console-output');
  const filter = state.consoleFilter.trim().toLowerCase();
  const lines = filter
    ? state.consoleLines.filter((line) => line.toLowerCase().includes(filter))
    : state.consoleLines;
  output.textContent = lines.join('\n');
  output.scrollTop = output.scrollHeight;
}

function appendConsoleLine(line) {
  state.consoleLines.push(line);
  if (state.consoleLines.length > 2000) state.consoleLines = state.consoleLines.slice(-1500);
  renderConsole();
}

async function loadLogHistory() {
  const lines = await api('/api/logs/history');
  state.consoleLines = lines;
  renderConsole();
}

function stopLogStream() {
  clearTimeout(state.logReconnectTimer);
  state.logReconnectTimer = null;
  if (state.logSource) {
    state.logSource.close();
    state.logSource = null;
  }
}

function updateConsoleStreamStatus(status) {
  const pill = $('#console-stream-status');
  if (!pill) return;
  const labels = { connected: '已連線', reconnecting: '重連中…', offline: '離線' };
  pill.textContent = labels[status] || status;
  pill.classList.toggle('online', status === 'connected');
  pill.classList.toggle('offline', status !== 'connected');
}

function scheduleLogReconnect() {
  clearTimeout(state.logReconnectTimer);
  state.logReconnectTimer = setTimeout(() => {
    const consoleView = $('#view-console');
    if (!consoleView || consoleView.classList.contains('hidden')) return;
    connectLogStream();
  }, state.logReconnectDelay);
  state.logReconnectDelay = Math.min(Math.round(state.logReconnectDelay * 1.5), 30000);
}

function connectLogStream() {
  if (state.logSource) {
    state.logSource.close();
    state.logSource = null;
  }
  state.logSource = new EventSource(`/api/logs/stream?token=${encodeURIComponent(state.token)}`);
  state.logSource.onopen = () => {
    state.logReconnectDelay = 1000;
    updateConsoleStreamStatus('connected');
  };
  state.logSource.onmessage = (event) => {
    try {
      appendConsoleLine(JSON.parse(event.data));
    } catch {
      appendConsoleLine(event.data);
    }
  };
  state.logSource.onerror = () => {
    if (state.logSource) {
      state.logSource.close();
      state.logSource = null;
    }
    updateConsoleStreamStatus('reconnecting');
    scheduleLogReconnect();
  };
}

function startLogStream() {
  stopLogStream();
  state.logReconnectDelay = 1000;
  updateConsoleStreamStatus('reconnecting');
  loadLogHistory().then(() => connectLogStream()).catch((err) => {
    updateConsoleStreamStatus('offline');
    showToast(err.message);
  });
}

function formatSize(size) {
  if (size == null) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function parseDownloadFilename(res) {
  const header = res.headers.get('Content-Disposition') || '';
  const match = /filename="([^"]+)"/.exec(header);
  return match ? match[1] : 'download.zip';
}

async function downloadFromResponse(res, fallbackName) {
  if (!res.ok) {
    let message = '下載失敗';
    try {
      const payload = await res.json();
      message = payload.error || message;
    } catch { /* ignore */ }
    throw new Error(message);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = parseDownloadFilename(res) || fallbackName;
  a.click();
  URL.revokeObjectURL(url);
}

async function downloadZipPath(path, fallbackName) {
  showToast('正在打包，請稍候...');
  const skipHeavy = path === '' ? '1' : '0';
  const res = await fetch(`/api/files/zip?path=${encodeURIComponent(path)}&skip_heavy=${skipHeavy}`, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  await downloadFromResponse(res, fallbackName);
  showToast('下載完成');
}

async function downloadBackupPreset(preset) {
  showToast('正在打包，請稍候...');
  const res = await fetch(`/api/backup/download?preset=${encodeURIComponent(preset)}`, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  await downloadFromResponse(res, `${preset}-backup.zip`);
  showToast('備份下載完成');
}

function renderFileEntries(entries) {
  const list = $('#files-list');
  list.innerHTML = '';
  if (!entries.length) {
    list.innerHTML = '<div class="file-item"><div>（空資料夾）</div></div>';
    return;
  }
  entries.forEach((entry) => {
    const item = document.createElement('div');
    item.className = `file-item${state.selectedFile === entry.path ? ' active' : ''}`;
    item.innerHTML = `
      <div class="icon">${entry.type === 'dir' ? '📁' : '📄'}</div>
      <div>
        <div>${entry.name}</div>
        <div class="meta">${entry.type === 'file' ? formatSize(entry.size) : '資料夾'}</div>
      </div>
      <div class="actions"></div>
    `;
    const del = document.createElement('button');
    del.textContent = '刪';
    del.className = 'ghost';
    del.onclick = (e) => { e.stopPropagation(); deletePath(entry.path); };
    item.querySelector('.actions').appendChild(del);
    item.onclick = () => {
      if (entry.type === 'dir') {
        state.currentPath = entry.path;
        loadFiles(entry.path);
      } else {
        openFile(entry.path, entry.name);
      }
    };
    list.appendChild(item);
  });
}

async function loadFiles(path = '') {
  state.currentPath = path;
  $('#files-path').textContent = `/${path}`;
  $('#files-rename').disabled = !state.selectedFile;
  try {
    const data = await api(`/api/files/list?path=${encodeURIComponent(path)}`);
    if (data.type === 'dir') renderFileEntries(data.entries || []);
  } catch (err) {
    showToast(err.message || '無法載入檔案列表');
  }
}

async function searchFiles(query) {
  if (!query.trim()) {
    loadFiles(state.currentPath);
    return;
  }
  try {
    const entries = await api(`/api/files/search?q=${encodeURIComponent(query)}`);
    renderFileEntries(entries);
    $('#files-path').textContent = `搜尋: ${query}`;
  } catch (err) {
    showToast(err.message);
  }
}

async function openFile(path, name) {
  state.selectedFile = path;
  $('#editor-name').textContent = name;
  $('#editor-download').disabled = false;
  $('#files-rename').disabled = false;
  try {
    const data = await api(`/api/files/read?path=${encodeURIComponent(path)}`);
    $('#editor-content').disabled = false;
    $('#editor-save').disabled = false;
    $('#editor-content').value = data.content;
  } catch (err) {
    $('#editor-content').disabled = true;
    $('#editor-save').disabled = true;
    $('#editor-content').value = err.message;
  }
  loadFiles(state.currentPath);
}

async function saveFile() {
  if (!state.selectedFile) return;
  await api('/api/files/write', {
    method: 'POST',
    body: { path: state.selectedFile, content: $('#editor-content').value },
  });
  showToast('已儲存');
}

async function deletePath(path) {
  if (!confirm(`確定刪除 ${path}？`)) return;
  await api('/api/files/delete', { method: 'POST', body: { path } });
  if (state.selectedFile === path) {
    state.selectedFile = '';
    $('#editor-name').textContent = '未選取檔案';
    $('#editor-content').value = '';
    $('#editor-save').disabled = true;
    $('#editor-download').disabled = true;
    $('#files-rename').disabled = true;
  }
  loadFiles(state.currentPath);
}

async function loadSchedules() {
  state.schedules = await api('/api/schedules');
  const wrap = $('#schedules-list');
  if (!state.schedules.length) {
    wrap.innerHTML = '<div class="card"><p>尚無排程，點擊「新增排程」建立定時任務。</p></div>';
    return;
  }
  const rows = state.schedules.map((item) => `
    <tr>
      <td><strong>${item.name || item.id}</strong></td>
      <td><code>${item.cron || ''}</code></td>
      <td>${item.action || 'command'}${item.command ? `: ${item.command}` : ''}</td>
      <td><span class="badge ${item.enabled !== false ? 'on' : 'off'}">${item.enabled !== false ? '啟用' : '停用'}</span></td>
      <td class="table-actions">
        <button class="ghost" data-run="${item.id}">執行</button>
        <button class="ghost" data-edit="${item.id}">編輯</button>
        <button class="ghost" data-del="${item.id}">刪除</button>
      </td>
    </tr>
  `).join('');
  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr><th>名稱</th><th>Cron</th><th>動作</th><th>狀態</th><th>操作</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  wrap.querySelectorAll('[data-edit]').forEach((btn) => {
    btn.onclick = () => openScheduleDialog(btn.dataset.edit);
  });
  wrap.querySelectorAll('[data-del]').forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm('確定刪除此排程？')) return;
      await api('/api/schedules/delete', { method: 'POST', body: { id: btn.dataset.del } });
      loadSchedules();
    };
  });
  wrap.querySelectorAll('[data-run]').forEach((btn) => {
    btn.onclick = async () => {
      await api('/api/schedules/run', { method: 'POST', body: { id: btn.dataset.run } });
      showToast('排程已執行');
    };
  });
}

function openScheduleDialog(id = '') {
  const item = state.schedules.find((s) => s.id === id) || {};
  $('#schedule-dialog-title').textContent = id ? '編輯排程' : '新增排程';
  $('#schedule-id').value = item.id || '';
  $('#schedule-name').value = item.name || '';
  $('#schedule-cron').value = item.cron || '0 */6 * * *';
  $('#schedule-action').value = item.action || 'command';
  $('#schedule-command').value = item.command || '';
  $('#schedule-enabled').checked = item.enabled !== false;
  toggleScheduleCommand();
  $('#schedule-dialog').showModal();
}

function toggleScheduleCommand() {
  const action = $('#schedule-action').value;
  $('#schedule-command-wrap').classList.toggle('hidden', action !== 'command');
}

async function loadSettings() {
  try {
    const props = await api('/api/settings/server-properties');
    $('#props-editor').value = props.content || '';
  } catch {
    $('#props-editor').value = '# 找不到 server.properties';
  }
  try {
    const mcdr = await api('/api/settings/mcdr-config');
    $('#mcdr-editor').value = mcdr.content || '';
  } catch {
    $('#mcdr-editor').value = '# 找不到 config.yml';
  }
}

async function loadNetworkInfo() {
  try {
    const props = await api('/api/settings/server-properties');
    const p = props.parsed || {};
    $('#network-info').textContent = [
      `遊戲埠: ${p['server-port'] || '25565'}`,
      `MOTD: ${(p.motd || '').replace(/\\n/g, ' ')}`,
      `難度: ${p.difficulty || '-'}`,
      `最大玩家: ${p['max-players'] || '-'}`,
      `白名單: ${p['white-list'] || p['enforce-whitelist'] || '-'}`,
    ].join('\n');
  } catch {
    $('#network-info').textContent = '無法載入 server.properties';
  }
}

async function loadActivity() {
  const items = await api('/api/activity');
  const list = $('#activity-list');
  if (!items.length) {
    list.innerHTML = '<div class="card"><p>尚無活動記錄。</p></div>';
    return;
  }
  list.innerHTML = items.map((item) => `
    <div class="activity-item">
      <span class="time">${item.time || ''}</span>
      <span class="action">${item.action || ''}</span>
      <span class="detail">${item.detail || ''}</span>
    </div>
  `).join('');
}

async function runServerAction(path, fallbackMessage) {
  try {
    const data = await api(path, { method: 'POST' });
    showToast(data.message || fallbackMessage);
    if (data.status) updateStatusUI(data.status);
    else refreshStatus();
  } catch (err) {
    showToast(err.message || '操作失敗');
  }
}

async function bootstrap() {
  await refreshStatus();
  state.statusTimer = setInterval(() => refreshStatus().catch(() => {}), 5000);
  switchView('dashboard');
}

$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = $('#login-error');
  err.classList.add('hidden');
  try {
    await login($('#password-input').value);
  } catch (error) {
    err.textContent = error.message;
    err.classList.remove('hidden');
  }
});

$('#logout-btn').onclick = () => logout();
$$('.nav-btn').forEach((btn) => { btn.onclick = () => switchView(btn.dataset.view); });

$('#btn-start').onclick = () => runServerAction('/api/server/start', '已送出啟動指令');
$('#btn-stop').onclick = () => runServerAction('/api/server/stop', '已送出停止指令');
$('#btn-restart').onclick = () => runServerAction('/api/server/restart', '已送出重啟指令');
$('#btn-stop-exit').onclick = async () => {
  if (!confirm('確定關閉 MCDR？這會停止整個伺服器程序。')) return;
  await runServerAction('/api/server/stop_exit', 'MCDR 關閉指令已送出');
};

$('#goto-console').onclick = () => switchView('console');
$('#goto-files').onclick = () => switchView('files');
$('#goto-backups').onclick = () => switchView('backups');

$('#backup-world').onclick = () => downloadBackupPreset('world').catch((err) => showToast(err.message));
$('#backup-config').onclick = () => downloadBackupPreset('config').catch((err) => showToast(err.message));
$('#backup-full').onclick = () => {
  if (!confirm('完整備份檔案較多，打包可能需要一些時間，是否繼續？')) return;
  downloadBackupPreset('full').catch((err) => showToast(err.message));
};

$('#console-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = $('#console-input');
  const command = input.value.trim();
  if (!command) return;
  try {
    await api('/api/console', { method: 'POST', body: { command } });
    input.value = '';
  } catch (err) {
    showToast(err.message || '指令送出失敗');
  }
});

$('#console-clear').onclick = () => { state.consoleLines = []; renderConsole(); };
$('#console-filter').oninput = (e) => { state.consoleFilter = e.target.value; renderConsole(); };

$('#files-up').onclick = () => {
  const parts = state.currentPath.split('/').filter(Boolean);
  parts.pop();
  loadFiles(parts.join('/'));
};
$('#files-refresh').onclick = () => loadFiles(state.currentPath);
$('#files-zip').onclick = () => {
  const name = state.currentPath.split('/').filter(Boolean).pop() || 'root';
  downloadZipPath(state.currentPath, `${name}.zip`).catch((err) => showToast(err.message));
};
$('#files-mkdir').onclick = async () => {
  const name = prompt('資料夾名稱');
  if (!name) return;
  const path = [state.currentPath, name].filter(Boolean).join('/');
  await api('/api/files/mkdir', { method: 'POST', body: { path } });
  loadFiles(state.currentPath);
};
$('#files-rename').onclick = async () => {
  if (!state.selectedFile) return;
  const newName = prompt('新檔名', state.selectedFile.split('/').pop());
  if (!newName) return;
  const dir = state.currentPath;
  const to = [dir, newName].filter(Boolean).join('/');
  await api('/api/files/rename', { method: 'POST', body: { from: state.selectedFile, to } });
  state.selectedFile = to;
  loadFiles(state.currentPath);
};
$('#files-upload').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('path', state.currentPath);
  form.append('file', file);
  await api('/api/files/upload', { method: 'POST', body: form });
  showToast('上傳完成');
  e.target.value = '';
  loadFiles(state.currentPath);
};
let searchTimer;
$('#files-search').oninput = (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchFiles(e.target.value), 300);
};
$('#editor-save').onclick = saveFile;
$('#editor-download').onclick = async () => {
  if (!state.selectedFile) return;
  const res = await fetch(`/api/files/download?path=${encodeURIComponent(state.selectedFile)}`, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  if (!res.ok) { showToast('下載失敗'); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = state.selectedFile.split('/').pop();
  a.click();
  URL.revokeObjectURL(url);
};

$('#schedule-add').onclick = () => openScheduleDialog();
$('#schedule-action').onchange = toggleScheduleCommand;
$('#schedule-cancel').onclick = () => $('#schedule-dialog').close();
$('#schedule-form').onsubmit = async (e) => {
  e.preventDefault();
  const payload = {
    id: $('#schedule-id').value || undefined,
    name: $('#schedule-name').value.trim(),
    cron: $('#schedule-cron').value.trim(),
    action: $('#schedule-action').value,
    command: $('#schedule-command').value.trim(),
    enabled: $('#schedule-enabled').checked,
  };
  await api('/api/schedules/save', { method: 'POST', body: payload });
  $('#schedule-dialog').close();
  showToast('排程已儲存');
  loadSchedules();
};

$$('.tab-btn').forEach((btn) => {
  btn.onclick = () => {
    $$('.tab-btn').forEach((b) => b.classList.toggle('active', b === btn));
    $$('.tab-panel').forEach((p) => p.classList.add('hidden'));
    $(`#tab-${btn.dataset.tab}`).classList.remove('hidden');
  };
});

$('#props-save-raw').onclick = async () => {
  await api('/api/settings/server-properties', { method: 'POST', body: { content: $('#props-editor').value } });
  showToast('server.properties 已儲存');
  loadNetworkInfo();
};
$('#props-reload').onclick = loadSettings;
$('#mcdr-save').onclick = async () => {
  await api('/api/settings/mcdr-config', { method: 'POST', body: { content: $('#mcdr-editor').value } });
  showToast('config.yml 已儲存（部分變更需重啟 MCDR）');
};
$('#mcdr-reload').onclick = loadSettings;

async function initPanel() {
  try {
    await loadPanelInfo();
    if (state.token) {
      $('#login-screen').classList.add('hidden');
      $('#app').classList.remove('hidden');
      await bootstrap();
    }
  } catch {
    if (state.token) logout(false);
    else showToast('無法載入面板資訊');
  }
}

initPanel();
