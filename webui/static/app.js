const $ = (id) => document.getElementById(id);
let selectedFile = null;
let currentJobId = null;
let pollTimer = null;
let ollamaReady = false;
let cacheBytes = 0;

const stages = [
  ['extracting', '解析 PDF'],
  ['structuring', '识别结构'],
  ['terminology', '术语扫描'],
  ['context', '上下文检索'],
  ['translating', '约束翻译'],
  ['qa', '规则质检'],
  ['repairing', '自动重译'],
  ['gate', '质量门禁'],
  ['rendering', '重建 PDF'],
  ['summarizing', '生成总结'],
];

function setSelectedFile(file) {
  selectedFile = file && file.name.toLowerCase().endsWith('.pdf') ? file : null;
  const button = $('start-button');
  if (!selectedFile) {
    $('file-meta').classList.add('hidden');
    button.disabled = true;
    return;
  }
  $('file-meta').textContent = `${selectedFile.name} · ${(selectedFile.size / 1024 / 1024).toFixed(2)} MB`;
  $('file-meta').classList.remove('hidden');
  button.disabled = !ollamaReady;
  button.title = ollamaReady ? '' : '请先手动启动 Ollama';
}

async function loadHealth() {
  try {
    const response = await fetch('/api/health');
    const data = await response.json();
    const ollama = data.ollama || {};
    const dot = $('ollama-dot');
    dot.className = `status-dot ${ollama.ready ? 'ready' : 'error'}`;
    ollamaReady = Boolean(ollama.ready);
    $('ollama-status').textContent = ollama.ready ? '模型服务已连接' : '等待 Ollama 启动';
    if (selectedFile) setSelectedFile(selectedFile);
    $('ollama-detail').textContent = ollama.ready ? ollama.base_url : '请先手动启动项目自带 Ollama';
    const chips = $('model-chips');
    chips.innerHTML = '';
    (ollama.models || []).slice(0, 5).forEach((name) => {
      const chip = document.createElement('span');
      chip.textContent = name;
      chips.appendChild(chip);
    });
  } catch (error) {
    $('ollama-dot').className = 'status-dot error';
    $('ollama-status').textContent = 'WebUI 暂时不可用';
  }
}

async function loadCache() {
  try {
    const response = await fetch('/api/cache');
    const data = await response.json();
    cacheBytes = Number(data.size_bytes || 0);
    $('cache-size').textContent = `${(cacheBytes / 1024 / 1024).toFixed(2)} MB · ${data.file_count || 0} 个文件`;
  } catch (error) {
    $('cache-size').textContent = '读取失败';
  }
}

