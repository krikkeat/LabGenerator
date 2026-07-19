// labctl web UI — Labctl App design (claude.ai/design) บน preact + htm, no build step
import { h, render } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (res.status === 401) {
    dispatchEvent(new Event('labctl:unauth'));
    throw new Error('ต้องเข้าสู่ระบบก่อน');
  }
  if (!res.ok) {
    let msg = `${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* noop */ }
    throw new Error(msg);
  }
  return res.json();
}

// ราคาโดยประมาณ ap-southeast-1 on-demand (ตรงกับตารางใน README)
const HOURLY = {
  't3.micro': 0.0132, 't3.small': 0.0264, 't3.medium': 0.0528,
  't3.large': 0.1056, 't3.xlarge': 0.2112,
};

const OS_LABELS = {
  'amazon-linux-2023': 'Amazon Linux 2023', 'amazon-linux-2': 'Amazon Linux 2',
  'ubuntu-20.04': 'Ubuntu 20.04 LTS', 'ubuntu-22.04': 'Ubuntu 22.04 LTS',
  'ubuntu-24.04': 'Ubuntu 24.04 LTS',
  'debian-11': 'Debian 11 (Bullseye)', 'debian-12': 'Debian 12 (Bookworm)',
  'suse-15': 'SUSE Linux Enterprise 15',
  'windows-2025': 'Windows Server 2025', 'windows-2022': 'Windows Server 2022',
  'windows-2019': 'Windows Server 2019', 'windows-2016': 'Windows Server 2016',
};

const STATUS_LABEL = {
  active: 'ACTIVE', provisioning: 'PROVISIONING', queued: 'QUEUED',
  destroying: 'DESTROYING', destroyed: 'DESTROYED', error: 'ERROR',
};

function fmtExpire(expiresAt) {
  if (!expiresAt) return null;
  const ms = expiresAt * 1000 - Date.now();
  if (ms <= 0) return 'กำลังจะถูกลบ';
  const hh = Math.floor(ms / 3600000), mm = Math.round((ms % 3600000) / 60000);
  return hh > 0 ? `⏳ ${hh} ชม ${mm} นาที` : `⏳ ${mm} นาที`;
}

function useRoute() {
  const [route, setRoute] = useState(location.hash.slice(1) || '/');
  useEffect(() => {
    const onHash = () => setRoute(location.hash.slice(1) || '/');
    addEventListener('hashchange', onHash);
    return () => removeEventListener('hashchange', onHash);
  }, []);
  return route;
}

const nav = (path) => { location.hash = path; };

let PREFILL = null; // config สำหรับ "สร้างซ้ำจาก config เดิม"

function Pill({ status }) {
  return html`<span class="pill ${status}"><span class="dot"></span>${STATUS_LABEL[status] || status}</span>`;
}

function ErrBox({ err }) {
  return err ? html`<div class="err">${err}</div>` : null;
}

// ---------------------------------------------------------------------------
// login / first-time setup
// ---------------------------------------------------------------------------

function BrandPanel() {
  return html`
    <div class="login-brand">
      <div class="logo-row">
        <div class="logo">lc</div>
        <div><div style="font:700 16px var(--mono)">labctl</div>
        <div style="font:500 10.5px var(--sans); opacity:.75">Hands-on Lab Generator</div></div>
      </div>
      <div class="headline">สร้าง lab environment บน AWS สำหรับ training — อัตโนมัติทั้งระบบ</div>
      <div class="points">
        <div><span class="n">01</span><span>VM แยกหนึ่งเครื่องต่อผู้เรียนหนึ่งคน พร้อม key ของตัวเอง</span></div>
        <div><span class="n">02</span><span>handout + lab guide สร้างให้อัตโนมัติ พร้อม zip ส่งผู้เรียน</span></div>
        <div><span class="n">03</span><span>จบ training ลบทิ้งทั้งหมดในคำสั่งเดียว — ไม่มีอะไรค้างคิดเงิน</span></div>
      </div>
      <div class="foot">v2.0 · terraform + aws · local only</div>
    </div>`;
}

function PwInput({ value, onInput, onKeyDown, placeholder, show, onToggle }) {
  return html`
    <div style="position:relative">
      <input type=${show ? 'text' : 'password'} value=${value} onInput=${onInput}
        onKeyDown=${onKeyDown} placeholder=${placeholder} autocomplete="off"
        style="padding-right:74px" />
      <button type="button" class="btn tiny" onClick=${onToggle}
        style="position:absolute; right:10px; top:50%; transform:translateY(-50%); background:var(--panel)">
        ${show ? 'ซ่อน' : 'แสดง'}</button>
    </div>`;
}

function SetupScreen({ onDone }) {
  const [f, setF] = useState({ u: '', p: '', c: '', show: false, busy: false, err: '' });
  const canGo = f.u.trim() && f.p && f.c && !f.busy;

  async function doCreate() {
    if (!canGo) return;
    if (!/^[a-z0-9-]{1,30}$/.test(f.u.trim().toLowerCase())) {
      return setF({ ...f, err: 'username ใช้ได้เฉพาะ a-z, 0-9 และ -' });
    }
    if (f.p.length < 8) return setF({ ...f, err: 'รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร' });
    if (f.p !== f.c) return setF({ ...f, err: 'รหัสผ่านทั้งสองช่องไม่ตรงกัน' });
    setF({ ...f, busy: true, err: '' });
    try {
      await api('/auth/setup', {
        method: 'POST',
        body: JSON.stringify({ username: f.u.trim().toLowerCase(), password: f.p }),
      });
      onDone(f.u.trim().toLowerCase());
    } catch (e) {
      setF({ ...f, busy: false, err: e.message });
    }
  }

  return html`
    <div class="login-wrap">
      <div class="login-card">
        <${BrandPanel} />
        <div class="login-form">
          <div style="font:700 20px var(--sans)">ตั้งค่าครั้งแรก</div>
          <div class="page-sub" style="margin:4px 0 26px">ยังไม่มีบัญชีผู้ดูแลในเครื่องนี้ — สร้างบัญชีก่อนเริ่มใช้งาน</div>
          <div class="field"><label>Username</label>
            <input type="text" value=${f.u} onInput=${(e) => setF({ ...f, u: e.target.value, err: '' })}
              placeholder="instructor" autocomplete="off" /></div>
          <div class="field"><label>Password</label>
            <${PwInput} value=${f.p} show=${f.show} placeholder="อย่างน้อย 8 ตัวอักษร"
              onInput=${(e) => setF({ ...f, p: e.target.value, err: '' })}
              onToggle=${() => setF({ ...f, show: !f.show })} /></div>
          <div class="field" style="margin-bottom:8px"><label>Confirm password</label>
            <${PwInput} value=${f.c} show=${f.show} placeholder="พิมพ์รหัสผ่านซ้ำ"
              onInput=${(e) => setF({ ...f, c: e.target.value, err: '' })}
              onKeyDown=${(e) => { if (e.key === 'Enter') doCreate(); }}
              onToggle=${() => setF({ ...f, show: !f.show })} /></div>
          <div class="help" style="margin-bottom:16px">เก็บเป็น hash ในเครื่องนี้เท่านั้น — ไม่ถูกส่งออกไปไหน</div>
          <${ErrBox} err=${f.err} />
          <button class="btn primary block" disabled=${!canGo} onClick=${doCreate}>
            ${f.busy ? 'กำลังสร้างบัญชี…' : 'สร้างบัญชีและเข้าสู่ระบบ →'}</button>
        </div>
      </div>
    </div>`;
}

function LoginScreen({ onAuthed, prefillU }) {
  const [f, setF] = useState({ u: prefillU || '', p: '', show: false, remember: true, busy: false, err: false });
  const canGo = f.u.trim() && f.p && !f.busy;

  async function doLogin() {
    if (!canGo) return;
    setF({ ...f, busy: true, err: false });
    try {
      const r = await api('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: f.u.trim(), password: f.p, remember: f.remember }),
      });
      onAuthed(r.username);
    } catch (e) {
      setF({ ...f, busy: false, err: true, p: '' });
    }
  }

  return html`
    <div class="login-wrap">
      <div class="login-card">
        <${BrandPanel} />
        <div class="login-form">
          <div style="font:700 20px var(--sans)">เข้าสู่ระบบ</div>
          <div class="page-sub" style="margin:4px 0 26px">สำหรับผู้สอน/ผู้ดูแล — ระบบรันบนเครื่องของคุณเท่านั้น (127.0.0.1)</div>
          <div class="field"><label>Username</label>
            <input type="text" value=${f.u} onInput=${(e) => setF({ ...f, u: e.target.value, err: false })}
              placeholder="instructor" autocomplete="off" /></div>
          <div class="field" style="margin-bottom:8px"><label>Password</label>
            <${PwInput} value=${f.p} show=${f.show} placeholder="••••••••"
              onInput=${(e) => setF({ ...f, p: e.target.value, err: false })}
              onKeyDown=${(e) => { if (e.key === 'Enter') doLogin(); }}
              onToggle=${() => setF({ ...f, show: !f.show })} /></div>
          ${f.err && html`<div class="err">ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง — ลองอีกครั้ง</div>`}
          <label class="checkbox" style="margin:10px 0 22px">
            <input type="checkbox" checked=${f.remember}
              onChange=${(e) => setF({ ...f, remember: e.target.checked })} />
            จำไว้ในเครื่องนี้ — ปิดโปรแกรมแล้วไม่ต้องเข้าสู่ระบบใหม่
          </label>
          <button class="btn primary block" disabled=${!canGo} onClick=${doLogin}>
            ${f.busy ? 'กำลังตรวจสอบ…' : 'เข้าสู่ระบบ →'}</button>
          <div class="help" style="margin-top:18px; text-align:center">
            credential ถูกตรวจกับเครื่องนี้เท่านั้น — ไม่ถูกส่งออกนอกเครื่องและไม่ลง log</div>
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// shell (sidebar + main)
// ---------------------------------------------------------------------------

function Sidebar({ route, labs, aws, theme, onToggleTheme, onLogout }) {
  const activeCount = labs.filter(l => l.status === 'active').length;
  const items = [
    { icon: '▤', label: 'Labs ของคุณ', badge: activeCount, r: '/', match: (rt) => rt === '/' || rt.startsWith('/lab/') || rt.startsWith('/job/') },
    { icon: '＋', label: 'สร้าง lab ใหม่', badge: 0, r: '/new', match: (rt) => rt === '/new' },
    { icon: '⚙', label: 'การเชื่อมต่อ AWS', badge: 0, r: '/aws', match: (rt) => rt === '/aws' },
  ];
  return html`
    <div class="sidebar">
      <div class="logo-row" onClick=${() => nav('/')}>
        <div class="logo">lc</div>
        <div><div class="brand-name">labctl</div><div class="brand-sub">Hands-on Lab Generator</div></div>
      </div>
      <nav class="nav">
        ${items.map(n => html`
          <button key=${n.r} class="nav-item ${n.match(route) ? 'active' : ''}" onClick=${() => nav(n.r)}>
            <span class="icon">${n.icon}</span><span>${n.label}</span>
            ${n.badge > 0 && html`<span class="badge">${n.badge}</span>`}
          </button>`)}
      </nav>
      <div class="aws-card ${aws?.connected ? 'on' : ''}" onClick=${() => nav('/aws')}>
        <div class="line1"><span class="dot"></span>
          ${aws === null ? 'กำลังเช็ค AWS…' : aws.connected ? 'เชื่อมต่อ AWS แล้ว' : 'ยังไม่เชื่อมต่อ AWS'}</div>
        ${aws?.connected && html`<div class="line2">${aws.account} · ${aws.region}</div>`}
      </div>
      <div class="side-foot">
        <span>${theme === 'light' ? '☀ Light' : '☾ Dark'}</span>
        <button class="theme-toggle ${theme === 'dark' ? 'dark' : ''}" onClick=${onToggleTheme}
          aria-label="สลับธีม"><span class="knob"></span></button>
        <button class="logout-link" onClick=${onLogout}>ออกจากระบบ</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// home — lab cards
// ---------------------------------------------------------------------------

function labCost(l) {
  return (HOURLY[l.instance_type] || 0) * (l.users || 0);
}

function Home({ labs, loaded }) {
  const active = labs.filter(l => l.status === 'active');
  const vmCount = active.reduce((a, l) => a + (l.users || 0), 0);
  const liveCost = active.reduce((a, l) => a + labCost(l), 0);
  const destroyed = labs.filter(l => l.status === 'destroyed');
  const shown = labs.filter(l => l.status !== 'destroyed');

  async function recreate(l) {
    try {
      const d = await api(`/labs/${l.lab_name}`);
      PREFILL = d.config;
      nav('/new');
    } catch (e) { /* ไม่มี config — เข้า wizard เปล่า */ PREFILL = null; nav('/new'); }
  }

  function open(l) {
    if (['provisioning', 'queued', 'destroying'].includes(l.status) && l.job) {
      nav(`/job/${l.job.id}?lab=${l.lab_name}`);
    } else if (l.status === 'error' && l.job) {
      nav(`/job/${l.job.id}?lab=${l.lab_name}`);
    } else {
      nav(`/lab/${l.lab_name}`);
    }
  }

  return html`
    <div class="screen">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px">
        <div>
          <h1 class="page-title">Labs ของคุณ</h1>
          <div class="page-sub">${active.length} active · ${vmCount} VM กำลังรัน · ~$${liveCost.toFixed(2)}/ชม.</div>
        </div>
        <button class="btn primary" onClick=${() => { PREFILL = null; nav('/new'); }}>＋ สร้าง lab ใหม่</button>
      </div>
      <div class="live-note">● live · อัปเดตอัตโนมัติทุก 8 วินาที</div>
      <div class="lab-list">
        ${!loaded && html`<div class="empty">กำลังโหลด…</div>`}
        ${loaded && shown.length === 0 && destroyed.length === 0
          && html`<div class="empty">ยังไม่มี lab — กด "สร้าง lab ใหม่" เพื่อเริ่ม</div>`}
        ${shown.map(l => {
          const busy = ['provisioning', 'queued', 'destroying'].includes(l.status);
          const iconCls = l.status === 'error' ? 'crit' : busy ? 'warn' : '';
          const initials = l.lab_name.slice(0, 2).toUpperCase();
          const subBits = [l.product_name || 'Custom OS', OS_LABELS[l.os] || l.os,
            l.instance_type, `${l.users} ผู้เรียน`].filter(Boolean);
          return html`
          <button key=${l.lab_name} class="lab-card ${l.status === 'error' ? 'error' : ''}" onClick=${() => open(l)}>
            <div class="lab-icon ${iconCls}">${initials}</div>
            <div class="lab-main">
              <div class="lab-title-row">
                <span class="lab-name">${l.lab_name}</span>
                <${Pill} status=${l.status} />
              </div>
              ${l.status === 'error'
                ? html`<div class="lab-sub bad">${l.job?.error || 'งานล้มเหลว'} · กดเปิดเพื่อดู log</div>`
                : busy
                  ? html`<div class="lab-sub">${l.job?.kind === 'destroy' ? 'กำลังลบ resource บน AWS…' : 'กำลังสร้างเครื่องบน AWS…'} กดเพื่อดู log สด</div>`
                  : html`<div class="lab-sub">${subBits.join(' · ')}</div>`}
            </div>
            ${l.status === 'active' && html`
              <div class="lab-right">
                <div class="lab-expire">${fmtExpire(l.expires_at) || (l.expire_date ? `ถึง ${l.expire_date}` : '')}</div>
                <div class="lab-cost">$${labCost(l).toFixed(3)}/ชม.</div>
              </div>`}
            <span class="lab-cta">${busy ? 'ดู log →' : 'เปิด →'}</span>
          </button>`;
        })}
        ${destroyed.map(l => html`
          <button key=${'d-' + l.lab_name} class="recreate-card" onClick=${() => recreate(l)}>
            <span style="font:600 14px var(--mono)">${l.lab_name}</span>
            ถูกลบแล้ว · <span style="color:var(--accent); font-weight:600">สร้างซ้ำจาก config เดิม →</span>
          </button>`)}
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// wizard
// ---------------------------------------------------------------------------

const CUSTOM = '__custom__';

function Wizard({ aws }) {
  const [catalog, setCatalog] = useState(null);
  const [step, setStep] = useState(0);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const pre = PREFILL; // อ่านครั้งเดียวตอน mount

  const [product, setProduct] = useState(pre ? (pre.product || CUSTOM) : null);
  const [form, setForm] = useState({
    lab_name: pre?.lab_name || '', region: pre?.region || 'ap-southeast-1',
    users: pre?.users || ['user01', 'user02', 'user03'],
    allowed_cidr: pre?.allowed_cidr || '', expire_date: pre?.expire_date || '',
    os: pre?.os || 'ubuntu-24.04',
    instance_type: '', root_volume_gb: '', extra_open_ports: '',
    ttl_hours: pre?.ttl_hours != null ? String(pre.ttl_hours) : '8',
    byol: !!pre?.custom_ami, custom_ami: pre?.custom_ami || '',
    custom_username: pre?.custom_username || 'Administrator',
    custom_is_windows: pre?.custom_is_windows ?? true,
  });
  const [params, setParams] = useState(pre?.product_params || {});
  useEffect(() => { PREFILL = null; }, []);

  useEffect(() => { api('/products').then(setCatalog).catch(e => setErr(e.message)); }, []);

  if (!catalog) return html`<div class="screen"><div class="card"><div class="empty">${err || 'กำลังโหลด product catalog…'}</div></div></div>`;

  const meta = catalog.products.find(p => p.id === product);
  const isCustom = product === CUSTOM;
  const paramSpecs = meta ? Object.entries(meta.params) : [];
  const requiredOk = paramSpecs.every(([k, spec]) => !spec.required || (params[k] || '').trim());
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const effOS = isCustom ? form.os : meta?.os;
  const effType = form.instance_type || meta?.instance_type || 't3.medium';
  const hourly = HOURLY[effType] || 0;
  const nUsers = form.users.length;
  const ttlNum = parseFloat(form.ttl_hours) || 0;

  const stepSeq = isCustom ? [0, 1, 3] : [0, 1, 2, 3];
  const pos = Math.max(0, stepSeq.indexOf(step));
  const stepTitles = ['เลือก product', 'ตั้งค่า lab', 'ข้อมูล product', 'ตรวจสอบ'];
  const byolOk = !form.byol ||
    (/^ami-[0-9a-f]{8,17}$/.test(form.custom_ami.trim()) && form.custom_username.trim());
  const canNext = {
    0: product !== null,
    1: /^[a-z0-9-]{1,32}$/.test(form.lab_name) && nUsers > 0 && byolOk,
    2: requiredOk,
  }[step] ?? true;

  async function submit() {
    setBusy(true); setErr(null);
    const body = {
      lab_name: form.lab_name.trim(), users: form.users,
      region: form.region || null, allowed_cidr: form.allowed_cidr || null,
      expire_date: form.expire_date || null,
      ttl_hours: form.ttl_hours === '' ? 8 : parseFloat(form.ttl_hours),
    };
    if (isCustom) {
      if (form.byol) {
        body.custom_ami = form.custom_ami.trim();
        body.custom_username = form.custom_username.trim();
        body.custom_is_windows = form.custom_is_windows;
      } else body.os = form.os;
    } else {
      body.product = product;
      body.product_params = Object.fromEntries(
        paramSpecs.map(([k]) => [k, (params[k] || '').trim()]).filter(([, v]) => v));
    }
    if (form.instance_type) body.instance_type = form.instance_type;
    if (form.root_volume_gb) body.root_volume_gb = parseInt(form.root_volume_gb, 10);
    if (form.extra_open_ports.trim()) {
      body.extra_open_ports = form.extra_open_ports.split(',')
        .map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
    }
    try {
      const r = await api('/labs', { method: 'POST', body: JSON.stringify(body) });
      nav(`/job/${r.job_id}?lab=${r.lab_name}`);
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  return html`
    <div class="screen">
      <h1 class="page-title" style="margin-bottom:16px">สร้าง lab ใหม่ ${' '}
        <span class="mono muted" style="font:500 12.5px var(--mono)">· ขั้นตอน ${pos + 1}/${stepSeq.length} — ${stepTitles[step]}</span></h1>
      <div class="wiz-wrap">
        <div class="wiz-main card">
          <div class="stepper">
            ${stepSeq.map((s, i) => html`<div class="s ${i < pos ? 'done' : i === pos ? 'now' : ''}"></div>`)}
          </div>
          ${aws && !aws.connected && html`
            <div class="callout" style="margin:0 0 16px; display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap">
              <span>ยังไม่ได้เชื่อมต่อ AWS — ต้องเชื่อมต่อก่อนจึงจะสร้าง lab ได้</span>
              <button class="btn primary small" onClick=${() => nav('/aws')}>เชื่อมต่อ AWS →</button>
            </div>`}
          <${ErrBox} err=${err} />

          ${step === 0 && html`
            <div class="step-title">Lab นี้สอนอะไร?</div>
            <div class="prod-grid">
              ${catalog.products.map(p => html`
                <button key=${p.id} class="prod-card ${product === p.id ? 'selected' : ''}" onClick=${() => setProduct(p.id)}>
                  <div class="pname">${p.product_name}</div>
                  <div class="pdesc">${p.description}</div>
                  <div class="pspec"><span>OS ${p.os}</span><span>${p.instance_type} · ${p.root_volume_gb}GB</span></div>
                </button>`)}
              <button class="prod-card ${product === CUSTOM ? 'selected' : ''}" onClick=${() => setProduct(CUSTOM)}>
                <div class="pname">Custom OS lab</div>
                <div class="pdesc">ไม่ติดตั้ง product agent — เลือก OS เอง (Linux/Windows Server) หรือใช้ AMI ของตัวเอง (BYOL เช่น Windows 10/11)</div>
                <div class="pspec"><span>${catalog.supported_os.length} OS ให้เลือก + custom AMI (BYOL)</span><span>กำหนดสเปคเองได้</span></div>
              </button>
            </div>`}

          ${step === 1 && html`
            <div class="step-title">ตั้งค่า lab</div>
            <div class="row">
              <div class="field"><label>lab_name <span class="req">*</span></label>
                <input type="text" value=${form.lab_name} onInput=${set('lab_name')} placeholder="เช่น trendai-training" />
                ${form.lab_name && !/^[a-z0-9-]{1,32}$/.test(form.lab_name)
                  && html`<div class="help bad">ใช้ได้เฉพาะ a-z, 0-9, - (ไม่เกิน 32 ตัว)</div>`}</div>
              <div class="field"><label>region</label>
                <input type="text" value=${form.region} onInput=${set('region')} /></div>
            </div>
            <${UserEditor} users=${form.users} onChange=${(users) => setForm({ ...form, users })} />
            <div class="row">
              <div class="field"><label>allowed_cidr</label>
                <input type="text" value=${form.allowed_cidr} onInput=${set('allowed_cidr')} placeholder="0.0.0.0/0" />
                <div class="help">จำกัดให้แคบที่สุด เช่น IP ของสถานที่อบรม — เลี่ยง 0.0.0.0/0 ถ้าทำได้</div></div>
              <div class="field" style="flex:0 0 190px; min-width:170px"><label>session หมดอายุใน (ชม.)</label>
                <input type="number" min="0" max="336" step="0.5" value=${form.ttl_hours} onInput=${set('ttl_hours')} />
                <div class="help">ครบแล้ว<b>ลบอัตโนมัติ</b> · 0 = ปิด</div></div>
            </div>
            ${isCustom && html`
              <div class="field" style="max-width:280px"><label>os</label>
                <select value=${form.os} onChange=${set('os')} disabled=${form.byol}>
                  ${catalog.supported_os.map(o => html`<option value=${o}>${OS_LABELS[o] || o}</option>`)}
                </select></div>
              <div class="field"><label class="checkbox">
                <input type="checkbox" checked=${form.byol}
                  onChange=${(e) => setForm({ ...form, byol: e.target.checked })} />
                ใช้ AMI ของตัวเอง (BYOL) — จำเป็นสำหรับ Windows 10/11 ที่ import license เอง
              </label></div>
              ${form.byol && html`
                <div class="row">
                  <div class="field"><label>custom AMI ID <span class="req">*</span></label>
                    <input type="text" value=${form.custom_ami} onInput=${set('custom_ami')} placeholder="ami-0123456789abcdef0" />
                    ${form.custom_ami && !/^ami-[0-9a-f]{8,17}$/.test(form.custom_ami.trim())
                      && html`<div class="help bad">รูปแบบต้องเป็น ami-xxxxxxxx</div>`}</div>
                  <div class="field"><label>username <span class="req">*</span></label>
                    <input type="text" value=${form.custom_username} onInput=${set('custom_username')} /></div>
                </div>
                <div class="field"><label class="checkbox">
                  <input type="checkbox" checked=${form.custom_is_windows}
                    onChange=${(e) => setForm({ ...form, custom_is_windows: e.target.checked })} />
                  AMI นี้เป็น Windows (ใช้ RDP + ถอดรหัสผ่านจาก key)
                </label></div>`}`}
            <details style="margin-top:4px">
              <summary style="font:500 12.5px var(--mono); color:var(--ink-dim); cursor:pointer; margin-bottom:10px">ขั้นสูง (instance_type, root_volume_gb, extra_open_ports)</summary>
              <div class="row">
                <div class="field"><label>instance_type</label>
                  <input type="text" value=${form.instance_type} onInput=${set('instance_type')}
                    placeholder=${meta?.instance_type || 't3.medium'} /></div>
                <div class="field"><label>root_volume_gb</label>
                  <input type="number" value=${form.root_volume_gb} onInput=${set('root_volume_gb')}
                    placeholder=${meta?.root_volume_gb || 30} /></div>
                <div class="field"><label>extra_open_ports</label>
                  <input type="text" value=${form.extra_open_ports} onInput=${set('extra_open_ports')} placeholder="80, 8080" /></div>
              </div>
            </details>`}

          ${step === 2 && meta && html`
            <div class="step-title" style="margin-bottom:6px">${meta.product_name} — ข้อมูล tenant</div>
            <div class="page-sub" style="margin-bottom:18px">ค่าเหล่านี้จะถูกเติมลงใน bootstrap script และ lab guide แทนที่ placeholder ใน template</div>
            ${paramSpecs.map(([key, spec]) => html`
              <div class="field" key=${key}>
                <label>${key} ${spec.required
                  ? html`<span class="req">*</span>`
                  : html`<span class="opt">optional${spec.default ? ` — default: ${spec.default}` : ''}</span>`}</label>
                <input type="text" value=${params[key] || ''}
                  onInput=${(e) => setParams({ ...params, [key]: e.target.value })} />
                ${spec.description && html`<div class="help">${spec.description}</div>`}
              </div>`)}`}

          ${step === 3 && html`
            <div class="step-title">ตรวจสอบก่อนสร้าง</div>
            <div class="review-rows">
              <div class="review-row"><span class="k">Lab</span>
                <span class="v">${form.lab_name || '—'} ${meta ? `— ${meta.product_name}` : form.byol ? '(custom AMI)' : '(custom OS)'}</span></div>
              <div class="review-row"><span class="k">เครื่อง</span>
                <span class="v">${nUsers} × ${isCustom && form.byol ? `AMI ${form.custom_ami}` : (OS_LABELS[effOS] || effOS)}, ${effType}</span></div>
              <div class="review-row"><span class="k">Auto-expire</span>
                <span class="v">${ttlNum > 0 ? `ลบอัตโนมัติหลัง ${form.ttl_hours} ชั่วโมง` : 'ปิด — ต้องกด Destroy เองตอนจบ'}</span></div>
              <div class="review-row"><span class="k">Region</span><span class="v">${form.region}</span></div>
              <div class="review-row"><span class="k">ผู้เรียน</span><span class="v">${form.users.join(', ')}</span></div>
              ${!isCustom && paramSpecs.filter(([, s]) => s.required).map(([k]) => html`
                <div class="review-row" key=${k}><span class="k">${k}</span><span class="v">${params[k] || '—'}</span></div>`)}
              <div class="review-row"><span class="k">ค่าใช้จ่าย</span>
                <span class="v">~$${(hourly * nUsers).toFixed(3)}/ชม. รวม · ~$${(hourly * nUsers * (ttlNum || 8)).toFixed(2)} ต่อ session ${ttlNum || 8} ชม.</span></div>
            </div>
            <div class="callout">อย่าลืม <b>ลบ lab (Destroy)</b> เมื่อจบ training — VM, network และ key ทั้งหมดจะถูกลบ ไม่มีอะไรค้างคิดเงินต่อ</div>`}

          <div class="btnbar">
            ${pos > 0 && html`<button class="btn ghost" style="margin-right:auto"
              onClick=${() => setStep(stepSeq[pos - 1])}>← ย้อนกลับ</button>`}
            <button class="btn ghost" onClick=${() => nav('/')}>ยกเลิก</button>
            ${pos < stepSeq.length - 1
              ? html`<button class="btn primary" disabled=${!canNext}
                  onClick=${() => setStep(stepSeq[pos + 1])}>ถัดไป →</button>`
              : html`<button class="btn primary" disabled=${busy || (aws && !aws.connected)}
                  onClick=${submit}>${busy ? 'กำลังส่ง…' : '🚀 สร้าง lab'}</button>`}
          </div>
        </div>

        <div class="cost-panel">
          <div class="card" style="padding:18px 20px">
            <div class="cost-title">ค่าใช้จ่ายโดยประมาณ · LIVE</div>
            <div class="cost-big">$${(hourly * nUsers).toFixed(3)}<span class="unit">/ชม.</span></div>
            <div class="cost-session">≈ $${(hourly * nUsers * (ttlNum || 8)).toFixed(2)} ต่อ session ${form.ttl_hours || 8} ชม.</div>
            <div class="cost-rows">
              <div><span>เครื่อง</span><span class="v">${nUsers} × ${effType}</span></div>
              <div><span>ราคา/เครื่อง</span><span class="v">$${hourly.toFixed(4)}/ชม.</span></div>
              <div><span>OS</span><span class="v">${isCustom && form.byol ? 'custom AMI' : (OS_LABELS[effOS] || effOS || (product ? '' : 'ยังไม่เลือก'))}</span></div>
              <div><span>Auto-expire</span><span class="v">${ttlNum > 0 ? `${form.ttl_hours} ชม.` : 'ปิด'}</span></div>
            </div>
            <div class="cost-foot">on-demand ap-southeast-1 + EBS gp3 เล็กน้อย</div>
          </div>
        </div>
      </div>
    </div>`;
}

function UserEditor({ users, onChange }) {
  const [draft, setDraft] = useState('');
  const [genN, setGenN] = useState('');
  const add = () => {
    const v = draft.trim().toLowerCase();
    if (v && /^[a-z0-9-]{1,30}$/.test(v) && !users.includes(v)) onChange([...users, v]);
    setDraft('');
  };
  const generate = () => {
    const n = parseInt(genN, 10);
    if (!n || n < 1 || n > 200) return;
    onChange(Array.from({ length: n }, (_, i) => `user${String(i + 1).padStart(2, '0')}`));
    setGenN('');
  };
  return html`
    <div class="field">
      <label>users <span class="req">*</span> <span class="opt">— ${users.length} คน = ${users.length} VM</span></label>
      <div class="tagrow">
        ${users.map(u => html`
          <span class="tag" key=${u}>${u}
            <button title="ลบ ${u}" onClick=${() => onChange(users.filter(x => x !== u))}>×</button></span>`)}
        <input type="text" value=${draft} placeholder="เพิ่มชื่อ… (Enter)"
          onInput=${(e) => setDraft(e.target.value)}
          onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          onBlur=${add} />
        <input type="number" value=${genN} placeholder="userNN × N"
          onInput=${(e) => setGenN(e.target.value)}
          onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); generate(); } }} />
        <button class="btn ghost small" onClick=${generate}>สร้างรายชื่อ</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// job — provisioning log
// ---------------------------------------------------------------------------

const PHASES = {
  create: [
    ['init', 'เตรียม Terraform'], ['workspace', 'สร้าง workspace ของ lab'],
    ['apply', 'สร้าง VPC + VM + key pairs บน AWS'], ['export', 'สร้าง handout + zip'],
    ['done', 'เสร็จสิ้น'],
  ],
  refresh: [['refresh', 'ดึงสถานะล่าสุดจาก AWS'], ['export', 'สร้าง handout ใหม่'], ['done', 'เสร็จสิ้น']],
  destroy: [['destroy', 'ลบ resource ทั้งหมดบน AWS'], ['cleanup', 'ลบ key/handout ในเครื่อง'], ['done', 'เสร็จสิ้น']],
};

function logClass(l) {
  if (/^\$ /.test(l)) return 'cmd';
  if (/Error|ล้มเหลว/i.test(l)) return 'bad';
  if (/Creation complete|Apply complete|Destruction complete|Destroy complete|เสร็จสิ้น|✓/.test(l)) return 'ok';
  if (/Still creating|Still destroying|Refreshing/.test(l)) return 'dim';
  return '';
}

function JobView({ jobId, labName }) {
  const [job, setJob] = useState(null);
  const [lines, setLines] = useState([]);
  const [phase, setPhase] = useState(null);
  const [done, setDone] = useState(null);
  const [elapsed, setElapsed] = useState('');
  const logRef = useRef(null);

  useEffect(() => {
    api(`/jobs/${jobId}`).then(setJob).catch(() => {});
    const es = new EventSource(`/api/jobs/${jobId}/events`);
    es.addEventListener('log', (e) => setLines(ls => [...ls.slice(-600), JSON.parse(e.data)]));
    es.addEventListener('phase', (e) => setPhase(JSON.parse(e.data).phase));
    es.addEventListener('done', (e) => {
      setDone(JSON.parse(e.data)); es.close();
      api(`/jobs/${jobId}`).then(j => {
        setJob(j);
        if (j.started_at && j.finished_at) {
          const s = Math.round(j.finished_at - j.started_at);
          setElapsed(`${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} นาที`);
        }
      }).catch(() => {});
    });
    return () => es.close();
  }, [jobId]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines]);

  const kind = job?.kind || 'create';
  const phases = PHASES[kind] || PHASES.create;
  const phaseIdx = phases.findIndex(([p]) => p === phase);
  const pct = done ? 100 : phaseIdx < 0 ? 4 : Math.round(((phaseIdx + 0.5) / phases.length) * 100);
  const failed = done?.state === 'error';

  return html`
    <div class="screen" style="max-width:920px">
      <h1 class="page-title" style="margin-bottom:16px">
        ${done ? (failed ? 'งานล้มเหลว' : kind === 'destroy' ? 'ลบเสร็จแล้ว' : 'สร้างเสร็จแล้ว')
          : kind === 'destroy' ? 'กำลังลบ' : kind === 'refresh' ? 'กำลัง refresh' : 'กำลังสร้าง'}
        ${' '}<span class="mono muted" style="font:500 14px var(--mono)">${labName}</span></h1>
      <div class="card">
        <div class="job-progress"><div class="fill" style="width:${pct}%; ${failed ? 'background:var(--crit)' : ''}"></div></div>
        <div class="checklist">
          ${phases.map(([p, label], i) => {
            const isDone = (done && !failed) || i < phaseIdx;
            const isNow = !done && i === phaseIdx;
            return html`
            <div class="check ${isDone ? 'done' : isNow ? 'now' : ''}" key=${p}>
              <span class="mark">${isDone ? '✓' : isNow ? '…' : ''}</span>${label}
              ${isNow && html`<span class="now-dot"></span>`}
            </div>`;
          })}
        </div>
        ${failed && html`<div class="err">งานล้มเหลว: ${done.error || 'ดูรายละเอียดใน log ด้านล่าง'}</div>`}
        <div class="logpane" ref=${logRef}>
          ${lines.length === 0 ? html`<span class="dim">รอ log จาก terraform…</span>`
            : lines.map((l, i) => html`<div key=${i} class=${logClass(l)}>${l}</div>`)}
          ${!done && html`<span class="cursor">▌</span>`}
        </div>
        <div class="btnbar">
          ${done
            ? html`
              ${!failed && html`<span class="job-done-note">✓ เสร็จสิ้น${elapsed ? `ใน ${elapsed}` : ''}</span>`}
              <button class="btn ghost" onClick=${() => nav('/')}>← หน้าแรก</button>
              ${kind !== 'destroy' && html`<button class="btn primary"
                onClick=${() => nav(`/lab/${labName}`)}>เปิด dashboard ของ lab →</button>`}`
            : html`
              <span class="job-run-note">ปิดหน้านี้ได้ — งานยังรันต่อเบื้องหลัง</span>
              <button class="btn ghost" onClick=${() => nav('/')}>← กลับหน้าแรก</button>`}
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// dashboard — machine cards
// ---------------------------------------------------------------------------

function Dashboard({ labName }) {
  const [lab, setLab] = useState(null);
  const [err, setErr] = useState(null);
  const [revealed, setRevealed] = useState({});
  const [copied, setCopied] = useState({});
  const [destroyOpen, setDestroyOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  const load = useCallback(() => {
    api(`/labs/${labName}`).then(setLab).catch(e => setErr(e.message));
  }, [labName]);
  useEffect(load, [load]);

  function copyIp(m) {
    try { navigator.clipboard.writeText(m.public_ip); } catch (e) { /* noop */ }
    setCopied(c => ({ ...c, [m.user]: true }));
    setTimeout(() => setCopied(c => ({ ...c, [m.user]: false })), 1600);
  }

  async function doRefresh() {
    try {
      const r = await api(`/labs/${labName}/refresh`, { method: 'POST' });
      nav(`/job/${r.job_id}?lab=${labName}`);
    } catch (e) { setErr(e.message); }
  }

  async function doDestroy() {
    try {
      const r = await api(`/labs/${labName}`, {
        method: 'DELETE', body: JSON.stringify({ confirm: confirmText }),
      });
      setDestroyOpen(false);
      nav(`/job/${r.job_id}?lab=${labName}`);
    } catch (e) { setErr(e.message); }
  }

  if (!lab) return html`<div class="screen"><div class="card"><div class="empty">${err || 'กำลังโหลด…'}</div></div></div>`;

  const busyJob = lab.job && ['queued', 'running'].includes(lab.job.state);
  const expireTxt = lab.status === 'active' && lab.expires_at
    ? ` · ลบอัตโนมัติใน ${(fmtExpire(lab.expires_at) || '').replace('⏳ ', '')}` : '';

  return html`
    <div class="screen">
      <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:14px; flex-wrap:wrap; margin-bottom:20px">
        <div>
          <h1 class="page-title">${lab.title || lab.product_name || lab.lab_name}
            <span class="mono muted" style="font:500 13px var(--mono)"> — ${lab.lab_name}</span></h1>
          <div style="display:flex; align-items:center; gap:10px; margin-top:7px">
            <${Pill} status=${lab.status} />
            <span class="mono muted" style="font-size:12px">
              ${[OS_LABELS[lab.os] || lab.os, lab.instance_type, lab.region].filter(Boolean).join(' · ')}${expireTxt}</span>
          </div>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap">
          ${busyJob && html`<button class="btn ghost small"
            onClick=${() => nav(`/job/${lab.job.id}?lab=${labName}`)}>ดูงานที่กำลังรัน →</button>`}
          <button class="btn ghost small" onClick=${doRefresh} disabled=${busyJob}>↻ Refresh</button>
          <a class="btn ghost small" href="/api/labs/${labName}/handouts/export" download>⤓ Export ทั้งหมด</a>
          <button class="btn danger small" disabled=${busyJob}
            onClick=${() => { setConfirmText(''); setDestroyOpen(true); }}>Destroy…</button>
        </div>
      </div>
      <${ErrBox} err=${err} />

      ${lab.machines.length === 0
        ? html`<div class="card"><div class="empty">
            ${lab.status === 'destroyed' ? 'lab นี้ถูกลบแล้ว'
             : ['queued', 'provisioning'].includes(lab.status) ? 'กำลังสร้างเครื่อง — ดูความคืบหน้าจากปุ่มด้านบน'
             : 'ยังอ่านข้อมูลเครื่องไม่ได้ — ลอง Refresh'}</div></div>`
        : html`
        <div class="mac-grid">
          ${lab.machines.map(m => html`
            <div class="mac-card" key=${m.user}>
              <div class="mac-head">
                <div class="mac-user"><span class="mac-dot ${m.public_ip ? '' : 'off'}"></span>${m.user}</div>
                <span class="mac-proto">${m.is_windows ? 'RDP · 3389' : 'SSH · 22'}</span>
              </div>
              <div class="mac-rows">
                <div><span class="k">Public IP</span>
                  <span class="flex-center-gap">
                    <span class="v">${m.public_ip || '—'}</span>
                    ${m.public_ip && html`<button class="btn tiny"
                      style=${copied[m.user] ? 'color:var(--good); border-color:var(--good)' : ''}
                      onClick=${() => copyIp(m)}>${copied[m.user] ? '✓ คัดลอกแล้ว' : 'คัดลอก'}</button>`}
                  </span></div>
                <div><span class="k">Username</span><span class="v">${m.username}</span></div>
                <div><span class="k">Password</span>
                  ${!m.is_windows ? html`<span class="muted" style="font-size:11.5px">key ในไฟล์ handout</span>`
                  : m.password === null ? html`<span class="muted" style="font-size:11.5px">ยังไม่พร้อม — รอบูต ~4-5 นาทีแล้วกด Refresh</span>`
                  : revealed[m.user]
                    ? html`<span class="flex-center-gap"><span class="pw-shown">${m.password}</span>
                        <button class="btn tiny" onClick=${() => setRevealed({ ...revealed, [m.user]: false })}>ซ่อน</button></span>`
                    : html`<span class="flex-center-gap"><span class="masked">••••••••</span>
                        <button class="btn tiny" onClick=${() => setRevealed({ ...revealed, [m.user]: true })}>แสดง</button></span>`}
                </div>
              </div>
              <div class="mac-foot">
                ${m.handout_ready
                  ? html`<a href="/api/labs/${labName}/handouts/${m.user}.zip" download>⤓ handout .zip</a>`
                  : html`<span class="muted" style="font:600 11px var(--mono)">handout ยังไม่พร้อม</span>`}
              </div>
            </div>`)}
        </div>`}

      ${destroyOpen && html`
        <div class="modal-overlay" onClick=${(e) => { if (e.target === e.currentTarget) setDestroyOpen(false); }}>
          <div class="modal">
            <div style="font:700 16px var(--sans); margin-bottom:8px">ลบ lab ทั้งหมด?</div>
            <div style="font:400 13.5px var(--sans); color:var(--ink-dim); margin-bottom:16px">
              VM ทุกเครื่อง, network, key pair และ handout ของ${' '}
              <b class="mono" style="color:var(--ink)">${labName}</b> จะถูกลบถาวร</div>
            <div class="flabel">พิมพ์ชื่อ lab เพื่อยืนยัน</div>
            <input type="text" value=${confirmText} placeholder=${labName}
              onInput=${(e) => setConfirmText(e.target.value)} />
            <div style="display:flex; gap:10px; justify-content:flex-end; margin-top:18px">
              <button class="btn ghost" onClick=${() => setDestroyOpen(false)}>ยกเลิก</button>
              <button class="btn danger" disabled=${confirmText !== labName}
                style=${confirmText === labName ? 'background:var(--crit); color:#fff' : ''}
                onClick=${doDestroy}>ลบทั้งหมด</button>
            </div>
          </div>
        </div>`}
    </div>`;
}

// ---------------------------------------------------------------------------
// aws page
// ---------------------------------------------------------------------------

function AwsPage({ aws, onAws }) {
  const [form, setForm] = useState({
    access_key_id: '', secret_access_key: '', session_token: '',
    region: 'ap-southeast-1', remember: true,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const res = await api('/aws/login', {
        method: 'POST',
        body: JSON.stringify({
          access_key_id: form.access_key_id,
          secret_access_key: form.secret_access_key,
          session_token: form.session_token || null,
          region: form.region || null,
          remember: form.remember,
        }),
      });
      onAws(res);
    } catch (ex) { setErr(ex.message); }
    setBusy(false);
  }

  if (aws?.connected) {
    return html`
      <div class="screen" style="max-width:720px">
        <h1 class="page-title" style="margin-bottom:16px">เชื่อมต่อ AWS แล้ว</h1>
        <div class="card">
          <div class="kv-rows">
            <div class="kv-row"><span class="k">Account</span><span class="v">${aws.account}</span></div>
            <div class="kv-row"><span class="k">Identity</span><span class="v">${aws.arn || '-'}</span></div>
            <div class="kv-row"><span class="k">Region</span><span class="v">${aws.region || '-'}</span></div>
            <div class="kv-row"><span class="k">ที่มา</span><span class="v sans">
              ${aws.method === 'env' ? 'ใส่ผ่านหน้านี้ (จำเฉพาะ session นี้)'
                : aws.method?.startsWith('profile:') ? `โปรไฟล์ ${aws.method.slice(8)} ใน ~/.aws/credentials — ครั้งหน้าไม่ต้องใส่ใหม่`
                : aws.method || '-'}</span></div>
          </div>
          <div class="btnbar">
            <button class="btn ghost" onClick=${() => nav('/')}>← หน้าแรก</button>
            <button class="btn primary" onClick=${() => { PREFILL = null; nav('/new'); }}>สร้าง hands-on lab ได้เลย →</button>
          </div>
        </div>
      </div>`;
  }

  return html`
    <div class="screen" style="max-width:720px">
      <h1 class="page-title" style="margin-bottom:16px">เชื่อมต่อ AWS</h1>
      <div class="card">
        <p class="page-sub" style="margin-top:0">
          ใส่ Access Key ของบัญชี AWS ที่มีสิทธิ์ EC2 + VPC — ระบบตรวจกับ AWS (STS) ก่อนใช้จริง
          key ไม่ถูกส่งไปที่อื่นนอกจาก AWS และไม่ลง log</p>
        ${aws?.reason && !err && html`<div class="err">${aws.reason}</div>`}
        <${ErrBox} err=${err} />
        <form onSubmit=${submit}>
          <div class="field"><label>Access Key ID <span class="req">*</span></label>
            <input type="text" value=${form.access_key_id} onInput=${set('access_key_id')}
              placeholder="AKIA…" autocomplete="off" /></div>
          <div class="field"><label>Secret Access Key <span class="req">*</span></label>
            <input type="password" value=${form.secret_access_key} onInput=${set('secret_access_key')} autocomplete="off" /></div>
          <div class="row">
            <div class="field"><label>Session Token <span class="opt">optional — เฉพาะ credential ชั่วคราว (SSO/STS)</span></label>
              <input type="password" value=${form.session_token} onInput=${set('session_token')} autocomplete="off" /></div>
            <div class="field"><label>Default region</label>
              <input type="text" value=${form.region} onInput=${set('region')} /></div>
          </div>
          <div class="field"><label class="checkbox">
            <input type="checkbox" checked=${form.remember}
              onChange=${(e) => setForm({ ...form, remember: e.target.checked })} />
            จำไว้ในเครื่องนี้ (~/.aws/credentials โปรไฟล์ labctl) — ปิดโปรแกรมแล้วไม่ต้องใส่ใหม่
          </label></div>
          <div class="btnbar">
            <button type="button" class="btn ghost" onClick=${() => nav('/')}>ยกเลิก</button>
            <button type="submit" class="btn primary"
              disabled=${busy || !form.access_key_id || !form.secret_access_key}>
              ${busy ? 'กำลังตรวจกับ AWS…' : 'เชื่อมต่อ'}</button>
          </div>
        </form>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// app shell + auth root
// ---------------------------------------------------------------------------

function Shell({ onLogout }) {
  const route = useRoute();
  const [labs, setLabs] = useState([]);
  const [labsLoaded, setLabsLoaded] = useState(false);
  const [aws, setAws] = useState(null);
  const [theme, setTheme] = useState(document.body.dataset.theme || 'light');

  useEffect(() => {
    const load = () => api('/labs').then(d => { setLabs(d.labs); setLabsLoaded(true); }).catch(() => {});
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    api('/aws/status')
      .then(setAws)
      .catch(() => setAws({ connected: false, reason: 'เช็คสถานะ AWS ไม่ได้' }));
  }, []);

  function toggleTheme() {
    const t = theme === 'light' ? 'dark' : 'light';
    document.body.dataset.theme = t;
    localStorage.setItem('labctl-theme', t);
    setTheme(t);
  }

  async function logout() {
    try { await api('/auth/logout', { method: 'POST' }); } catch (e) { /* noop */ }
    onLogout();
  }

  let screen;
  const jobMatch = route.match(/^\/job\/([^?]+)\?lab=(.+)$/);
  const labMatch = route.match(/^\/lab\/(.+)$/);
  if (route === '/new') screen = html`<${Wizard} aws=${aws} />`;
  else if (route === '/aws') screen = html`<${AwsPage} aws=${aws} onAws=${setAws} />`;
  else if (jobMatch) screen = html`<${JobView} jobId=${jobMatch[1]} labName=${decodeURIComponent(jobMatch[2])} />`;
  else if (labMatch) screen = html`<${Dashboard} labName=${decodeURIComponent(labMatch[1])} />`;
  else screen = html`<${Home} labs=${labs} loaded=${labsLoaded} />`;

  return html`
    <div class="shell">
      <${Sidebar} route=${route} labs=${labs} aws=${aws} theme=${theme}
        onToggleTheme=${toggleTheme} onLogout=${logout} />
      <div class="main">${screen}</div>
    </div>`;
}

function App() {
  const [auth, setAuth] = useState(null); // null = กำลังเช็ค
  const [justSetupU, setJustSetupU] = useState(null);

  const check = useCallback(() => {
    api('/auth/status').then(setAuth)
      .catch(() => setAuth({ setup_required: false, authenticated: false }));
  }, []);
  useEffect(check, [check]);
  useEffect(() => {
    const onUnauth = () => setAuth(a => (a && a.authenticated ? { ...a, authenticated: false } : a));
    addEventListener('labctl:unauth', onUnauth);
    return () => removeEventListener('labctl:unauth', onUnauth);
  }, []);

  if (auth === null) return null;
  if (!auth.authenticated) {
    if (auth.setup_required && justSetupU === null) {
      return html`<${SetupScreen} onDone=${(u) => { setJustSetupU(u); setAuth({ ...auth, setup_required: false }); }} />`;
    }
    return html`<${LoginScreen} prefillU=${justSetupU || ''}
      onAuthed=${() => { setJustSetupU(null); setAuth({ setup_required: false, authenticated: true }); }} />`;
  }
  return html`<${Shell} onLogout=${() => setAuth({ setup_required: false, authenticated: false })} />`;
}

render(html`<${App} />`, document.getElementById('root'));
