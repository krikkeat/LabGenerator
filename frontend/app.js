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
    let needsLogin = false;
    try {
      const d = (await res.json()).detail;
      if (typeof d === 'string') msg = d;
      // FastAPI ตอบ 422 เป็น list ของ object — แปลงเป็นข้อความที่อ่านรู้เรื่อง
      // ไม่งั้นจะขึ้น "[object Object]" แล้วไม่มีใครรู้ว่าพังเพราะอะไร
      else if (Array.isArray(d)) {
        msg = `ข้อมูลที่ส่งไม่ถูกต้อง (${res.status}): ` + d.map(x =>
          `${(x.loc || []).filter(k => k !== 'body').join('.') || 'request'} — ${x.msg}`).join(' · ');
      } else if (d && typeof d === 'object') {
        msg = d.message || JSON.stringify(d);
        needsLogin = !!d.needs_login;
      }
    } catch (e) { /* noop */ }
    const err = new Error(msg);
    err.status = res.status;
    err.needsLogin = needsLogin;
    throw err;
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
  'kali-linux': 'Kali Linux',
};

// ไอคอนเส้นเดียวกันทั้งชุด (stroke 1.75) — ไม่ใช้ emoji/อักขระแทนไอคอน
const ICON_PATHS = {
  labs: 'M4 5h16v4H4zM4 11h16v4H4zM4 17h10v2H4z',
  plus: 'M12 5v14M5 12h14',
  cloud: 'M7 18h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.2 9.1 4.5 4.5 0 0 0 7 18z',
  sun: 'M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z',
  logout: 'M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l4-4-4-4M14 12H4',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6',
  copy: 'M9 9h10v10H9zM5 15V5h10',
  check: 'M5 12.5l4.5 4.5L19 7',
  arrow: 'M5 12h14M13 6l6 6-6 6',
};
function Icon({ name }) {
  return html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${ICON_PATHS[name]} /></svg>`;
}

const osFamily = (os, isWindows) =>
  (isWindows ?? (os || '').startsWith('windows')) ? 'win'
    : (!os || os.startsWith('custom')) ? 'custom' : 'linux';

// แถบเวลา — ความยาวคือสัดส่วนเวลาที่เหลือของ TTL ทั้งหมด
function TimeBar({ expiresAt, ttlHours }) {
  if (!expiresAt) return html`<div class="timebar"><span class="lbl">ไม่ลบอัตโนมัติ</span></div>`;
  const left = expiresAt * 1000 - Date.now();
  const total = Math.max((ttlHours || 0) * 3600000, left, 1);
  const pct = Math.max(0, Math.min(100, (left / total) * 100));
  const cls = left <= 0 ? 'late' : left < 30 * 60000 ? 'soon' : '';
  return html`
    <div class="timebar ${cls}" role="img" aria-label=${`เหลือ ${fmtLeft(left)}`}>
      <div class="track"><div class="fill" style=${`width:${pct}%`}></div></div>
      <span class="lbl">${left <= 0 ? 'กำลังลบ' : fmtLeft(left)}</span>
    </div>`;
}
function fmtLeft(ms) {
  if (ms <= 0) return '0 นาที';
  const hh = Math.floor(ms / 3600000), mm = Math.round((ms % 3600000) / 60000);
  return hh > 0 ? `${hh} ชม. ${mm} นาที` : `${mm} นาที`;
}

const STATUS_LABEL = {
  active: 'พร้อมใช้', provisioning: 'กำลังสร้าง', queued: 'รอคิว',
  destroying: 'กำลังลบ', destroyed: 'ลบแล้ว', error: 'ผิดพลาด',
};

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

// ข้อความ error จาก backend มีลิงก์ได้ (เช่นหน้า Subscribe ของ Marketplace) — ทำให้กดได้
function Linkify({ text }) {
  return String(text).split(/(https:\/\/[^\s)]+)/g).map((part, i) => (i % 2
    ? html`<a href=${part} target="_blank" rel="noopener" style="color:inherit; text-decoration:underline">${part}</a>`
    : part));
}

function ErrBox({ err }) {
  return err ? html`<div class="err"><${Linkify} text=${err} /></div>` : null;
}

// ---------------------------------------------------------------------------
// login / first-time setup
// ---------------------------------------------------------------------------

function DemoBadge({ name, os, ip, proto, fam }) {
  return html`
    <div class="badge ${fam}" aria-hidden="true" style="min-height:0">
      <div class="band"><span>${os}</span><span class="proto">${proto}</span></div>
      <div class="who" style="padding-bottom:12px"><div class="name" style="font-size:19px">${name}</div>
        <div class="status-line" style="font-size:12px">● พร้อมใช้ · <span class="mono">${ip}</span></div></div>
    </div>`;
}

function BrandPanel() {
  return html`
    <aside class="login-brand">
      <div class="logo-row">
        <div class="logo">lc</div>
        <div><div class="brand-name">labctl</div><div class="brand-sub">PoC & hands-on lab บน AWS</div></div>
      </div>
      <div class="headline">เครื่องหนึ่งเครื่องต่อหนึ่งคน พร้อมไฟล์เข้าเครื่อง ภายในไม่กี่นาที</div>
      <div class="lede">สร้าง VM บน AWS แยก VPC ต่อ lab ส่ง handout ให้แต่ละคน แล้วลบทิ้งหมดเมื่อจบ — ไม่มีอะไรค้างคิดเงิน</div>
      <div class="demo-badges">
        <${DemoBadge} name="user01" os="Windows Server 2025" proto="RDP · 3389" ip="54.169.169.237" fam="win" />
        <${DemoBadge} name="user02" os="Ubuntu 24.04 LTS" proto="SSH · 22" ip="13.212.40.18" fam="linux" />
      </div>
      <div class="foot">ตัวอย่างบัตร · รันบนเครื่องนี้เท่านั้น (127.0.0.1)</div>
    </aside>`;
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
      <${BrandPanel} />
      <main class="login-form">
          <h1>ตั้งค่าครั้งแรก</h1>
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
            ${f.busy ? 'กำลังสร้างบัญชี…' : 'สร้างบัญชีและเข้าสู่ระบบ'}</button>
      </main>
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
      <${BrandPanel} />
      <main class="login-form">
          <h1>เข้าสู่ระบบ</h1>
          <div class="page-sub" style="margin:4px 0 26px">บัญชีของ labctl ในเครื่องนี้ — เข้าแล้วสร้างเครื่องได้เลย</div>
          <div class="field"><label>Username</label>
            <input type="text" value=${f.u} onInput=${(e) => setF({ ...f, u: e.target.value, err: false })}
              placeholder="username" autocomplete="username" /></div>
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
            ${f.busy ? 'กำลังตรวจสอบ…' : 'เข้าสู่ระบบ'}</button>
          <div class="help" style="margin-top:18px; text-align:center">
            credential ถูกตรวจกับเครื่องนี้เท่านั้น — ไม่ถูกส่งออกนอกเครื่องและไม่ลง log</div>
      </main>
    </div>`;
}

// ---------------------------------------------------------------------------
// shell (sidebar + main)
// ---------------------------------------------------------------------------

function AccountChip({ aws }) {
  const state = aws === null ? 'wait' : aws.connected ? 'ok' : aws.needs_login ? 'login' : 'bad';
  const label = { wait: 'กำลังเช็ค…', ok: 'ใช้งานได้', login: 'ต้อง sign in', bad: 'ยังไม่เชื่อมต่อ' }[state];
  return html`
    <button class="acct-chip ${state}" onClick=${() => nav('/aws')}
      title="AWS account ที่ใช้สร้าง lab ใหม่ — กดเพื่อสลับหรือเพิ่ม">
      <div class="top"><${Icon} name="cloud" />
        <span class="name">${aws?.profile || (aws?.connected ? 'AWS' : 'AWS account')}</span>
        <span class="state">${label}</span></div>
      <div class="sub">${aws?.connected ? `${aws.account} · ${aws.region}` : 'กดเพื่อเลือก account'}</div>
    </button>`;
}

function Sidebar({ route, labs, aws, theme, onToggleTheme, onLogout }) {
  const activeCount = labs.filter(l => l.status !== 'destroyed').length;
  const items = [
    { icon: 'labs', label: 'Labs', count: activeCount, r: '/', match: (rt) => rt === '/' || rt.startsWith('/lab/') || rt.startsWith('/job/') },
    { icon: 'plus', label: 'สร้าง lab', count: 0, r: '/new', match: (rt) => rt === '/new' },
    { icon: 'cloud', label: 'AWS accounts', count: 0, r: '/aws', match: (rt) => rt === '/aws' },
  ];
  return html`
    <nav class="sidebar" aria-label="เมนูหลัก">
      <button class="logo-row" onClick=${() => nav('/')}>
        <div class="logo">lc</div>
        <div class="brand-text"><div class="brand-name">labctl</div><div class="brand-sub">PoC & hands-on lab</div></div>
      </button>
      <div class="nav">
        ${items.map(n => html`
          <button key=${n.r} class="nav-item ${n.match(route) ? 'active' : ''}"
            aria-current=${n.match(route) ? 'page' : null} onClick=${() => nav(n.r)}>
            <${Icon} name=${n.icon} /><span class="label">${n.label}</span>
            ${n.count > 0 && html`<span class="count">${n.count}</span>`}
          </button>`)}
      </div>
      <${AccountChip} aws=${aws} />
      <div class="side-foot">
        <button class="icon-btn" onClick=${onToggleTheme} aria-label="สลับธีม">
          <${Icon} name=${theme === 'dark' ? 'sun' : 'moon'} /><span>${theme === 'dark' ? 'Light' : 'Dark'}</span></button>
        <button class="icon-btn" onClick=${onLogout}><${Icon} name="logout" /><span>ออกจากระบบ</span></button>
      </div>
    </nav>`;
}

