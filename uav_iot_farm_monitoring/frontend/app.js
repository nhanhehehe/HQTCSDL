const api = '/api/v1';
let token = localStorage.getItem('farmIotToken');

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'})[char]);
const headers = () => token ? { Authorization: `Bearer ${token}` } : {};

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  if (response.status === 401) logout();
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function table(rows, columns) {
  if (!rows.length) return '<p class="muted">Chưa có dữ liệu.</p>';
  return `<div class="table-wrap"><table><thead><tr>${columns.map(c => `<th>${c.label}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(c => `<td>${escapeHtml(c.value(row))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function drawTrend(records) {
  const canvas = document.querySelector('#chart');
  const context = canvas.getContext('2d');
  const width = Math.max(canvas.clientWidth || 720, 320);
  const height = 240;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio; canvas.height = height * ratio;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.fillStyle = '#59716a'; context.font = '14px Arial';
  if (!records.length) { context.fillText('Chưa có bản đo để vẽ biểu đồ.', 18, 32); return; }
  const values = records.flatMap(x => [Number(x.temperature), Number(x.air_humidity)]);
  const min = Math.floor(Math.min(...values) - 3); const max = Math.ceil(Math.max(...values) + 3);
  const left = 42, right = 16, top = 24, bottom = 32;
  const x = index => records.length === 1 ? left + (width-left-right)/2 : left + index * (width-left-right)/(records.length-1);
  const y = value => top + (max-value) * (height-top-bottom)/(max-min || 1);
  context.strokeStyle = '#c7d7d0'; context.lineWidth = 1;
  for (let step = 0; step <= 4; step++) { const py = top + step * (height-top-bottom)/4; context.beginPath(); context.moveTo(left, py); context.lineTo(width-right, py); context.stroke(); context.fillText(String(Math.round(max - step*(max-min)/4)), 5, py + 4); }
  const series = [{ key: 'temperature', color: '#d85a36' }, { key: 'air_humidity', color: '#146c99' }];
  series.forEach(({key, color}) => { context.strokeStyle = color; context.lineWidth = 2; context.beginPath(); records.forEach((row, index) => { const px = x(index), py = y(Number(row[key])); if (index) context.lineTo(px, py); else context.moveTo(px, py); }); context.stroke(); });
  context.fillStyle = '#d85a36'; context.fillText('— Nhiệt độ °C', left, height-8); context.fillStyle = '#146c99'; context.fillText('— Độ ẩm %RH', left+130, height-8);
}

async function loadDashboard() {
  const data = await request('/dashboard');
  const labels = { farms: 'Farm', zones: 'Zone', sensors: 'Sensor', gateways: 'Gateway', open_alerts: 'Alert mở', missions: 'Đợt thu thập' };
  document.querySelector('#stats').innerHTML = Object.entries(data.counts).map(([key, value]) => `<div class="stat"><span>${labels[key]}</span><b>${value}</b></div>`).join('');
  document.querySelector('#latest').innerHTML = table(data.latest_measurements, [
    { label: 'Node', value: x => x.sensor_id }, { label: 'Nhiệt độ', value: x => `${x.temperature} °C` },
    { label: 'Độ ẩm KK', value: x => `${x.air_humidity} %RH` }, { label: 'Áp suất', value: x => `${x.pressure} hPa` },
    { label: 'Ánh sáng', value: x => `${x.light} lux` }, { label: 'Độ ẩm đất', value: x => `${x.soil_moisture} %` }
  ]);
  drawTrend([...data.latest_measurements].reverse());
}

async function loadFarms() {
  const [farms, zones] = await Promise.all([request('/farms'), request('/zones')]);
  const withPosition = zones.filter(x => x.latitude !== null && x.longitude !== null);
  const map = withPosition.length ? `<div class="mini-map">${withPosition.map((zone, index) => `<span class="map-pin" style="left:${18 + (index * 57) % 70}%;top:${22 + (index * 31) % 55}%" title="${escapeHtml(zone.name)}">${escapeHtml(zone.name)}</span>`).join('')}</div>` : '<p class="muted">Chưa khai báo tọa độ zone.</p>';
  document.querySelector('#farms-table').innerHTML = `<h3>Farm</h3>${table(farms, [{label:'Tên',value:x=>x.name},{label:'Mô tả',value:x=>x.description || '—'}])}<h3>Zone</h3>${table(zones, [{label:'Farm ID',value:x=>x.farm_id},{label:'Tên',value:x=>x.name},{label:'Vĩ độ',value:x=>x.latitude ?? '—'},{label:'Kinh độ',value:x=>x.longitude ?? '—'}])}<h3>Sơ đồ vị trí mô phỏng</h3>${map}`;
}

async function loadMeasurements() {
  const rows = await request('/measurements');
  document.querySelector('#measurement-table').innerHTML = table(rows, [
    { label: 'Thời điểm đo', value: x => new Date(x.measured_at).toLocaleString() }, { label: 'Node', value: x => x.sensor_id },
    { label: '°C', value: x => x.temperature }, { label: '%RH', value: x => x.air_humidity }, { label: 'hPa', value: x => x.pressure }, { label: 'lux', value: x => x.light }, { label: 'Đất %', value: x => x.soil_moisture }, { label: 'Nguồn', value: x => x.source }
  ]);
}

async function loadDevices() {
  const [sensors, gateways] = await Promise.all([request('/devices/sensors'), request('/devices/gateways')]);
  document.querySelector('#devices-table').innerHTML = `<h3>Sensor nodes</h3>${table(sensors, [{label:'Mã',value:x=>x.sensor_id},{label:'Tên',value:x=>x.name},{label:'Zone',value:x=>x.zone_id},{label:'Trạng thái',value:x=>x.active?'Active':'Inactive'}])}<h3>Gateway</h3>${table(gateways, [{label:'Mã',value:x=>x.gateway_id},{label:'Tên',value:x=>x.name},{label:'Trạng thái',value:x=>x.active?'Active':'Inactive'},{label:'Lần cuối',value:x=>x.last_seen_at?new Date(x.last_seen_at).toLocaleString():'Chưa có'}])}`;
}

async function loadMissions() {
  const rows = await request('/missions');
  document.querySelector('#missions-table').innerHTML = table(rows, [{label:'ID',value:x=>x.id},{label:'Tên',value:x=>x.name},{label:'Trạng thái',value:x=>x.status},{label:'Gateway ID',value:x=>x.gateway_id},{label:'Node theo thứ tự',value:x=>x.points.map(p=>`${p.sort_order}. ${p.sensor_id} (${p.status})`).join(', ') || '—'},{label:'Tạo lúc',value:x=>new Date(x.created_at).toLocaleString()}]);
}

async function loadAlerts() {
  const rows = await request('/alerts');
  const canClose = localStorage.getItem('farmIotRole') === 'admin';
  const content = table(rows, [{label:'Loại',value:x=>x.type},{label:'Mức độ',value:x=>x.severity},{label:'Nội dung',value:x=>x.message},{label:'Trạng thái',value:x=>x.status},{label:'Thời gian',value:x=>new Date(x.created_at).toLocaleString()},{label:'Thao tác',value:x=>canClose && x.status === 'open' ? `Đóng alert #${x.id}` : '—'}]);
  document.querySelector('#alerts-table').innerHTML = content;
  if (canClose) document.querySelectorAll('#alerts-table td').forEach(cell => { const match = cell.textContent.match(/^Đóng alert #(\d+)$/); if (match) { cell.innerHTML = `<button class="close-alert" data-id="${match[1]}">Đóng</button>`; } });
  document.querySelectorAll('.close-alert').forEach(button => button.addEventListener('click', async () => { await request(`/alerts/${button.dataset.id}/close`, {method:'POST'}); await loadAlerts(); }));
}

async function loadReports() {
  const data = await request('/reports/summary');
  const labels = {temperature:'Nhiệt độ °C',air_humidity:'Độ ẩm KK %RH',pressure:'Áp suất hPa',light:'Ánh sáng lux',soil_moisture:'Độ ẩm đất %'};
  document.querySelector('#reports-table').innerHTML = `<div class="stats"><div class="stat"><span>Tổng bản đo</span><b>${data.total_measurements}</b></div><div class="stat"><span>Alert mở</span><b>${data.open_alerts}</b></div></div>${table(Object.entries(data.averages).map(([key,value]) => ({key,value})), [{label:'Chỉ số trung bình',value:x=>labels[x.key]},{label:'Giá trị',value:x=>x.value === null ? '—' : Number(x.value).toFixed(2)}])}`;
}

function logout() { token = null; localStorage.removeItem('farmIotToken'); localStorage.removeItem('farmIotRole'); document.querySelector('#app').hidden = true; document.querySelector('#login-card').hidden = false; }

async function showApp() {
  document.querySelector('#login-card').hidden = true; document.querySelector('#app').hidden = false;
  document.querySelector('#identity').textContent = `Đăng nhập: ${localStorage.getItem('farmIotUser')}`;
  await loadDashboard();
}

document.querySelector('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch(`${api}/auth/login`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({username: document.querySelector('#username').value, password: document.querySelector('#password').value}) });
  const error = document.querySelector('#login-error');
  if (!response.ok) { error.textContent = 'Không thể đăng nhập.'; return; }
  const data = await response.json(); token = data.access_token; localStorage.setItem('farmIotToken', token); localStorage.setItem('farmIotUser', `${data.username} (${data.role})`); localStorage.setItem('farmIotRole', data.role); error.textContent = ''; showApp();
});

document.querySelector('#logout').addEventListener('click', logout);
document.querySelectorAll('nav button').forEach(button => button.addEventListener('click', async () => {
  document.querySelectorAll('nav button').forEach(x => x.classList.remove('active')); button.classList.add('active');
  document.querySelectorAll('.view').forEach(x => x.hidden = true); document.querySelector(`#${button.dataset.view}`).hidden = false;
  const loaders = {dashboard:loadDashboard, farms:loadFarms, measurements:loadMeasurements, devices:loadDevices, missions:loadMissions, alerts:loadAlerts, reports:loadReports};
  try { await loaders[button.dataset.view](); } catch (error) { console.error(error); alert('Không thể tải dữ liệu. Kiểm tra lại backend và tài khoản.'); }
}));

if (token) showApp().catch(logout);