async function clearCache() {
  if (!confirm('确定清理 WebUI 临时缓存吗？output 内的最终结果不会被删除。')) return;
  const button = $('clear-cache-button');
  button.disabled = true;
  button.textContent = '清理中…';
  try {
    const response = await fetch('/api/cache/clear', { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '清理失败');
    await loadCache();
    await loadHistory();
    $('job-panel').classList.add('hidden');
    alert('缓存已清理，output 目录中的最终结果已保留。');
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '清理缓存';
  }
}

async function loadHistory() {
  try {
    const response = await fetch('/api/jobs');
    const data = await response.json();
    const list = $('history-list');
    const jobs = data.jobs || [];
    if (!jobs.length) {
      list.innerHTML = '<div class="empty-state">还没有处理记录。</div>';
      return;
    }
    list.innerHTML = '';
    jobs.slice(0, 12).forEach((job) => {
      const item = document.createElement('div');
      item.className = 'history-item';
      const info = document.createElement('div');
      info.innerHTML = `<strong>${escapeHtml(job.filename)}</strong><small>${job.created_at || ''} · ${job.id}</small>`;
      const status = document.createElement('span');
      status.className = `history-status ${job.status}`;
      status.textContent = statusText(job.status);
      const actions = document.createElement('div');
      actions.className = 'history-actions';
      if (job.status === 'completed') {
        const folder = job.output_folder || job.id;
        actions.innerHTML = `<a href="/api/outputs/${encodeURIComponent(folder)}/files/translated_cn.pdf">下载中文 PDF</a>`;
      } else {
        actions.innerHTML = `<a href="#" data-job="${encodeURIComponent(job.id)}">查看</a>`;
        actions.querySelector('a').onclick = (event) => {
          event.preventDefault();
          openJob(job.id);
        };
      }
      item.append(info, status, actions);
      list.appendChild(item);
    });
  } catch (error) {
    $('history-list').innerHTML = '<div class="empty-state">读取记录失败。</div>';
  }
}

function statusText(status) {
  return ({ queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', interrupted: '已中断' })[status] || status;
}

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value || '';
  return div.innerHTML;
}

function renderStages(job) {
  const list = $('stage-list');
  const activeIndex = stages.findIndex(([key]) => key === job.stage);
  list.innerHTML = '';
  stages.forEach(([key, label], index) => {
    const item = document.createElement('div');
    item.className = 'stage-item';
    if (activeIndex > index || job.status === 'completed') item.classList.add('done');
    if (activeIndex === index && job.status === 'running') item.classList.add('active');
    item.innerHTML = `<span class="stage-icon">${activeIndex > index || job.status === 'completed' ? '✓' : index + 1}</span><span>${label}</span>`;
    list.appendChild(item);
  });
}

function renderResults(job) {
  const section = $('result-section');
  if (!job.files || !job.files.length) {
    section.classList.add('hidden');
    return;
  }
  section.classList.remove('hidden');
  $('job-folder').textContent = '打开中文 PDF';
  $('job-folder').href = `/api/jobs/${encodeURIComponent(job.id)}/files/translated_cn.pdf`;
  const grid = $('result-grid');
  grid.innerHTML = '';
  job.files.forEach((file) => {
    const card = document.createElement('a');
    card.className = 'result-card';
    card.href = file.url;
    card.target = '_blank';
    card.rel = 'noreferrer';
    const size = file.size ? `${(Number(file.size) / 1024 / 1024).toFixed(2)} MB` : '';
    card.innerHTML = `<strong>${escapeHtml(file.label)}</strong><span>${escapeHtml(file.name)} · ${size}</span>`;
    grid.appendChild(card);
  });
}

function renderJob(job) {
  currentJobId = job.id;
  $('job-panel').classList.remove('hidden');
  $('job-title').textContent = job.filename || '处理任务';
  const badge = $('job-badge');
  badge.className = `job-badge ${job.status}`;
  badge.textContent = statusText(job.status);
  $('progress-message').textContent = job.message || '';
  $('progress-value').textContent = `${job.progress || 0}%`;
  $('progress-bar').style.width = `${job.progress || 0}%`;
  $('output-folder').textContent = job.output_folder || '处理完成后生成';
  $('job-log').textContent = (job.logs || []).join('\n') || '任务正在准备…';
  $('job-log').scrollTop = $('job-log').scrollHeight;
  renderStages(job);
  renderResults(job);
}

async function openJob(jobId) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  const job = await response.json();
  renderJob(job);
  clearInterval(pollTimer);
  if (job.status === 'running' || job.status === 'queued') {
    pollTimer = setInterval(() => pollJob(jobId), 1200);
  }
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    const job = await response.json();
    renderJob(job);
    if (job.status !== 'running' && job.status !== 'queued') {
      clearInterval(pollTimer);
      loadHistory();
    }
  } catch (error) {
    clearInterval(pollTimer);
  }
}

async function startJob() {
  if (!selectedFile) return;
  if (!ollamaReady) {
    alert('请先手动启动 Ollama，再开始任务。');
    return;
  }
  $('start-button').disabled = true;
  $('start-button').textContent = '正在上传…';
  try {
    const response = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'X-Filename': encodeURIComponent(selectedFile.name) },
      body: selectedFile,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '上传失败');
    renderJob(data.job);
    $('start-button').textContent = '任务已开始';
    clearInterval(pollTimer);
    pollTimer = setInterval(() => pollJob(data.job.id), 1200);
    loadHistory();
  } catch (error) {
    alert(error.message);
    $('start-button').textContent = '开始自动翻译';
    $('start-button').disabled = false;
  }
}

function bindDropzone() {
  const zone = $('dropzone');
  const input = $('file-input');
  $('choose-button').onclick = () => input.click();
  $('start-button').onclick = startJob;
  $('refresh-button').onclick = () => currentJobId && openJob(currentJobId);
  $('history-refresh').onclick = loadHistory;
  $('clear-cache-button').onclick = clearCache;
  input.onchange = () => setSelectedFile(input.files[0]);
  ['dragenter', 'dragover'].forEach((name) => zone.addEventListener(name, (event) => {
    event.preventDefault();
    zone.classList.add('dragging');
  }));
  ['dragleave', 'drop'].forEach((name) => zone.addEventListener(name, (event) => {
    event.preventDefault();
    zone.classList.remove('dragging');
  }));
  zone.addEventListener('drop', (event) => {
    setSelectedFile(event.dataTransfer.files[0]);
  });
}

bindDropzone();
loadHealth();
loadCache();
loadHistory();
setInterval(loadHealth, 5000);
setInterval(loadCache, 10000);