// ---------------------------------------------------------------------------
// home — labs
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
    } catch (e) { PREFILL = null; }       // ไม่มี config — เริ่มฟอร์มเปล่า
    nav('/new');
  }

  function open(l) {
    if (['provisioning', 'queued', 'destroying'].includes(l.status) && l.job) {
      nav(`/job/${l.job.id}?lab=${l.lab_name}`);
    } else if (l.status === 'error' && l.job && l.job.kind !== 'destroy') {
      // destroy ที่ล้มต้องพาไป dashboard — ไม่งั้นกด Destroy ซ้ำไม่ได้
      nav(`/job/${l.job.id}?lab=${l.lab_name}`);
    } else {
      nav(`/lab/${l.lab_name}`);
    }
  }

  const goNew = () => { PREFILL = null; nav('/new'); };

  return html`
    <div class="screen">
      <div class="page-head">
        <div>
          <h1 class="page-title">Labs</h1>
          <div class="page-sub">อัปเดตเองทุก 8 วินาที</div>
        </div>
        <button class="btn primary" onClick=${goNew}><${Icon} name="plus" />สร้าง lab</button>
      </div>
      ${shown.length > 0 && html`
        <div class="summary-strip">
          <span><b>${active.length}</b>lab พร้อมใช้</span>
          <span><b>${vmCount}</b>เครื่องกำลังรัน</span>
          ${liveCost > 0 && html`<span><b>$${liveCost.toFixed(2)}</b>ต่อชั่วโมงตอนนี้</span>`}
        </div>`}
      <div class="lab-list">
        ${!loaded && html`<div class="empty">กำลังโหลด…</div>`}
        ${loaded && shown.length === 0 && html`
          <div class="empty-desk">
            <h2>ยังไม่มี lab ที่รันอยู่</h2>
            <p>เลือก AWS account, OS และจำนวนเครื่อง แล้วกดสร้าง — ได้ VM พร้อมไฟล์เข้าเครื่องของแต่ละคน</p>
            <button class="btn primary" onClick=${goNew}><${Icon} name="plus" />สร้าง lab แรก</button>
          </div>`}
        ${shown.map(l => {
          const busy = ['provisioning', 'queued', 'destroying'].includes(l.status);
          const subBits = [OS_LABELS[l.os] || l.os || 'custom AMI', `${l.users} เครื่อง`,
            l.instance_type, l.aws_profile].filter(Boolean);
          const busyText = l.job?.kind === 'destroy' ? 'กำลังลบ resource บน AWS…'
            : l.job?.kind === 'remove_user' ? 'กำลังลบเครื่องบน AWS…' : 'กำลังสร้างเครื่องบน AWS…';
          return html`
          <button key=${l.lab_name} class="lab-card ${l.status === 'error' ? 'error' : ''}" onClick=${() => open(l)}>
            <span class="band ${osFamily(l.os)}"></span>
            <div class="lab-main">
              <div class="lab-title-row">
                <span class="lab-name">${l.title || l.lab_name}</span>
                <${Pill} status=${l.status} />
              </div>
              ${l.status === 'error'
                ? html`<div class="lab-sub bad">${l.job?.error || 'งานล้มเหลว'} — เปิดเพื่อดู log</div>`
                : busy
                  ? html`<div class="lab-sub">${busyText} เปิดเพื่อดู log สด</div>`
                  : html`<div class="lab-sub"><span class="mono">${l.lab_name}</span> · ${subBits.join(' · ')}</div>`}
            </div>
            <div class="lab-right">
              ${l.status === 'active' && html`
                <${TimeBar} expiresAt=${l.expires_at} ttlHours=${l.ttl_hours} />
                ${labCost(l) > 0 && html`<div class="lab-cost">$${labCost(l).toFixed(3)} ต่อชั่วโมง</div>`}`}
            </div>
            <span class="lab-cta">${busy ? 'ดู log' : 'เปิด'} →</span>
          </button>`;
        })}
        ${destroyed.length > 0 && html`<div class="section-title" style="margin:18px 0 4px; color:var(--ink-dim); font-size:13.5px">ลบแล้ว — สร้างซ้ำจาก config เดิมได้</div>`}
        ${destroyed.slice(0, 6).map(l => html`
          <button key=${'d-' + l.lab_name} class="recreate-card" onClick=${() => recreate(l)}>
            <span class="mono" style="color:var(--ink-dim)">${l.lab_name}</span>
            <span>${OS_LABELS[l.os] || l.os || ''}</span>
            <span class="go">สร้างซ้ำ →</span>
          </button>`)}
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// wizard
// ---------------------------------------------------------------------------

// เลือก hands-on lab guide ตั้งแต่ก่อน gen — อ่านไฟล์ในเบราว์เซอร์แล้วส่งเป็น
// ข้อความไปกับ body ตอนสร้าง (ไม่ต้องมี endpoint upload แยกและไม่ต้องพึ่ง multipart)
// value = { mode, path, markdown, filename }
function GuidePicker({ value, onChange, productGuide }) {
  const [guides, setGuides] = useState([]);
  const [fileErr, setFileErr] = useState(null);

  useEffect(() => { api('/lab-guides').then(r => setGuides(r.guides)).catch(() => {}); }, []);

  function readFile(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    setFileErr(null);
    const isPdf = /\.pdf$/i.test(f.name) || f.type === 'application/pdf';
    const limit = isPdf ? 20 * 1024 * 1024 : 512 * 1024;
    if (f.size > limit) {
      setFileErr(`ไฟล์ใหญ่เกิน ${isPdf ? '20 MB' : '512 KB'}`);
      return;
    }
    const rd = new FileReader();
    rd.onerror = () => setFileErr('อ่านไฟล์ไม่สำเร็จ');
    if (isPdf) {
      // PDF เป็นไบนารี — ส่งเป็น data URL แล้วให้ backend ถอด base64
      rd.onload = () => onChange({ ...value, pdf: String(rd.result), markdown: '',
                                   filename: f.name, size: f.size });
      rd.readAsDataURL(f);
    } else {
      rd.onload = () => onChange({ ...value, markdown: String(rd.result), pdf: '',
                                   filename: f.name, size: f.size });
      rd.readAsText(f);
    }
  }

  const selected = guides.find(g => g.path === value.path);

  async function removeGuide() {
    setFileErr(null);
    try {
      await api('/lab-guides/delete', { method: 'POST', body: JSON.stringify({ path: value.path }) });
      setGuides(gs => gs.filter(g => g.path !== value.path));
      onChange({ ...value, path: '' });
    } catch (e) { setFileErr(e.message); }
  }

  const tab = (m, label) => html`
    <button type="button" role="tab" aria-selected=${value.mode === m} class=${value.mode === m ? 'on' : ''}
      onClick=${() => onChange({ ...value, mode: m })}>${label}</button>`;

  return html`
    <div class="field">
      <label>Lab guide <span class="opt">— รวมเข้า handout ของทุกคน พร้อมวิธีเข้าเครื่อง</span></label>
      <div class="seg" role="tablist" aria-label="lab guide">
        ${tab('none', 'ไม่ใส่')}
        ${tab('upload', 'อัปโหลดไฟล์')}
        ${tab('pick', 'เลือกจากที่มี')}
      </div>

      ${value.mode === 'upload' && html`
        <input type="file" accept=".md,.markdown,.txt,.pdf,text/markdown,text/plain,application/pdf"
          onChange=${readFile} style="font:400 12.5px var(--sans); padding:7px" />
        ${value.filename && html`<div class="help">
          แนบแล้ว: <b>${value.filename}</b> · ${value.pdf
            ? `PDF ${((value.size || 0) / 1024 / 1024).toFixed(1)} MB — จะแนบไปใน zip ของผู้เรียนทุกคน`
            : `${(value.markdown || '').length.toLocaleString()} ตัวอักษร`}</div>`}
        ${fileErr && html`<div class="help bad">${fileErr}</div>`}
        ${!value.filename && !fileErr && html`<div class="help">
          Markdown (.md) ฝังเนื้อหาลงในหน้า handout · PDF (.pdf) แนบเป็นไฟล์ไปกับ zip</div>`}`}

      ${value.mode === 'pick' && html`
        <div style="display:flex; gap:8px; align-items:center">
          <select value=${value.path} onChange=${(e) => onChange({ ...value, path: e.target.value })}>
            <option value="">— เลือก lab guide —</option>
            ${guides.map(g => html`<option value=${g.path} key=${g.path}>${g.name} · ${g.source}</option>`)}
          </select>
          ${selected?.deletable && html`
            <button type="button" class="btn ghost small" style="flex:0 0 auto"
              onClick=${removeGuide}>ลบไฟล์นี้</button>`}
        </div>
        ${selected?.deletable && html`<div class="help">
          เป็นไฟล์ที่อัปโหลดไว้เอง — ลบได้ (guide ของ product ลบไม่ได้)</div>`}
        ${fileErr && html`<div class="help bad">${fileErr}</div>`}`}

      ${value.mode === 'none' && html`<div class="help">
        ${productGuide ? `จะใช้ guide มาตรฐานของ ${productGuide}`
          : 'handout จะมีเฉพาะวิธีเข้าเครื่อง — แนบ guide ทีหลังจากหน้า lab ได้'}</div>`}
    </div>`;
}

// ---------------------------------------------------------------------------
// AWS account — เลือก/sign in ใช้ร่วมกันระหว่างหน้าสร้าง lab กับหน้า AWS accounts
// ---------------------------------------------------------------------------

// sign in SSO: backend รัน `aws sso login` (เปิดเบราว์เซอร์ให้เอง) แล้วเราคอยถามสถานะ
function useSsoLogin(onDone, pathFor = (p) => `/aws/accounts/${encodeURIComponent(p)}/sso-login`) {
  const [state, setState] = useState(null);   // {profile, state, url, code, error}
  const timer = useRef(null);
  useEffect(() => () => clearInterval(timer.current), []);

  async function start(profile) {
    clearInterval(timer.current);
    try {
      const st = await api(pathFor(profile), { method: 'POST' });
      setState({ ...st, profile });
      timer.current = setInterval(async () => {
        try {
          const s2 = await api(pathFor(profile));
          setState({ ...s2, profile });
          if (s2.state !== 'waiting') {
            clearInterval(timer.current);
            if (s2.state === 'done') onDone(profile);
          }
        } catch (e) { clearInterval(timer.current); setState({ profile, state: 'error', error: e.message }); }
      }, 2000);
    } catch (e) { setState({ profile, state: 'error', error: e.message }); }
  }
  return [state, start];
}

function SsoWait({ st }) {
  if (!st) return null;
  if (st.state === 'error') return html`<div class="err">sign in ${st.profile} ไม่สำเร็จ: ${st.error}</div>`;
  if (st.state === 'done') return html`<div class="ok-box">sign in ${st.profile} แล้ว</div>`;
  return html`
    <div class="sso-wait" role="status">
      <b>รอ sign in ${st.profile} ในเบราว์เซอร์…</b>
      <div class="help" style="margin-top:4px">หน้า AWS sign-in เปิดในเบราว์เซอร์ของคุณแล้ว — กด Allow แล้วกลับมาที่นี่ หน้านี้จะไปต่อเอง</div>
      ${st.code && html`<div style="margin-top:8px">รหัสยืนยัน <span class="code">${st.code}</span></div>`}
      ${st.url && html`<div class="help">ถ้าเบราว์เซอร์ไม่เปิดเอง: <a href=${st.url} target="_blank" rel="noopener">เปิดหน้า sign in</a></div>`}
    </div>`;
}

function AccountPicker({ aws, onAws }) {
  const [list, setList] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const refresh = () => api('/aws/accounts').then(setList).catch(e => setErr(e.message));
  useEffect(() => { refresh(); }, []);
  const reload = async () => onAws(await api('/aws/status?force=true'));
  const [sso, startSso] = useSsoLogin(() => reload());

  async function pick(profile) {
    setBusy(true); setErr(null);
    try { onAws(await api('/aws/accounts/select', { method: 'POST', body: JSON.stringify({ profile }) })); }
    catch (e) { setErr(e.message); }
    setBusy(false);
    refresh();
  }

  if (!list) return html`<div class="muted">${err || 'กำลังอ่าน ~/.aws…'}</div>`;
  if (list.accounts.length === 0) {
    return html`<div class="warn-box" style="margin:0">ยังไม่มี AWS account ในเครื่องนี้ —
      <a href="#/aws">เพิ่ม account</a> (SSO หรือ access key) ก่อนสร้าง lab</div>`;
  }
  const current = aws?.profile || list.active || '';
  const prof = list.accounts.find(a => a.name === current);
  return html`
    <div class="row" style="align-items:flex-end">
      <div class="field" style="margin:0; flex:1">
        <label for="acct">AWS account</label>
        <select id="acct" value=${current} disabled=${busy} onChange=${(e) => pick(e.target.value)}>
          ${!current && html`<option value="">— เลือก account —</option>`}
          ${list.accounts.map(a => html`<option value=${a.name} key=${a.name}>
            ${a.name}${a.account_id ? ` · ${a.account_id}` : ''}${a.kind === 'sso' ? ' · SSO' : ''}</option>`)}
        </select>
      </div>
      <div style="flex:0 0 auto; padding-bottom:9px; font-size:13.5px">
        ${aws === null ? html`<span class="muted">กำลังเช็ค…</span>`
          : aws.connected ? html`<span style="color:var(--good); font-weight:600">● ใช้งานได้ · <span class="mono">${aws.account}</span></span>`
          : aws.needs_login && prof?.kind === 'sso' ? html`
              <button class="btn primary small" onClick=${() => startSso(current)}
                disabled=${sso?.state === 'waiting'}>Sign in ด้วย SSO</button>`
          : current ? html`<span style="color:var(--crit); font-weight:600">● ใช้ไม่ได้</span>` : null}
      </div>
    </div>
    ${aws && !aws.connected && aws.reason && !sso && current && html`<div class="help bad">${aws.reason}</div>`}
    <${SsoWait} st=${sso} />
    <${ErrBox} err=${err} />`;
}

// ---------------------------------------------------------------------------
// สร้าง lab — หน้าเดียว: account → เครื่อง → ชื่อ/เวลา, ที่เหลือพับไว้ใน "ขั้นสูง"
// ---------------------------------------------------------------------------

const CUSTOM = '__custom__';
const OS_ORDER = ['windows-2025', 'windows-2022', 'ubuntu-24.04', 'ubuntu-22.04',
  'amazon-linux-2023', 'debian-12', 'kali-linux', 'windows-2019', 'windows-2016',
  'amazon-linux-2', 'ubuntu-20.04', 'debian-11', 'suse-15'];
const MAX_USERS = 50;
const userNames = (n) => Array.from({ length: n }, (_, i) => `user${String(i + 1).padStart(2, '0')}`);

function defaultLabName() {
  const d = new Date();
  const p = (x) => String(x).padStart(2, '0');
  return `poc-${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

function Wizard({ aws, onAws }) {
  const [catalog, setCatalog] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const pre = PREFILL; // อ่านครั้งเดียวตอน mount
  useEffect(() => { PREFILL = null; }, []);
  useEffect(() => { api('/products').then(setCatalog).catch(e => setErr(e.message)); }, []);

  const [source, setSource] = useState(pre?.product ? 'product' : pre?.custom_ami ? 'ami' : 'os');
  const [product, setProduct] = useState(pre?.product || null);
  const [form, setForm] = useState({
    lab_name: pre?.lab_name || defaultLabName(), title: pre?.title || '',
    region: pre?.region || 'ap-southeast-1',
    users: pre?.users || ['user01'], customNames: !!pre?.users,
    allowed_cidr: pre?.allowed_cidr || '',
    os: pre?.os || 'windows-2025',
    instance_type: pre?.instance_type || '', root_volume_gb: '', extra_open_ports: '',
    ttl_hours: pre?.ttl_hours != null ? String(pre.ttl_hours) : '8',
    custom_ami: pre?.custom_ami || '', custom_username: pre?.custom_username || 'Administrator',
    custom_is_windows: pre?.custom_is_windows ?? true, use_spot: !!pre?.use_spot,
    instructorKali: !!(pre?.user_os && pre.user_os.instructor === 'kali-linux'),
  });
  const [params, setParams] = useState(pre?.product_params || {});
  const [guide, setGuide] = useState({ mode: 'none', path: '', markdown: '', pdf: '', filename: '', size: 0 });

  if (!catalog) return html`<div class="screen"><div class="card"><div class="empty">${err || 'กำลังโหลด…'}</div></div></div>`;

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const meta = source === 'product' ? catalog.products.find(p => p.id === product) : null;
  const paramSpecs = meta ? Object.entries(meta.params) : [];
  const requiredOk = paramSpecs.every(([k, spec]) => !spec.required || (params[k] || '').trim());
  const effOS = source === 'product' ? meta?.os : source === 'os' ? form.os : null;
  // Windows บน t3.small อืดจนใช้ demo ไม่ได้ — default เครื่องใหญ่กว่า Linux หนึ่งขั้น
  const isWin = (effOS || '').startsWith('windows') || (source === 'ami' && form.custom_is_windows);
  const effType = form.instance_type || meta?.instance_type || (isWin ? 't3.medium' : 't3.small');
  const hourly = HOURLY[effType] || 0;
  // เครื่อง instructor เป็น Kali เสมอ — แยกจากรายชื่อผู้เรียน จะได้ไม่หายตอนกดเปลี่ยนจำนวน
  const learners = form.users.filter(u => u !== 'instructor');
  const n = learners.length + (form.instructorKali ? 1 : 0);
  const ttlNum = parseFloat(form.ttl_hours) || 0;
  const amiOk = source !== 'ami' || (/^ami-[0-9a-f]{8,17}$/.test(form.custom_ami.trim()) && form.custom_username.trim());
  const nameOk = /^[a-z0-9-]{1,32}$/.test(form.lab_name);
  const problems = [
    !aws?.connected && 'ยังไม่ได้เลือก AWS account ที่ใช้งานได้',
    !nameOk && 'ชื่อ lab ใช้ได้แค่ a-z, 0-9, - (ไม่เกิน 32 ตัว)',
    n === 0 && 'ต้องมีอย่างน้อย 1 เครื่อง',
    form.instructorKali && source === 'ami' && 'เครื่อง Instructor (Kali) ใช้คู่กับ AMI ของตัวเองไม่ได้ — เลือก OS หรือ product แทน',
    source === 'product' && !product && 'เลือก product preset',
    source === 'product' && product && !requiredOk && 'กรอกข้อมูล product ที่จำเป็นในส่วนขั้นสูง',
    !amiOk && 'ใส่ AMI ID (ami-xxxxxxxx) และ username',
  ].filter(Boolean);

  const setCount = (c) => setForm({ ...form, users: userNames(Math.min(MAX_USERS, Math.max(1, c || 1))), customNames: false });

  async function submit() {
    if (problems.length) return;
    setBusy(true); setErr(null);
    const body = {
      lab_name: form.lab_name.trim(), title: form.title.trim() || null,
      users: form.instructorKali ? [...learners, 'instructor'] : learners,
      user_os: form.instructorKali && !(source === 'os' && form.os === 'kali-linux')
        ? { instructor: 'kali-linux' } : null,
      region: form.region || null, allowed_cidr: form.allowed_cidr || null,
      ttl_hours: form.ttl_hours === '' ? 8 : parseFloat(form.ttl_hours),
      use_spot: form.use_spot || null,
    };
    if (source === 'ami') {
      body.custom_ami = form.custom_ami.trim();
      body.custom_username = form.custom_username.trim();
      body.custom_is_windows = form.custom_is_windows;
    } else if (source === 'os') {
      body.os = form.os;
    } else {
      body.product = product;
      body.product_params = Object.fromEntries(
        paramSpecs.map(([k]) => [k, (params[k] || '').trim()]).filter(([, v]) => v));
    }
    if (guide.mode === 'upload' && guide.pdf) body.lab_guide_pdf = guide.pdf;
    else if (guide.mode === 'upload' && guide.markdown.trim()) body.lab_guide_markdown = guide.markdown;
    else if (guide.mode === 'pick' && guide.path) body.lab_guide = guide.path;
    if (form.instance_type) body.instance_type = form.instance_type;
    else if (source === 'os') body.instance_type = effType;
    if (form.root_volume_gb) body.root_volume_gb = parseInt(form.root_volume_gb, 10);
    if (form.extra_open_ports.trim()) {
      body.extra_open_ports = form.extra_open_ports.split(',')
        .map(x => parseInt(x.trim(), 10)).filter(x => !isNaN(x));
    }
    try {
      const r = await api('/labs', { method: 'POST', body: JSON.stringify(body) });
      nav(`/job/${r.job_id}?lab=${r.lab_name}`);
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  const osList = OS_ORDER.filter(o => catalog.supported_os.includes(o));
  const machineLabel = source === 'ami' ? `AMI ${form.custom_ami || '—'}`
    : source === 'product' ? (meta?.product_name || 'ยังไม่เลือก') : (OS_LABELS[form.os] || form.os);

  return html`
    <div class="screen">
      <div class="page-head">
        <div>
          <h1 class="page-title">สร้าง lab</h1>
          <div class="page-sub">แต่ละคนได้ VM ของตัวเอง แยก VPC ต่อ lab — ลบทิ้งได้ทั้งหมดในครั้งเดียว</div>
        </div>
      </div>
      <div class="create-wrap">
        <div class="card create-card">
          <section class="form-block">
            <h2 class="block-title"><span class="step-n">1</span>สร้างใน AWS account ไหน</h2>
            <${AccountPicker} aws=${aws} onAws=${onAws} />
          </section>

          <section class="form-block">
            <h2 class="block-title"><span class="step-n">2</span>เครื่อง</h2>
            <div class="seg" role="tablist" aria-label="ที่มาของเครื่อง">
              ${[['os', 'เลือก OS'], ['product', 'Product preset'], ['ami', 'AMI ของตัวเอง']].map(([k, l]) => html`
                <button role="tab" aria-selected=${source === k} class=${source === k ? 'on' : ''}
                  onClick=${() => setSource(k)}>${l}</button>`)}
            </div>
            ${source === 'os' && html`
              <div class="os-grid">
                ${osList.map(o => html`
                  <button key=${o} class="os-opt ${osFamily(o)}" aria-pressed=${form.os === o}
                    onClick=${() => setForm({ ...form, os: o })}>
                    <span class="n">${OS_LABELS[o] || o}</span>
                    <span class="m">${o.startsWith('windows') ? 'RDP · พร้อมใช้ ~5 นาที'
                      : o === 'kali-linux' ? 'SSH · เครื่องมือพร้อมใน ~15 นาที' : 'SSH · พร้อมใช้ ~2 นาที'}</span>
                  </button>`)}
              </div>`}
            ${source === 'product' && html`
              <div class="prod-grid">
                ${catalog.products.map(p => html`
                  <button key=${p.id} class="prod-card" aria-pressed=${product === p.id} onClick=${() => setProduct(p.id)}>
                    <div class="pname">${p.product_name}</div>
                    <div class="pdesc">${p.description}</div>
                  </button>`)}
                ${catalog.products.length === 0 && html`<div class="muted">ยังไม่มี product preset ในโฟลเดอร์ products/</div>`}
              </div>`}
            ${source === 'ami' && html`
              <div class="row">
                <div class="field"><label for="ami">AMI ID <span class="req">*</span></label>
                  <input id="ami" class="mono" type="text" value=${form.custom_ami} onInput=${set('custom_ami')} placeholder="ami-0123456789abcdef0" /></div>
                <div class="field"><label for="amiu">Username <span class="req">*</span></label>
                  <input id="amiu" type="text" value=${form.custom_username} onInput=${set('custom_username')} /></div>
              </div>
              <label class="checkbox"><input type="checkbox" checked=${form.custom_is_windows}
                onChange=${(e) => setForm({ ...form, custom_is_windows: e.target.checked })} />
                AMI นี้เป็น Windows (เข้าด้วย RDP และถอดรหัสผ่านจาก key)</label>`}

            <div style="display:flex; gap:28px; flex-wrap:wrap; margin-top:20px; align-items:flex-end">
              <div>
                <div class="flabel">จำนวนเครื่อง <span class="opt">— คนละเครื่อง</span></div>
                <div class="stepper">
                  <button aria-label="ลด" disabled=${learners.length <= 1} onClick=${() => setCount(learners.length - 1)}>−</button>
                  <input type="number" min="1" max=${MAX_USERS} value=${learners.length} aria-label="จำนวนเครื่อง"
                    onInput=${(e) => setCount(parseInt(e.target.value, 10))} />
                  <button aria-label="เพิ่ม" disabled=${learners.length >= MAX_USERS} onClick=${() => setCount(learners.length + 1)}>+</button>
                </div>
              </div>
              <div class="tagrow" style="flex:1; min-width:200px; padding-bottom:8px">
                ${learners.slice(0, 8).map(u => html`<span class="tag" key=${u}>${u}</span>`)}
                ${learners.length > 8 && html`<span class="muted">+${learners.length - 8}</span>`}
                ${form.instructorKali && html`<span class="tag" style="background:var(--rail); color:var(--rail-ink)">instructor · Kali</span>`}
              </div>
            </div>
            <label class="checkbox" style="margin-top:16px">
              <input type="checkbox" checked=${form.instructorKali}
                onChange=${(e) => setForm({ ...form, instructorKali: e.target.checked })} />
              <span><b>เพิ่มเครื่อง Instructor (Kali Linux)</b> — ชื่อ <span class="mono">instructor</span> แยกจากเครื่องผู้เรียน
                <br /><span class="help">สร้างจาก Debian 12 แล้วติดตั้ง Kali (kali-linux-headless) ตอนบูต — เข้า SSH ได้ทันที เครื่องมือพร้อมใน ~15 นาที (ไม่ต้อง Subscribe Marketplace)</span></span>
            </label>
          </section>

          <section class="form-block">
            <h2 class="block-title"><span class="step-n">3</span>ชื่อและเวลา</h2>
            <div class="row">
              <div class="field"><label for="ln">ชื่อ lab <span class="opt">— ใช้เป็น prefix ของ resource บน AWS</span></label>
                <input id="ln" class="mono" type="text" value=${form.lab_name} onInput=${set('lab_name')} />
                ${form.lab_name && !nameOk && html`<div class="help bad">ใช้ได้แค่ a-z, 0-9, - (ไม่เกิน 32 ตัว)</div>`}</div>
              <div class="field" style="flex:0 0 200px; min-width:180px"><label for="ttl">ลบอัตโนมัติหลัง (ชม.)</label>
                <input id="ttl" type="number" min="0" max="336" step="0.5" value=${form.ttl_hours} onInput=${set('ttl_hours')} />
                <div class="help">0 = ไม่ลบเอง</div></div>
            </div>
            <div class="field" style="margin-bottom:0"><label for="tt">ชื่อที่แสดง <span class="opt">— ไม่บังคับ เช่นชื่อลูกค้า/งาน</span></label>
              <input id="tt" type="text" value=${form.title} onInput=${set('title')} placeholder="เช่น PoC ลูกค้า BGC" /></div>
          </section>

          <section class="form-block">
            <details>
              <summary>ขั้นสูง — ชื่อผู้ใช้เอง, ขนาดเครื่อง, network, lab guide${paramSpecs.length ? ', ข้อมูล product' : ''}</summary>
              <div style="margin-top:14px">
                <${UserEditor} users=${form.users} onChange=${(users) => setForm({ ...form, users, customNames: true })} />
                <div class="row">
                  <div class="field"><label for="it">Instance type</label>
                    <input id="it" class="mono" type="text" value=${form.instance_type} onInput=${set('instance_type')} placeholder=${effType} /></div>
                  <div class="field"><label for="disk">Disk (GB)</label>
                    <input id="disk" type="number" value=${form.root_volume_gb} onInput=${set('root_volume_gb')} placeholder=${meta?.root_volume_gb || 30} /></div>
                  <div class="field"><label for="rg">Region</label>
                    <input id="rg" class="mono" type="text" value=${form.region} onInput=${set('region')} /></div>
                </div>
                <div class="row">
                  <div class="field"><label for="cidr">เปิดให้เข้าจาก (CIDR)</label>
                    <input id="cidr" class="mono" type="text" value=${form.allowed_cidr} onInput=${set('allowed_cidr')} placeholder="0.0.0.0/0" />
                    <div class="help">แคบได้ยิ่งดี เช่น IP ของห้องประชุมลูกค้า</div></div>
                  <div class="field"><label for="ports">พอร์ตเพิ่ม</label>
                    <input id="ports" class="mono" type="text" value=${form.extra_open_ports} onInput=${set('extra_open_ports')} placeholder="80, 8080" /></div>
                </div>
                <label class="checkbox" style="margin-bottom:18px"><input type="checkbox" checked=${form.use_spot}
                  onChange=${(e) => setForm({ ...form, use_spot: e.target.checked })} />
                  ใช้ spot instance — ถูกกว่า ~60% แต่ AWS ยึดคืนได้ และหยุดชั่วคราว (stop) ไม่ได้</label>
                ${paramSpecs.map(([key, spec]) => html`
                  <div class="field" key=${key}>
                    <label>${key} ${spec.required ? html`<span class="req">*</span>`
                      : html`<span class="opt">ไม่บังคับ${spec.default ? ` — default ${spec.default}` : ''}</span>`}</label>
                    <input type="text" value=${params[key] || ''} onInput=${(e) => setParams({ ...params, [key]: e.target.value })} />
                    ${spec.description && html`<div class="help">${spec.description}</div>`}
                  </div>`)}
                <${GuidePicker} value=${guide} onChange=${setGuide} productGuide=${meta?.lab_guide ? meta.product_name : null} />
              </div>
            </details>
          </section>
        </div>

        <aside class="card summary" aria-label="สรุปก่อนสร้าง">
          <div class="head">
            <div class="t">ค่าใช้จ่ายโดยประมาณ</div>
            <div class="big">$${(hourly * n).toFixed(3)}<span class="unit">/ชม.</span></div>
          </div>
          <div class="body">
            <div class="rows">
              <div><span class="k">Account</span><span class="v">${aws?.connected ? (aws.profile || aws.account) : '—'}</span></div>
              <div><span class="k">เครื่อง</span><span class="v">${learners.length} × ${machineLabel}</span></div>
              ${form.instructorKali && html`<div><span class="k">Instructor</span><span class="v">1 × Kali Linux</span></div>`}
              <div><span class="k">ขนาด</span><span class="v mono">${effType}</span></div>
              <div><span class="k">ลบอัตโนมัติ</span><span class="v">${ttlNum > 0 ? `${form.ttl_hours} ชม. (~$${(hourly * n * ttlNum).toFixed(2)})` : 'ปิด'}</span></div>
            </div>
            <${ErrBox} err=${err} />
            ${problems.length > 0 && html`<div class="help bad" style="margin-top:12px">${problems[0]}</div>`}
            <button class="btn primary block" style="margin-top:14px" disabled=${busy || problems.length > 0} onClick=${submit}>
              ${busy ? 'กำลังส่ง…' : `สร้าง ${n} เครื่อง`}</button>
            <div class="foot">on-demand ${form.region} · ราคาจริงอาจต่างเล็กน้อย (EBS, network)</div>
          </div>
        </aside>
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
  reset: [['reset', 'สร้างเครื่องของผู้เรียนใหม่'], ['export', 'สร้าง handout ใหม่'], ['done', 'เสร็จสิ้น']],
  export: [['export', 'สร้าง handout + zip ใหม่'], ['done', 'เสร็จสิ้น']],
  retire_account: [['done', 'destroy lab ครบ → sign out SSO → ลบโปรไฟล์']],
  remove_user: [['destroy', 'ลบเครื่องบน AWS'], ['cleanup', 'ลบ key/handout ของเครื่องนี้'], ['export', 'สร้าง handout ใหม่'], ['done', 'เสร็จสิ้น']],
  add_users: [['apply', 'สร้างเครื่องที่เพิ่มบน AWS'], ['export', 'สร้าง handout ใหม่'], ['done', 'เสร็จสิ้น']],
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
        ${done ? (failed ? 'งานล้มเหลว' : kind === 'destroy' ? 'ลบเสร็จแล้ว'
            : kind === 'retire_account' ? 'ลบ account เสร็จแล้ว' : kind === 'remove_user' ? 'ลบเครื่องเสร็จแล้ว' : kind === 'add_users' ? 'เพิ่มเครื่องเสร็จแล้ว'
            : kind === 'export' ? 'สร้าง handout ใหม่เสร็จแล้ว' : 'สร้างเสร็จแล้ว')
          : kind === 'destroy' ? 'กำลังลบ' : kind === 'retire_account' ? 'กำลังลบ account' : kind === 'remove_user' ? 'กำลังลบเครื่อง'
          : kind === 'add_users' ? 'กำลังเพิ่มเครื่อง' : kind === 'refresh' ? 'กำลัง refresh'
          : kind === 'export' ? 'กำลังสร้าง handout ใหม่' : 'กำลังสร้าง'}
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
        ${failed && html`<div class="err">งานล้มเหลว: <${Linkify} text=${done.error || 'ดูรายละเอียดใน log ด้านล่าง'} /></div>`}
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
              ${/* destroy ที่สำเร็จแล้วไม่มี lab ให้เปิด — แต่ถ้าล้มเหลว lab ยังอยู่
                    และต้องกลับไปหน้า dashboard เพื่อกด Destroy ซ้ำได้ */
                (kind !== 'destroy' || failed) && html`<button class="btn primary"
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

// แนบ/เปลี่ยน lab guide หลัง lab ถูกสร้างไปแล้ว — สร้าง handout ใหม่อย่างเดียว
// ไม่แตะ VM จึงกดซ้ำได้ระหว่างสอนโดยไม่กระทบผู้เรียนที่ต่ออยู่
function GuideCard({ labName, disabled, onDone }) {
  const [guides, setGuides] = useState([]);
  const [current, setCurrent] = useState(null);
  const [choice, setChoice] = useState('');
  const [markdown, setMarkdown] = useState('');
  const [pdf, setPdf] = useState('');
  const [pdfName, setPdfName] = useState('');
  const [mode, setMode] = useState('pick');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [okMsg, setOkMsg] = useState(null);
  const [jobId, setJobId] = useState(null);

  useEffect(() => {
    api('/lab-guides').then(r => setGuides(r.guides)).catch(e => setErr(e.message));
    api(`/labs/${labName}/guide`).then((g) => {
      setCurrent(g);
      setChoice(g.lab_guide || '');
      if (g.content) setMarkdown(g.content);
    }).catch(() => { /* lab ที่ไม่มี config ไม่ต้องแสดง current */ });
  }, [labName]);

  // สร้าง handout ใหม่แล้วอยู่หน้าเดิม — งานนี้ไม่แตะ terraform จึงจบในไม่กี่วินาที
  // พาไปหน้า log แล้วให้เดินกลับมากดดาวน์โหลดเองมีแต่ทำให้เสียจังหวะ
  async function apply() {
    setBusy(true); setErr(null); setOkMsg(null);
    try {
      const body = mode !== 'write' ? { guide: choice }
        : (pdf ? { pdf } : { markdown });
      const r = await api(`/labs/${labName}/guide`, { method: 'PUT', body: JSON.stringify(body) });
      for (let i = 0; i < 90; i++) {
        await new Promise(res => setTimeout(res, 1000));
        const job = await api(`/jobs/${r.job_id}`);
        if (job.state === 'success') {
          setOkMsg('สร้าง handout ใหม่แล้ว — ดาวน์โหลด zip ด้านล่างได้เลย');
          setCurrent(await api(`/labs/${labName}/guide`));
          if (onDone) onDone();
          setBusy(false);
          return;
        }
        if (job.state === 'error') {
          setErr(job.error || 'สร้าง handout ไม่สำเร็จ');
          setJobId(r.job_id);
          setBusy(false);
          return;
        }
      }
      setJobId(r.job_id);
      setErr('งานใช้เวลานานผิดปกติ — เปิดดู log ได้จากลิงก์ด้านล่าง');
    } catch (e) { setErr(e.message); }
    setBusy(false);
  }

  const canApply = !busy && !disabled
    && (mode === 'write' ? (markdown.trim() || pdf) : choice);

  return html`
    <div class="card" style="margin-top:18px">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap">
        <div>
          <div style="font:700 14px var(--sans)">เปลี่ยน Hands-on Lab Guide</div>
          <div class="muted" style="font:400 12.5px var(--sans); margin-top:3px">
            ${current?.lab_guide
              ? html`ใช้อยู่: <span class="mono">${current.lab_guide}</span>${
                  current.kind === 'pdf' ? ' · PDF' : ''}${current.exists ? '' : ' (ไม่พบไฟล์)'}`
              : 'lab นี้ยังไม่มี guide — แนบตอนนี้แล้วสร้าง handout ใหม่ได้'}
          </div>
        </div>
        <div style="display:flex; gap:6px">
          <button class="btn ghost small" style=${mode === 'pick' ? 'border-color:var(--accent)' : ''}
            onClick=${() => setMode('pick')}>เลือกไฟล์ที่มี</button>
          <button class="btn ghost small" style=${mode === 'write' ? 'border-color:var(--accent)' : ''}
            onClick=${() => setMode('write')}>เขียน/วางเอง</button>
        </div>
      </div>

      <${ErrBox} err=${err} />

      ${current && !current.lab_guide && html`
        <div class="warn-box">
          handout ที่ดาวน์โหลดตอนนี้จะมีแต่วิธีเข้าเครื่อง ยังไม่มีเนื้อหา lab —
          แนบ guide ตรงนี้ก่อนแล้วค่อยแจก zip ให้ผู้เรียน
        </div>`}
      ${current && current.lab_guide && !current.exists && html`
        <div class="err">ไฟล์ <span class="mono">${current.lab_guide}</span> หายไปจากเครื่อง —
          handout รอบหน้าจะไม่มีเนื้อหา guide จนกว่าจะแนบใหม่</div>`}

      ${mode === 'pick' ? html`
        <div class="field" style="margin-top:12px">
          <select value=${choice} onChange=${(e) => setChoice(e.target.value)}>
            <option value="">— เลือก lab guide —</option>
            ${guides.map(g => html`
              <option value=${g.path} key=${g.path}>${g.name} · ${g.source}</option>`)}
          </select>
        </div>`
      : html`
        <div class="field" style="margin-top:12px">
          <label>Markdown <span class="opt">อัปโหลดไฟล์มาแก้ต่อ หรือพิมพ์เองก็ได้</span></label>
          <input type="file" accept=".md,.markdown,.txt,.pdf,text/markdown,text/plain,application/pdf"
            style="font:400 12.5px var(--sans); padding:7px; margin-bottom:8px"
            onChange=${(e) => {
              const f = e.target.files && e.target.files[0];
              if (!f) return;
              const isPdf = /\.pdf$/i.test(f.name) || f.type === 'application/pdf';
              if (f.size > (isPdf ? 20 * 1024 * 1024 : 512 * 1024)) {
                setErr(`ไฟล์ใหญ่เกิน ${isPdf ? '20 MB' : '512 KB'}`); return;
              }
              const rd = new FileReader();
              rd.onerror = () => setErr('อ่านไฟล์ไม่สำเร็จ');
              if (isPdf) {
                rd.onload = () => { setErr(null); setPdf(String(rd.result)); setPdfName(f.name); };
                rd.readAsDataURL(f);
              } else {
                rd.onload = () => { setErr(null); setPdf(''); setPdfName(''); setMarkdown(String(rd.result)); };
                rd.readAsText(f);
              }
            }} />
          ${pdfName && html`<div class="help">แนบ PDF: <b>${pdfName}</b> —
            จะถูกแนบไปในโฟลเดอร์ของผู้เรียนทุกคน (เนื้อหาด้านล่างจะไม่ถูกใช้)</div>`}
          <textarea rows="12" value=${markdown} placeholder="# ขั้นตอนที่ 1\n..."
            style="font:400 12.5px var(--mono); resize:vertical"
            onInput=${(e) => setMarkdown(e.target.value)}></textarea>
        </div>`}

      ${okMsg && html`<div class="ok-box" style="margin-top:12px">✔ ${okMsg}</div>`}
      ${jobId && html`<div class="help">
        <a href="#/job/${jobId}?lab=${labName}">เปิดดู log ของงานนี้ →</a></div>`}

      <div class="btnbar">
        <span class="muted" style="font:400 12px var(--sans)">
          สร้าง handout + zip ใหม่ทุกคน — ไม่แตะ VM ที่รันอยู่</span>
        <button class="btn primary small" disabled=${!canApply} onClick=${apply}>
          ${busy ? 'กำลังสร้าง handout…' : 'ใส่ guide แล้วสร้าง handout ใหม่'}</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// เพิ่มเครื่องใน lab ที่รันอยู่
// ---------------------------------------------------------------------------

const MAX_ADD = 50;
const USER_RE = /^[a-z0-9-]{1,30}$/;

// ต้องตรงกับ _next_user_names ฝั่ง backend — ชื่อที่ preview คือชื่อที่จะได้จริง
function nextUserNames(existing, count) {
  const nums = existing.map(u => /^user(\d+)$/.exec(u)).filter(Boolean).map(m => +m[1]);
  const start = Math.max(0, ...nums) + 1;
  return Array.from({ length: count }, (_, i) => `user${String(start + i).padStart(2, '0')}`);
}

function AddUsersModal({ labName, existing, labOs, onClose }) {
  const [mode, setMode] = useState('count');
  // custom AMI ไม่อยู่ใน OS_LABELS — ใช้ค่าว่างแทน "เหมือนเครื่องเดิม"
  const [os, setOs] = useState(OS_LABELS[labOs] ? labOs : '');
  const [count, setCount] = useState(1);
  const [namesText, setNamesText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const typed = namesText.split(/[\s,]+/).map(s => s.trim().toLowerCase()).filter(Boolean);
  const names = mode === 'count' ? nextUserNames(existing, count) : typed;
  const bad = mode === 'names' ? typed.filter(u => !USER_RE.test(u)) : [];
  const taken = names.filter((u, i) => existing.includes(u) || names.indexOf(u) !== i);
  const problem = [
    bad.length && `ชื่อใช้ได้แค่ a-z, 0-9, - : ${bad.join(', ')}`,
    taken.length && `มีชื่อนี้อยู่แล้ว: ${[...new Set(taken)].join(', ')}`,
  ].filter(Boolean).join(' · ') || null;
  const canSubmit = names.length > 0 && !problem && !busy;
  const setN = (n) => setCount(Math.min(MAX_ADD, Math.max(1, n || 1)));

  async function submit() {
    if (!canSubmit) return;
    setBusy(true); setErr(null);
    const body = { ...(mode === 'count' ? { count } : { users: names }), ...(os ? { os } : {}) };
    try {
      const r = await api(`/labs/${labName}/users`, { method: 'POST', body: JSON.stringify(body) });
      nav(`/job/${r.job_id}?lab=${labName}`);
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  return html`
    <div class="modal-overlay" onClick=${(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div class="modal add-modal" role="dialog" aria-modal="true" aria-labelledby="add-title">
        <div id="add-title" class="modal-title">เพิ่มเครื่องให้ผู้เรียน</div>
        <div class="modal-sub">ตอนนี้มี ${existing.length} เครื่อง · เครื่องเดิมไม่ถูกแตะ</div>

        <div class="seg" role="tablist">
          <button role="tab" aria-selected=${mode === 'count'} class=${mode === 'count' ? 'on' : ''}
            onClick=${() => setMode('count')}>ตามจำนวน</button>
          <button role="tab" aria-selected=${mode === 'names'} class=${mode === 'names' ? 'on' : ''}
            onClick=${() => setMode('names')}>ตั้งชื่อเอง</button>
        </div>

        ${mode === 'count' ? html`
          <div class="flabel">จำนวนเครื่อง</div>
          <div class="stepper">
            <button aria-label="ลด" disabled=${count <= 1} onClick=${() => setN(count - 1)}>−</button>
            <input type="number" min="1" max=${MAX_ADD} value=${count} autofocus
              onInput=${(e) => setN(parseInt(e.target.value, 10))}
              onKeyDown=${(e) => { if (e.key === 'Enter') submit(); }} />
            <button aria-label="เพิ่ม" disabled=${count >= MAX_ADD} onClick=${() => setN(count + 1)}>+</button>
          </div>` : html`
          <div class="flabel">ชื่อผู้เรียน <span class="opt">— คั่นด้วย , หรือเว้นวรรค</span></div>
          <input type="text" value=${namesText} placeholder="alice, bob" autofocus
            onInput=${(e) => setNamesText(e.target.value)}
            onKeyDown=${(e) => { if (e.key === 'Enter') submit(); }} />`}

        ${!existing.includes('instructor') && html`
          <button type="button" class="btn ghost small" style="margin-top:12px"
            onClick=${() => { setMode('names'); setNamesText('instructor'); setOs('kali-linux'); }}>
            + เครื่อง Instructor (Kali Linux)</button>`}

        <div class="flabel" style="margin-top:16px">OS</div>
        <select value=${os} onChange=${(e) => setOs(e.target.value)}>
          ${!OS_LABELS[labOs] && html`<option value="">เหมือนเครื่องเดิม (custom AMI)</option>`}
          ${Object.entries(OS_LABELS).map(([k, v]) => html`
            <option value=${k}>${v}${k === labOs ? ' — เหมือนเครื่องเดิม' : ''}</option>`)}
        </select>
        ${os && OS_LABELS[labOs] && os !== labOs && html`<div class="modal-note" style="margin-top:6px">
          ต่างจากเครื่องเดิม — สคริปต์ติดตั้งของ lab จะไม่รันบนเครื่องนี้${os === 'kali-linux'
            ? ' · Kali ติดตั้งเครื่องมือตอนบูต ใช้เวลาเพิ่ม ~15 นาที' : ''}</div>`}

        <div class="add-preview">
          ${names.length === 0
            ? html`<span class="muted">พิมพ์ชื่ออย่างน้อย 1 คน</span>`
            : html`<span class="muted">จะสร้าง</span>
              ${names.map(u => html`<span key=${u} class="tag ${bad.includes(u) || taken.includes(u) ? 'bad' : ''}">${u}</span>`)}`}
        </div>
        ${problem && html`<div class="field-err">${problem}</div>`}
        <div class="modal-note">ใช้เวลาประมาณ ${(os || labOs || '').startsWith('windows') ? '5–8' : '2–3'} นาที — handout ของเครื่องใหม่จะพร้อมเมื่อเสร็จ</div>
        <${ErrBox} err=${err} />

        <div class="modal-actions">
          <button class="btn ghost" disabled=${busy} onClick=${onClose}>ยกเลิก</button>
          <button class="btn primary" disabled=${!canSubmit} onClick=${submit}>
            ${busy ? 'กำลังเริ่ม…' : `สร้าง ${names.length || ''} เครื่อง`}</button>
        </div>
      </div>
    </div>`;
}

function RemoveUserModal({ labName, machine, onClose }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const r = await api(`/labs/${labName}/users/${machine.user}`, { method: 'DELETE' });
      nav(`/job/${r.job_id}?lab=${labName}`);
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  return html`
    <div class="modal-overlay" onClick=${(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="rm-title">
        <div id="rm-title" class="modal-title">ลบเครื่องของ <span class="mono">${machine.user}</span>?</div>
        <div class="modal-sub" style="margin-bottom:0">
          VM <span class="mono">${machine.public_ip || machine.instance_id}</span>, key และ handout
          ของคนนี้จะถูกลบถาวร — เครื่องคนอื่นในคลาสไม่กระทบ</div>
        <${ErrBox} err=${err} />
        <div class="modal-actions">
          <button class="btn ghost" disabled=${busy} onClick=${onClose} autofocus>ยกเลิก</button>
          <button class="btn danger" disabled=${busy} style="background:var(--crit); color:#fff"
            onClick=${submit}>${busy ? 'กำลังเริ่ม…' : 'ลบเครื่องนี้'}</button>
        </div>
      </div>
    </div>`;
}

function Dashboard({ labName }) {
  const [lab, setLab] = useState(null);
  const [err, setErr] = useState(null);
  const [revealed, setRevealed] = useState({});
  const [copied, setCopied] = useState({});
  const [destroyOpen, setDestroyOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [delGuide, setDelGuide] = useState(false);
  const [notice, setNotice] = useState(null);
  const [guidePath, setGuidePath] = useState(null);
  const [reach, setReach] = useState({});
  const [reachBusy, setReachBusy] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [removing, setRemoving] = useState(null);

  const load = useCallback(() => {
    api(`/labs/${labName}`).then(setLab).catch(e => setErr(e.message));
    api(`/labs/${labName}/guide`).then(g => setGuidePath(g.lab_guide)).catch(() => {});
  }, [labName]);
  useEffect(load, [load]);

  // guide ที่อัปโหลดให้ lab นี้โดยเฉพาะ — ตัวเดียวที่เสนอให้ลบพร้อม lab ได้
  // guide ที่อัปโหลดให้ lab นี้โดยเฉพาะ (md หรือ pdf) — ต้องเป็น boolean เสมอ
  // เพราะค่านี้ถูกส่งไป backend ตรง ๆ: null && x ได้ null แล้ว backend ตอบ 422
  const ownGuide = !!guidePath && [`lab-guides/${labName}-custom.md`,
    `lab-guides/${labName}-custom.pdf`].includes(guidePath);

  function copyIp(m) {
    try { navigator.clipboard.writeText(m.public_ip); } catch (e) { /* noop */ }
    setCopied(c => ({ ...c, [m.user]: true }));
    setTimeout(() => setCopied(c => ({ ...c, [m.user]: false })), 1600);
  }

  // ตรวจว่าพอร์ต SSH/RDP เปิดรับแล้วหรือยัง — รู้ก่อนเริ่มคลาสว่าเครื่องไหนยังไม่พร้อม
  async function checkReach() {
    setReachBusy(true); setErr(null);
    try {
      const r = await api(`/labs/${labName}/reachability`);
      setReach(Object.fromEntries(r.results.map(x => [x.user, x])));
    } catch (e) { setErr(e.message); }
    setReachBusy(false);
  }

  // เปิดคลาสเดิมรอบใหม่ — เติมค่าเดิมให้ wizard แล้วให้ผู้สอนแก้ชื่อ/ผู้เรียนเอง
  function cloneLab() {
    const c = lab.config || {};
    // user_os ผูกกับรายชื่อเดิม — ถ้าติดไปด้วย แก้รายชื่อใน wizard แล้วจะ validate ไม่ผ่าน
    // ยกเว้นเครื่อง instructor (Kali) ซึ่งหน้าสร้าง lab รู้จักเป็นตัวเลือกแยก
    const { user_os, ...rest } = c;
    const keep = user_os && user_os.instructor ? { user_os: { instructor: user_os.instructor } } : {};
    PREFILL = { ...rest, ...keep, lab_name: '', ttl_hours: lab.ttl_hours ?? 8 };
    nav('/new');
  }

  async function doExtend(hours) {
    try {
      const r = await api(`/labs/${labName}/extend`, {
        method: 'POST', body: JSON.stringify({ hours }),
      });
      setErr(null);
      setNotice(`ต่อเวลาอีก ${hours} ชม. — ลบอัตโนมัติ ${new Date(r.expires_at * 1000)
        .toLocaleString('th-TH', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}`);
      load();
    } catch (e) { setErr(e.message); }
  }

  async function doReset(user) {
    if (!confirm(`สร้างเครื่องของ ${user} ใหม่? ข้อมูลในเครื่องเดิมจะหายทั้งหมด (เครื่องคนอื่นไม่กระทบ)`)) return;
    try {
      const r = await api(`/labs/${labName}/users/${user}/reset`, { method: 'POST' });
      nav(`/job/${r.job_id}?lab=${labName}`);
    } catch (e) { setErr(e.message); }
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
        method: 'DELETE',
        body: JSON.stringify({ confirm: confirmText, delete_guide: Boolean(ownGuide && delGuide) }),
      });
      setDestroyOpen(false);
      nav(`/job/${r.job_id}?lab=${labName}`);
    } catch (e) { setErr(e.message); }
  }

  if (!lab) return html`<div class="screen"><div class="card"><div class="empty">${err || 'กำลังโหลด…'}</div></div></div>`;

  const busyJob = lab.job && ['queued', 'running'].includes(lab.job.state);
  const msLeft = lab.expires_at ? lab.expires_at * 1000 - Date.now() : null;
  const isActive = lab.status === 'active';

  return html`
    <div class="screen">
      <header class="lab-head">
        <div style="min-width:0">
          <h1>${lab.title || lab.product_name || lab.lab_name}</h1>
          <div class="meta">
            <${Pill} status=${lab.status} />
            <span class="mono">${lab.lab_name}</span>
            <span class="sep">/</span>
            <span>${[OS_LABELS[lab.os] || lab.os, lab.instance_type, lab.region].filter(Boolean).join(' · ')}</span>
            ${(lab.aws_profile || lab.aws_account) && html`<span class="sep">/</span>
              <span title="AWS account ที่ lab นี้อยู่">${lab.aws_profile || 'AWS'} <span class="mono">${lab.aws_account || ''}</span></span>`}
          </div>
        </div>
        <div class="actions">
          ${busyJob && html`<button class="btn primary small"
            onClick=${() => nav(`/job/${lab.job.id}?lab=${labName}`)}>ดูงานที่กำลังรัน →</button>`}
          ${!busyJob && lab.job && lab.job.state === 'error' && html`<button class="btn ghost small"
            onClick=${() => nav(`/job/${lab.job.id}?lab=${labName}`)}>ดู log ที่ล้มเหลว</button>`}
          <button class="btn ghost small" onClick=${checkReach} disabled=${reachBusy || busyJob}>
            ${reachBusy ? 'กำลังตรวจ…' : 'ตรวจการเชื่อมต่อ'}</button>
          <button class="btn ghost small" onClick=${doRefresh} disabled=${busyJob}><${Icon} name="refresh" />Refresh</button>
          <a class="btn ghost small" href="/api/labs/${labName}/handouts/export" download><${Icon} name="download" />handout ทั้งหมด</a>
          <button class="btn ghost small" onClick=${cloneLab}>สร้างซ้ำ</button>
          <button class="btn danger small" disabled=${busyJob}
            onClick=${() => { setConfirmText(''); setDestroyOpen(true); }}>Destroy…</button>
        </div>
        ${isActive && html`
          <div class="timeline">
            <span class="k">ลบอัตโนมัติใน</span>
            <${TimeBar} expiresAt=${lab.expires_at} ttlHours=${lab.ttl_hours} />
            <span class="btn-row" style=${lab.expires_at ? '' : 'visibility:hidden'}>
              <button class="btn ghost small" disabled=${busyJob} onClick=${() => doExtend(1)}>+1 ชม.</button>
              <button class="btn ghost small" disabled=${busyJob} onClick=${() => doExtend(2)}>+2 ชม.</button>
            </span>
          </div>`}
      </header>
      <${ErrBox} err=${err} />
      ${notice && html`<div class="ok-box">${notice}</div>`}
      ${isActive && msLeft !== null && msLeft > 0 && msLeft < 30 * 60000 && html`
        <div class="warn-box">lab นี้จะถูกลบอัตโนมัติใน ${Math.round(msLeft / 60000)} นาที — ถ้ายังใช้อยู่ ให้กด +1 ชม. ก่อน</div>`}

      <div class="desk-head">
        <h2>${lab.machines.length} เครื่อง</h2>
        ${isActive && lab.machines.length > 0 && html`<span class="muted" style="font-size:13px">
          บัตรละหนึ่งคน · reset/ลบทีละเครื่องได้ เครื่องอื่นไม่กระทบ</span>`}
      </div>
      ${lab.machines.length === 0
        ? html`<div class="card"><div class="empty">
            ${lab.status === 'destroyed' ? 'lab นี้ถูกลบแล้ว'
             : ['queued', 'provisioning'].includes(lab.status) ? 'กำลังสร้างเครื่อง — ดูความคืบหน้าได้จากปุ่ม "ดูงานที่กำลังรัน"'
             : 'ยังอ่านข้อมูลเครื่องไม่ได้ — ลอง Refresh'}</div></div>`
        : html`
        <div class="mac-grid">
          ${lab.machines.map(m => {
            const r = reach[m.user];
            const pending = !m.public_ip || (m.is_windows && m.password === null);
            const state = r && !r.reachable ? 'down' : pending ? 'pending' : '';
            const statusText = r ? (r.reachable ? `พร้อมใช้ · ตอบใน ${r.ms} ms` : 'ติดต่อเครื่องไม่ได้')
              : !m.public_ip ? 'ยังไม่มี IP' : pending ? 'กำลังบูต — รอรหัสผ่าน Windows' : 'พร้อมใช้';
            return html`
            <article class="badge ${osFamily(m.os, m.is_windows)} ${state}" key=${m.user}>
              <div class="band">
                <span>${OS_LABELS[m.os] || m.os}</span>
                <span class="proto">${m.is_windows ? 'RDP · 3389' : 'SSH · 22'}</span>
              </div>
              <div class="who">
                <div class="name">${m.user}</div>
                <div class="status-line">● ${statusText}</div>
              </div>
              <div class="fine">
                <div><span class="k">IP</span><span class="v">${m.public_ip || '—'}</span>
                  ${m.public_ip ? html`<button class="btn tiny" aria-label="คัดลอก IP ของ ${m.user}"
                    style=${copied[m.user] ? 'color:var(--good); border-color:var(--good)' : ''}
                    onClick=${() => copyIp(m)}>${copied[m.user] ? 'คัดลอกแล้ว' : 'คัดลอก'}</button>` : html`<span></span>`}</div>
                <div><span class="k">Username</span><span class="v">${m.username}</span><span></span></div>
                <div><span class="k">Password</span>
                  ${!m.is_windows ? html`<span class="v sans">ใช้ key ใน handout</span><span></span>`
                  : m.password === null ? html`<span class="v sans">ยังไม่พร้อม (~5 นาทีหลังบูต)</span><span></span>`
                  : revealed[m.user]
                    ? html`<span class="v"><span class="pw-shown">${m.password}</span></span>
                        <button class="btn tiny" onClick=${() => setRevealed({ ...revealed, [m.user]: false })}>ซ่อน</button>`
                    : html`<span class="v masked">••••••••</span>
                        <button class="btn tiny" onClick=${() => setRevealed({ ...revealed, [m.user]: true })}>แสดง</button>`}
                </div>
              </div>
              <div class="foot">
                <span class="handout">${m.handout_ready
                  ? html`<a class="btn tiny" href="/api/labs/${labName}/handouts/${m.user}.zip" download>
                      <${Icon} name="download" />handout</a>`
                  : html`<span class="muted" style="font-size:12px">handout ยังไม่พร้อม</span>`}</span>
                ${isActive && html`<button class="btn tiny" disabled=${busyJob}
                  title="สร้างเครื่องของคนนี้ใหม่ — ข้อมูลในเครื่องหาย เครื่องอื่นไม่กระทบ"
                  onClick=${() => doReset(m.user)}>reset</button>`}
                ${isActive && lab.machines.length > 1 && html`<button class="btn tiny danger-text"
                  disabled=${busyJob} title="ลบเครื่องของคนนี้ เครื่องอื่นไม่กระทบ"
                  onClick=${() => setRemoving(m)}>ลบ</button>`}
              </div>
            </article>`;
          })}
          ${isActive && html`
            <button class="badge ghost-add" disabled=${busyJob} onClick=${() => setAddOpen(true)}>
              <span class="plus">+</span>เพิ่มเครื่อง</button>`}
        </div>`}

      ${isActive && lab.machines.length > 0 && html`
        <div style="margin-top:28px"><${GuideCard} labName=${labName} disabled=${busyJob} onDone=${load} /></div>`}

      ${removing && html`<${RemoveUserModal} labName=${labName} machine=${removing}
        onClose=${() => setRemoving(null)} />`}

      ${addOpen && html`<${AddUsersModal} labName=${labName}
        existing=${(lab.config && lab.config.users) || lab.machines.map(m => m.user)}
        labOs=${(lab.config && lab.config.custom_ami) ? '' : (lab.config && lab.config.os) || lab.os}
        onClose=${() => setAddOpen(false)} />`}

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
            ${ownGuide && html`
              <label class="checkbox" style="margin-top:12px">
                <input type="checkbox" checked=${delGuide}
                  onChange=${(e) => setDelGuide(e.target.checked)} />
                ลบไฟล์ lab guide ที่อัปโหลดไว้ด้วย (<span class="mono">${guidePath}</span>)
              </label>`}
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

// ถาม AWS ตรง ๆ ว่ามี resource ติด tag Project=hands-on-lab เหลืออยู่ไหม
// ใช้ตอบคำถาม "ลบหมดจริงหรือยัง" ได้แม้ terraform state จะหายไปแล้ว
function OrphanScan() {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function scan() {
    setBusy(true); setErr(null); setRes(null);
    try { setRes(await api('/aws/orphans')); } catch (ex) { setErr(ex.message); }
    setBusy(false);
  }

  const byLab = {};
  (res?.resources || []).forEach((r) => {
    const k = r.lab || '(ไม่มี tag LabName)';
    (byLab[k] = byLab[k] || []).push(r);
  });

  return html`
    <div class="card" style="margin-top:16px">
      <div class="kv-row" style="border:0;padding:0">
        <span class="k">ตรวจ resource ค้าง</span>
        <button class="btn ghost small" onClick=${scan} disabled=${busy}>
          ${busy ? 'กำลังถาม AWS…' : 'ตรวจเลย'}</button>
      </div>
      <p class="page-sub" style="margin:8px 0 0">
        ถาม AWS ตรง ๆ ว่ายังมีของติด tag <code>Project=hands-on-lab</code> เหลืออยู่ไหม —
        ไม่ได้อ่านจาก terraform state จึงเชื่อได้แม้ state จะหาย
      </p>
      <${ErrBox} err=${err} />
      ${res && res.clean && html`
        <div class="ok-box" style="margin-top:12px">
          ✔ ไม่มี resource ค้าง — ตรวจแล้วที่ ${res.regions.join(', ')}
        </div>`}
      ${res && !res.clean && html`
        <div style="margin-top:12px">
          ${res.total > 0 && html`
            <div class="err">พบ ${res.total} resource ที่ยังอยู่บน AWS — ยังเดินค่าใช้จ่ายอยู่</div>`}
          ${Object.entries(byLab).map(([lab, rows]) => html`
            <div class="kv-rows" style="margin-top:10px">
              <div class="kv-row"><span class="k">lab</span><span class="v">${lab}</span></div>
              ${rows.map((r) => html`
                <div class="kv-row">
                  <span class="k sans">${r.kind}</span>
                  <span class="v">${r.id}${r.state ? ` · ${r.state}` : ''}${r.public_ip ? ` · ${r.public_ip}` : ''}
                    <span class="sans" style="opacity:.6"> · ${r.region}</span></span>
                </div>`)}
            </div>`)}
          ${(res.errors || []).map((e) => html`
            <div class="err" style="margin-top:8px">ถาม ${e.kind} ที่ ${e.region} ไม่สำเร็จ: ${e.error}</div>`)}
          ${res.total > 0 && html`
            <p class="page-sub" style="margin:10px 0 0">
              ลบตามลำดับนี้: EC2 (รอ terminated ก่อน) → security group → subnet → IGW →
              route table → VPC → key pair
            </p>`}
        </div>`}
    </div>`;
}

const NEW_SESSION = '__new__';
const slugify = (s) => (s || '').toLowerCase().replace(/[^a-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48) || 'aws';
const sessionLoginPath = (s) => `/aws/sso-sessions/${encodeURIComponent(s)}/login`;

// SSO: เลือก session (หรือใส่ start URL ใหม่) → sign in ครั้งเดียว → เลือก account/role จากรายการ
function AddSsoAccount({ onAdded }) {
  const [sessions, setSessions] = useState(null);
  const [session, setSession] = useState('');
  const [fresh, setFresh] = useState({ name: '', start_url: '', sso_region: 'ap-southeast-1' });
  const [region, setRegion] = useState('ap-southeast-1');
  const [accts, setAccts] = useState(null);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const [added, setAdded] = useState(null);
  const [dropSession, setDropSession] = useState(false);

  const loadSessions = (pick) => api('/aws/sso-sessions').then(r => {
    setSessions(r.sessions);
    setSession(pick || (r.sessions[0] ? r.sessions[0].name : NEW_SESSION));
  }).catch(e => setErr(e.message));
  useEffect(() => { loadSessions(); }, []);

  async function loadAccounts(s) {
    if (!s || s === NEW_SESSION) { setAccts(null); return; }
    setBusy('list'); setErr(null); setNeedsLogin(false); setAccts(null);
    try { setAccts((await api(`/aws/sso-sessions/${encodeURIComponent(s)}/accounts`)).accounts); }
    catch (e) { if (e.needsLogin) setNeedsLogin(true); else setErr(e.message); }
    setBusy(null);
  }
  useEffect(() => { loadAccounts(session); }, [session]);

  const [sso, startSso] = useSsoLogin(() => loadAccounts(session), sessionLoginPath);

  async function createSession(e) {
    e.preventDefault();
    setBusy('new'); setErr(null);
    const name = slugify(fresh.name);
    try {
      await api('/aws/sso-sessions', { method: 'POST', body: JSON.stringify({ ...fresh, name }) });
      await loadSessions(name);          // useEffect จะลองดึง account → 409 → แสดงปุ่ม sign in
      startSso(name);                    // sign in เริ่มแล้วที่ backend — ตัวนี้ตามสถานะ
    } catch (ex) { setErr(ex.message); }
    setBusy(null);
  }

  async function addProfile(a, role) {
    const name = slugify(a.roles.length > 1 ? `${a.account_name}-${role.name}` : a.account_name);
    setBusy(`${a.account_id}/${role.name}`); setErr(null);
    try {
      await api(`/aws/sso-sessions/${encodeURIComponent(session)}/profiles`, { method: 'POST',
        body: JSON.stringify({ name, account_id: a.account_id, role_name: role.name, region }) });
      setAdded(name);
      onAdded();
      loadAccounts(session);
    } catch (ex) { setErr(ex.message); }
    setBusy(null);
  }

  if (!sessions) return html`<div class="muted">${err || 'กำลังอ่าน ~/.aws/config…'}</div>`;
  return html`
    <div class="row">
      <div class="field"><label for="ss">SSO login</label>
        <select id="ss" value=${session} onChange=${(e) => setSession(e.target.value)}>
          ${sessions.map(s => html`<option value=${s.name} key=${s.name}>${s.name} · ${s.start_url}</option>`)}
          <option value=${NEW_SESSION}>+ SSO ใหม่ (ใส่ start URL)</option>
        </select></div>
      <div class="field" style="flex:0 0 200px; min-width:180px"><label for="rg2">Region ที่ใช้สร้าง lab</label>
        <input id="rg2" class="mono" type="text" value=${region} onInput=${(e) => setRegion(e.target.value)} /></div>
    </div>

    ${session === NEW_SESSION && html`
      <form onSubmit=${createSession}>
        <div class="row">
          <div class="field"><label for="sn">ชื่อเรียก <span class="req">*</span></label>
            <input id="sn" class="mono" type="text" value=${fresh.name} placeholder="my-org"
              onInput=${(e) => setFresh({ ...fresh, name: e.target.value })} /></div>
          <div class="field" style="flex:0 0 200px; min-width:180px"><label for="sr2">SSO region</label>
            <input id="sr2" class="mono" type="text" value=${fresh.sso_region}
              onInput=${(e) => setFresh({ ...fresh, sso_region: e.target.value })} /></div>
        </div>
        <div class="field"><label for="su2">SSO start URL <span class="req">*</span></label>
          <input id="su2" class="mono" type="text" value=${fresh.start_url} placeholder="https://my-org.awsapps.com/start"
            onInput=${(e) => setFresh({ ...fresh, start_url: e.target.value })} />
          <div class="help">หาได้จากหน้า AWS access portal ที่ใช้ login ปกติ</div></div>
        <div class="btnbar" style="margin-top:4px">
          <button type="submit" class="btn primary" disabled=${busy === 'new' || !fresh.name || !fresh.start_url.startsWith('https://')}>
            ${busy === 'new' ? 'กำลังบันทึก…' : 'บันทึกแล้ว sign in'}</button>
        </div>
      </form>`}

    ${(() => {
      const cur = sessions.find(x => x.name === session);
      return cur && cur.profiles.length === 0 && html`
        <div class="warn-box" style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap">
          <span>SSO login <span class="mono">${cur.name}</span> ยังไม่มี account ไหนใช้</span>
          <button class="btn danger small" onClick=${() => setDropSession(true)}>ลบ SSO login นี้</button>
        </div>`;
    })()}
    ${dropSession && html`<${ConfirmModal} title=${`ลบ SSO login ${session}?`} confirmLabel="ลบ SSO login"
      onClose=${() => setDropSession(false)}
      onConfirm=${async () => {
        await api(`/aws/sso-sessions/${encodeURIComponent(session)}`, { method: 'DELETE' });
        await loadSessions();
      }}>
      ลบ start URL นี้ออกจาก ~/.aws/config — เพิ่มกลับได้ด้วย "+ SSO ใหม่"
    </${ConfirmModal}>`}
    ${needsLogin && !sso && html`
      <div class="warn-box" style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap">
        <span>ต้อง sign in ${session} ก่อนจึงจะเห็นรายการ account</span>
        <button class="btn primary small" onClick=${() => startSso(session)}>Sign in</button>
      </div>`}
    <${SsoWait} st=${sso} />
    ${busy === 'list' && html`<div class="muted">กำลังดึงรายการ account จาก SSO…</div>`}
    <${ErrBox} err=${err} />
    ${added && html`<div class="ok-box">เพิ่ม ${added} และตั้งเป็น account ที่ใช้แล้ว</div>`}

    ${accts && html`
      <div class="flabel" style="margin-top:6px">Account ที่คุณเข้าได้ (${accts.length})</div>
      <div class="acct-list" role="list">
        ${accts.length === 0 && html`<div class="muted">SSO นี้ยังไม่มี account ที่คุณเข้าได้</div>`}
        ${accts.map(a => html`
          <div class="acct-row" role="listitem" key=${a.account_id} style="grid-template-columns:minmax(0,1fr) auto">
            <div style="min-width:0">
              <div class="nm">${a.account_name || a.account_id}</div>
              <div class="ds">${[a.account_id, a.email].filter(Boolean).join(' · ')}</div>
            </div>
            <div class="btn-row">
              ${a.roles.map(r => r.profile
                ? html`<span class="kind using" key=${r.name} title=${r.name}>มีแล้ว · ${r.profile}</span>`
                : html`<button class="btn ghost small" key=${r.name} disabled=${!!busy}
                    onClick=${() => addProfile(a, r)}>${busy === `${a.account_id}/${r.name}` ? 'กำลังเพิ่ม…' : `เพิ่ม · ${r.name}`}</button>`)}
            </div>
          </div>`)}
      </div>`}`;
}

function AddKeysAccount({ onAdded }) {
  const [f, setF] = useState({ name: '', region: 'ap-southeast-1', access_key_id: '', secret_access_key: '', session_token: '' });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      await api('/aws/accounts/keys', { method: 'POST', body: JSON.stringify({
        name: f.name, access_key_id: f.access_key_id, secret_access_key: f.secret_access_key,
        session_token: f.session_token || null, region: f.region }) });
      setF({ ...f, access_key_id: '', secret_access_key: '', session_token: '' });
      onAdded();
    } catch (ex) { setErr(ex.message); }
    setBusy(false);
  }

  return html`
    <form onSubmit=${submit}>
      <div class="row">
        <div class="field"><label for="an">ชื่อ account <span class="req">*</span></label>
          <input id="an" class="mono" type="text" value=${f.name} onInput=${set('name')} placeholder="customer-poc" /></div>
        <div class="field"><label for="ar">Region ที่ใช้สร้าง lab</label>
          <input id="ar" class="mono" type="text" value=${f.region} onInput=${set('region')} /></div>
      </div>
      <div class="field"><label for="ak">Access Key ID <span class="req">*</span></label>
        <input id="ak" class="mono" type="text" value=${f.access_key_id} onInput=${set('access_key_id')} placeholder="AKIA… หรือ ASIA…" autocomplete="off" /></div>
      <div class="field"><label for="sk">Secret Access Key <span class="req">*</span></label>
        <input id="sk" type="password" value=${f.secret_access_key} onInput=${set('secret_access_key')} autocomplete="off" /></div>
      <div class="field"><label for="st">Session token <span class="opt">— เฉพาะคีย์ชั่วคราว (ขึ้นต้น ASIA)</span></label>
        <input id="st" type="password" value=${f.session_token} onInput=${set('session_token')} autocomplete="off" /></div>
      <${ErrBox} err=${err} />
      <div class="btnbar">
        <button type="submit" class="btn primary" disabled=${busy || !f.name || !f.access_key_id || !f.secret_access_key}>
          ${busy ? 'กำลังตรวจ…' : 'ตรวจแล้วบันทึก'}</button>
      </div>
    </form>`;
}

function ConfirmModal({ title, children, confirmLabel, onConfirm, onClose }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);
  async function go() {
    setBusy(true); setErr(null);
    try { await onConfirm(); onClose(); } catch (e) { setErr(e.message); setBusy(false); }
  }
  return html`
    <div class="modal-overlay" onClick=${(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="cf-title">
        <div id="cf-title" class="modal-title">${title}</div>
        <div class="modal-sub" style="margin-bottom:0">${children}</div>
        <${ErrBox} err=${err} />
        <div class="modal-actions">
          <button class="btn ghost" disabled=${busy} onClick=${onClose} autofocus>ยกเลิก</button>
          <button class="btn danger solid" disabled=${busy} onClick=${go}>${busy ? 'กำลังลบ…' : confirmLabel}</button>
        </div>
      </div>
    </div>`;
}

function AddAccount({ onAdded }) {
  const [kind, setKind] = useState('sso');
  return html`
    <section class="card" style="margin-top:18px">
      <h2 class="section-title">เพิ่ม account</h2>
      <div class="seg" role="tablist" aria-label="ประเภท account">
        <button type="button" role="tab" aria-selected=${kind === 'sso'} class=${kind === 'sso' ? 'on' : ''}
          onClick=${() => setKind('sso')}>SSO (แนะนำ)</button>
        <button type="button" role="tab" aria-selected=${kind === 'keys'} class=${kind === 'keys' ? 'on' : ''}
          onClick=${() => setKind('keys')}>Access key</button>
      </div>
      <p class="help" style="margin:0 0 16px">${kind === 'sso'
        ? 'sign in SSO ครั้งเดียว แล้วเลือก account กับ role จากรายการ — labctl เขียนโปรไฟล์ลง ~/.aws/config ให้ หมดอายุก็กด sign in ใหม่'
        : 'ตรวจกับ AWS ก่อน แล้วบันทึกเป็นโปรไฟล์ใน ~/.aws/credentials (เฉพาะชื่อนี้ — โปรไฟล์อื่นไม่ถูกแตะ)'}</p>
      ${kind === 'sso' ? html`<${AddSsoAccount} onAdded=${onAdded} />` : html`<${AddKeysAccount} onAdded=${onAdded} />`}
    </section>`;
}

function RetireAccountModal({ account, onClose, onDone }) {
  const hasLabs = account.labs.length > 0;
  const [destroyLabs, setDestroyLabs] = useState(false);
  const [signOut, setSignOut] = useState(account.kind === 'sso');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const blocked = hasLabs && !destroyLabs;
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  async function go() {
    setBusy(true); setErr(null);
    try {
      const r = await api(`/aws/accounts/${encodeURIComponent(account.name)}/retire`, {
        method: 'POST', body: JSON.stringify({ destroy_labs: destroyLabs, sign_out: signOut }) });
      onDone(r);
      const first = r.destroy_jobs[0];
      nav(first ? `/job/${first.job_id}?lab=${first.lab_name}` : `/job/${r.retire_job}?lab=account:${account.name}`);
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  return html`
    <div class="modal-overlay" onClick=${(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div class="modal" style="width:500px" role="alertdialog" aria-modal="true" aria-labelledby="ra-title">
        <div id="ra-title" class="modal-title">ลบ account <span class="mono">${account.name}</span></div>
        <div class="modal-sub">${account.account_id ? html`<span class="mono">${account.account_id}</span> · ` : ''}
          ลบโปรไฟล์ออกจาก ~/.aws (สำรองไว้ที่ ~/.aws/*.labctl-bak) แล้วเลือกได้ว่าจะเก็บกวาดบน AWS ด้วยไหม</div>
        ${hasLabs && html`
          <label class="checkbox" style="margin-bottom:12px">
            <input type="checkbox" checked=${destroyLabs} onChange=${(e) => setDestroyLabs(e.target.checked)} />
            <span><b>Destroy lab ใน account นี้ก่อน (${account.labs.length})</b> — ${account.labs.join(', ')}<br />
              <span class="help">VM, VPC และ key ทั้งหมดถูกลบถาวร — ไม่มีอะไรค้างคิดเงิน</span></span>
          </label>`}
        ${account.kind === 'sso' && html`
          <label class="checkbox">
            <input type="checkbox" checked=${signOut} onChange=${(e) => setSignOut(e.target.checked)} />
            <span><b>Sign out SSO</b> — เพิกถอน token ของเครื่องนี้กับ AWS และลบ cache
              <br /><span class="help">ถ้ายังมี account อื่นใช้ SSO login เดียวกัน จะข้ามขั้นนี้ให้เอง</span></span>
          </label>`}
        ${blocked && html`<div class="help bad" style="margin-top:12px">ต้อง destroy lab ก่อน — ไม่งั้นจะไม่เหลือ account ไว้ลบ lab เหล่านั้น</div>`}
        <${ErrBox} err=${err} />
        <div class="modal-actions">
          <button class="btn ghost" disabled=${busy} onClick=${onClose} autofocus>ยกเลิก</button>
          <button class="btn danger solid" disabled=${busy || blocked} onClick=${go}>
            ${busy ? 'กำลังเริ่ม…' : hasLabs ? `Destroy ${account.labs.length} lab แล้วลบ account` : 'ลบ account'}</button>
        </div>
      </div>
    </div>`;
}

// งานระดับ organization — ใช้ได้เฉพาะโปรไฟล์ของ management account และย้อนไม่ได้
function OrgDangerZone({ accounts }) {
  const sso = accounts.filter(a => a.kind === 'sso' && a.account_id);
  const [f, setF] = useState({ action: 'remove-access', admin_profile: '', account_id: '', permission_set: '', user_name: '', confirm: '' });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value, ...(k !== 'confirm' ? { confirm: '' } : {}) });

  function pickTarget(e) {
    const a = sso.find(x => x.account_id === e.target.value);
    setF({ ...f, account_id: e.target.value, permission_set: a?.role || f.permission_set, confirm: '' });
  }

  async function go(e) {
    e.preventDefault();
    setBusy(true); setErr(null); setDone(null);
    try {
      const r = await api(`/aws/org/${f.action}`, { method: 'POST', body: JSON.stringify(f) });
      setDone(f.action === 'close-account'
        ? `ส่งคำสั่งปิด account ${r.account_id} แล้ว — สถานะ ${r.status} (AWS ลบถาวรหลัง 90 วัน)`
        : `ถอนสิทธิ์ ${r.permission_set} ของ ${r.user} ออกจาก ${r.account_id} แล้ว (${r.status || 'ส่งคำสั่งแล้ว'})`);
      setF({ ...f, confirm: '' });
    } catch (ex) { setErr(ex.message); }
    setBusy(false);
  }

  const ready = f.admin_profile && /^\d{12}$/.test(f.account_id) && f.confirm === f.account_id
    && (f.action === 'close-account' || (f.permission_set && f.user_name));

  return html`
    <details class="card" style="margin-top:18px; border-color:color-mix(in srgb, var(--crit) 40%, var(--border))">
      <summary style="color:var(--crit)">Danger zone — ถอนสิทธิ์ SSO / ปิด AWS account</summary>
      <form onSubmit=${go} style="margin-top:14px">
        <p class="help" style="margin:0 0 14px">มีผลกับทั้ง organization และย้อนไม่ได้ ต้องใช้โปรไฟล์ของ <b>management account</b>
          (หรือ delegated admin ของ IAM Identity Center) — ไม่ใช่ account ที่จะถูกปิด</p>
        <div class="seg" role="tablist">
          <button type="button" role="tab" aria-selected=${f.action === 'remove-access'} class=${f.action === 'remove-access' ? 'on' : ''}
            onClick=${() => setF({ ...f, action: 'remove-access', confirm: '' })}>ถอนสิทธิ์ผู้ใช้</button>
          <button type="button" role="tab" aria-selected=${f.action === 'close-account'} class=${f.action === 'close-account' ? 'on' : ''}
            onClick=${() => setF({ ...f, action: 'close-account', confirm: '' })}>ปิด AWS account</button>
        </div>
        <div class="row">
          <div class="field"><label for="adm">โปรไฟล์ management account <span class="req">*</span></label>
            <select id="adm" value=${f.admin_profile} onChange=${set('admin_profile')}>
              <option value="">— เลือก —</option>
              ${accounts.map(a => html`<option value=${a.name} key=${a.name}>${a.name}${a.account_id ? ` · ${a.account_id}` : ''}</option>`)}
            </select></div>
          <div class="field"><label for="tgt">Account เป้าหมาย <span class="req">*</span></label>
            <select id="tgt" value=${f.account_id} onChange=${pickTarget}>
              <option value="">— เลือก —</option>
              ${[...new Set(sso.map(a => a.account_id))].map(id => html`<option value=${id} key=${id}>${id} · ${sso.filter(a => a.account_id === id).map(a => a.name).join(', ')}</option>`)}
            </select></div>
        </div>
        ${f.action === 'remove-access' && html`
          <div class="row">
            <div class="field"><label for="psn">Permission set <span class="req">*</span></label>
              <input id="psn" class="mono" type="text" value=${f.permission_set} onInput=${set('permission_set')} /></div>
            <div class="field"><label for="un">Username ใน Identity Center <span class="req">*</span></label>
              <input id="un" class="mono" type="text" value=${f.user_name} onInput=${set('user_name')} placeholder="name@company.com" /></div>
          </div>`}
        ${f.action === 'close-account' && html`<div class="warn-box">ปิดแล้ว account เข้าใช้ไม่ได้ทันที — กู้คืนได้ภายใน 90 วันผ่าน AWS Support เท่านั้น จากนั้นลบถาวร</div>`}
        <div class="field"><label for="cf">พิมพ์ account ID เพื่อยืนยัน</label>
          <input id="cf" class="mono" type="text" value=${f.confirm} onInput=${set('confirm')} placeholder=${f.account_id || '123456789012'} /></div>
        <${ErrBox} err=${err} />
        ${done && html`<div class="ok-box">${done}</div>`}
        <div class="btnbar">
          <button type="submit" class="btn danger solid" disabled=${busy || !ready}>
            ${busy ? 'กำลังส่งคำสั่ง…' : f.action === 'close-account' ? 'ปิด AWS account' : 'ถอนสิทธิ์'}</button>
        </div>
      </form>
    </details>`;
}

function AwsPage({ aws, onAws }) {
  const [list, setList] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null);
  const refresh = () => api('/aws/accounts').then(setList).catch(e => setErr(e.message));
  useEffect(() => { refresh(); }, []);
  const reloadStatus = async () => { onAws(await api('/aws/status?force=true')); refresh(); };
  const [sso, startSso] = useSsoLogin(() => reloadStatus());

  async function use(a) {
    setBusy(a.name); setErr(null);
    try {
      const st = await api('/aws/accounts/select', { method: 'POST', body: JSON.stringify({ profile: a.name }) });
      onAws(st);
      if (st.needs_login) startSso(a.name);
    } catch (e) {
      // SSO ที่หมดอายุ: ตั้งเป็นตัวที่ใช้แล้ว แต่ต้อง sign in ก่อน — เริ่มให้เลย
      if (e.needsLogin) startSso(a.name);
      else setErr(e.message);
    }
    setBusy(null);
    refresh();
    try { onAws(await api('/aws/status?force=true')); } catch (e2) { /* noop */ }
  }

  const [removing, setRemoving] = useState(null);

  return html`
    <div class="screen" style="max-width:860px">
      <div class="page-head">
        <div>
          <h1 class="page-title">AWS accounts</h1>
          <div class="page-sub">อ่านจากโปรไฟล์ใน ~/.aws — lab ใหม่สร้างใน account ที่เลือก ส่วน lab เดิมยังใช้ account ที่สร้างมันเสมอ</div>
        </div>
      </div>
      <div class="card">
        ${!list && html`<div class="muted">${err || 'กำลังอ่าน ~/.aws…'}</div>`}
        ${list && list.accounts.length === 0 && html`<div class="muted">ยังไม่มีโปรไฟล์ AWS ในเครื่องนี้ — เพิ่มด้านล่าง</div>`}
        ${list && html`<div class="acct-list" role="list">
          ${list.accounts.map(a => {
            const isActive = a.name === (aws?.profile || list.active);
            const live = isActive && aws?.connected;
            return html`
            <div class="acct-row ${isActive ? 'active' : ''}" role="listitem" key=${a.name}>
              <span class="mark" aria-hidden="true"></span>
              <div style="min-width:0">
                <div class="nm">${a.name}
                  <span class="kind ${a.kind}">${a.kind === 'sso' ? 'SSO' : a.temporary ? 'คีย์ชั่วคราว' : 'Access key'}</span>
                  ${isActive && html`<span class="kind using">${live ? 'ใช้อยู่' : aws?.needs_login ? 'ใช้อยู่ · ต้อง sign in' : 'ใช้อยู่ · ใช้ไม่ได้'}</span>`}</div>
                <div class="ds">${[live ? aws.account : a.account_id, a.role, a.region].filter(Boolean).join(' · ') || '—'}</div>
                ${a.labs.length > 0 && html`<div class="help">lab ที่ใช้อยู่: ${a.labs.join(', ')}</div>`}
                ${isActive && !live && aws?.reason && !aws?.needs_login && html`<div class="help bad">${aws.reason}</div>`}
              </div>
              <div class="btn-row">
                ${a.kind === 'sso' && html`<button class="btn ghost small" disabled=${sso?.state === 'waiting'}
                  onClick=${() => startSso(a.name)}>Sign in</button>`}
                ${!isActive && html`<button class="btn primary small" disabled=${busy === a.name}
                  onClick=${() => use(a)}>${busy === a.name ? 'กำลังตรวจ…' : 'ใช้ account นี้'}</button>`}
                ${a.removable && html`<button class="btn tiny danger-text"
                  title="ลบ account นี้ — เลือก destroy lab และ sign out SSO ได้"
                  onClick=${() => setRemoving(a)}>ลบ</button>`}
              </div>
            </div>`;
          })}
        </div>`}
        <${SsoWait} st=${sso} />
        <${ErrBox} err=${list ? err : null} />
        ${aws?.connected && html`<div class="btnbar">
          <button class="btn primary" onClick=${() => { PREFILL = null; nav('/new'); }}>สร้าง lab ใน ${aws.profile || aws.account} →</button>
        </div>`}
      </div>
      <${AddAccount} key=${(list?.accounts || []).map(a => a.name).join(',')} onAdded=${reloadStatus} />
      ${removing && html`<${RetireAccountModal} account=${removing}
        onClose=${() => setRemoving(null)} onDone=${() => setRemoving(null)} />`}
      ${list && html`<${OrgDangerZone} accounts=${list.accounts} />`}
      <${OrphanScan} />
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
    let first = true;
    const load = () => api('/labs').then(d => {
      setLabs(d.labs); setLabsLoaded(true);
      // เข้ามาครั้งแรกแล้วยังไม่มี lab ที่รันอยู่ = มาเพื่อสร้างเครื่อง — พาไปหน้าสร้างเลย
      if (first && !d.labs.some(l => l.status !== 'destroyed') && ['', '/'].includes(location.hash.slice(1))) nav('/new');
      first = false;
    }).catch(() => {});
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
  if (route === '/new') screen = html`<${Wizard} aws=${aws} onAws=${setAws} />`;
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
