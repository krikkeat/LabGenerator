// labctl web UI — no-build SPA (preact + htm ผ่าน import map, ไม่ต้องมี Node)
import { h, render } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// ---------------------------------------------------------------------------
// api helpers
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    let msg = `${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* noop */ }
    throw new Error(msg);
  }
  return res.json();
}

// ราคาโดยประมาณ ap-southeast-1 on-demand (ตรงกับตารางใน README)
const HOURLY_USD = {
  't3.micro': 0.0132, 't3.small': 0.0264, 't3.medium': 0.0528,
  't3.large': 0.1056, 't3.xlarge': 0.2112,
};

const OS_LABELS = {
  'amazon-linux-2023': 'Amazon Linux 2023', 'amazon-linux-2': 'Amazon Linux 2',
  'ubuntu-22.04': 'Ubuntu 22.04', 'ubuntu-24.04': 'Ubuntu 24.04',
  'windows-2022': 'Windows Server 2022', 'windows-2019': 'Windows Server 2019',
};

const STATUS_TH = {
  active: 'Active', provisioning: 'Provisioning', queued: 'Queued',
  destroying: 'Destroying', destroyed: 'Destroyed', error: 'Error',
};

// ---------------------------------------------------------------------------
// routing (hash-based)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// shared bits
// ---------------------------------------------------------------------------

function Pill({ status }) {
  return html`<span class="pill ${status}"><span class="dot"></span>${STATUS_TH[status] || status}</span>`;
}

function ErrBox({ err }) {
  return err ? html`<div class="err">${err}</div>` : null;
}

// ---------------------------------------------------------------------------
// aws connection
// ---------------------------------------------------------------------------

function AwsChip({ aws }) {
  const cls = aws === null ? 'checking' : aws.connected ? 'on' : 'off';
  const label = aws === null ? 'กำลังเช็ค AWS…'
    : aws.connected ? `AWS · ${aws.account}` : 'ยังไม่เชื่อมต่อ AWS';
  return html`<button class="aws-chip ${cls}" onClick=${() => nav('/aws')} title=${aws?.arn || ''}>
    <span class="dot"></span>${label}</button>`;
}

function AwsBanner({ aws }) {
  if (!aws || aws.connected) return null;
  return html`
    <div class="callout" style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap">
      <span>ยังไม่ได้เชื่อมต่อ AWS — ต้องเชื่อมต่อก่อนจึงจะสร้าง lab ได้</span>
      <button class="btn primary small" onClick=${() => nav('/aws')}>เชื่อมต่อ AWS →</button>
    </div>`;
}

function AwsConnect({ aws, onAws }) {
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
      <div>
        <h1>เชื่อมต่อ AWS แล้ว</h1>
        <div class="card">
          <table style="margin-bottom:18px"><tbody>
            <tr><td class="muted" style="width:120px">Account</td><td>${aws.account}</td></tr>
            <tr><td class="muted">Identity</td><td style="word-break:break-all">${aws.arn || '-'}</td></tr>
            <tr><td class="muted">Region</td><td>${aws.region || '-'}</td></tr>
            ${aws.method && html`<tr><td class="muted">ที่มา</td>
              <td>${aws.method === 'env' ? 'ใส่ผ่านหน้านี้ (จำไว้เฉพาะ session นี้)'
                  : aws.method.startsWith('profile:') ? `โปรไฟล์ ${aws.method.slice(8)} ใน ~/.aws/credentials` : aws.method}</td></tr>`}
            ${aws.remembered && html`<tr><td class="muted">บันทึกแล้ว</td>
              <td>~/.aws/credentials (โปรไฟล์ labctl) — ครั้งหน้าไม่ต้องใส่ใหม่</td></tr>`}
          </tbody></table>
          <div class="btnbar">
            <button class="btn ghost" onClick=${() => nav('/')}>← หน้าแรก</button>
            <button class="btn primary" onClick=${() => nav('/new')}>สร้าง hands-on lab ได้เลย →</button>
          </div>
        </div>
      </div>`;
  }

  return html`
    <div>
      <h1>เชื่อมต่อ AWS</h1>
      <div class="card">
        <p style="margin-top:0; font-size:14px" class="muted">
          ใส่ Access Key ของบัญชี AWS ที่มีสิทธิ์ EC2 + VPC — ระบบจะตรวจกับ AWS ก่อน
          ผ่านแล้วถึงจะใช้สร้าง lab ได้ (key ไม่ถูกส่งไปที่อื่นนอกจาก AWS และไม่ลง log)
        </p>
        ${aws?.reason && !err && html`<div class="err">${aws.reason}</div>`}
        <${ErrBox} err=${err} />
        <form onSubmit=${submit}>
          <div class="field">
            <label>Access Key ID <span class="req">*</span></label>
            <input type="text" value=${form.access_key_id} onInput=${set('access_key_id')}
              placeholder="AKIA…" autocomplete="off" />
          </div>
          <div class="field">
            <label>Secret Access Key <span class="req">*</span></label>
            <input type="password" value=${form.secret_access_key} onInput=${set('secret_access_key')}
              autocomplete="off" />
          </div>
          <div class="row">
            <div class="field">
              <label>Session Token <span class="opt">optional — เฉพาะ credential ชั่วคราว (SSO/STS)</span></label>
              <input type="password" value=${form.session_token} onInput=${set('session_token')} autocomplete="off" />
            </div>
            <div class="field">
              <label>Default region</label>
              <input type="text" value=${form.region} onInput=${set('region')} />
            </div>
          </div>
          <div class="field">
            <label class="checkbox">
              <input type="checkbox" checked=${form.remember}
                onChange=${(e) => setForm({ ...form, remember: e.target.checked })} />
              จำไว้ในเครื่องนี้ (~/.aws/credentials โปรไฟล์ labctl) — ปิดโปรแกรมแล้วไม่ต้องใส่ใหม่
            </label>
          </div>
          <div class="btnbar">
            <button type="button" class="btn ghost" onClick=${() => nav('/')}>ยกเลิก</button>
            <button type="submit" class="btn primary" disabled=${busy || !form.access_key_id || !form.secret_access_key}>
              ${busy ? 'กำลังตรวจกับ AWS…' : 'เชื่อมต่อ'}
            </button>
          </div>
        </form>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// screen: lab list (home)
// ---------------------------------------------------------------------------

function Home({ aws }) {
  const [labs, setLabs] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    api('/labs').then(d => setLabs(d.labs)).catch(e => setErr(e.message));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  return html`
    <div>
      <div class="headrow" style="margin-bottom:18px">
        <h1 style="margin:0">Labs ของคุณ</h1>
        <button class="btn primary" onClick=${() => nav('/new')}>+ สร้าง lab ใหม่</button>
      </div>
      <${AwsBanner} aws=${aws} />
      <${ErrBox} err=${err} />
      <div class="card">
        ${labs === null ? html`<div class="empty">กำลังโหลด…</div>`
        : labs.length === 0 ? html`<div class="empty">ยังไม่มี lab — กด "สร้าง lab ใหม่" เพื่อเริ่ม</div>`
        : html`
          <div class="scrollx"><table>
            <thead><tr><th>Lab</th><th>Product</th><th>ผู้เรียน</th><th>สถานะ</th><th>หมดอายุ</th><th></th></tr></thead>
            <tbody>
              ${labs.map(l => html`
                <tr key=${l.lab_name}>
                  <td>${l.lab_name}</td>
                  <td>${l.product_name || html`<span class="muted">— (custom OS)</span>`}</td>
                  <td class="num">${l.users || '-'}</td>
                  <td><${Pill} status=${l.status} /></td>
                  <td class="num">${l.expire_date || '-'}</td>
                  <td style="text-align:right">
                    <button class="btn ghost small" onClick=${() => nav(`/lab/${l.lab_name}`)}>เปิด →</button>
                  </td>
                </tr>`)}
            </tbody>
          </table></div>`}
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// screen: wizard
// ---------------------------------------------------------------------------

const CUSTOM = '__custom__';

function Wizard({ aws }) {
  const [catalog, setCatalog] = useState(null);
  const [step, setStep] = useState(0);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  const [product, setProduct] = useState(null); // product id หรือ CUSTOM
  const [form, setForm] = useState({
    lab_name: '', region: 'ap-southeast-1', users: ['user01', 'user02', 'user03'],
    allowed_cidr: '', expire_date: '', os: 'ubuntu-24.04',
    instance_type: '', root_volume_gb: '', extra_open_ports: '',
  });
  const [params, setParams] = useState({});

  useEffect(() => { api('/products').then(setCatalog).catch(e => setErr(e.message)); }, []);

  if (!catalog) return html`<div class="card"><div class="empty">${err || 'กำลังโหลด product catalog…'}</div></div>`;

  const meta = catalog.products.find(p => p.id === product);
  const isCustom = product === CUSTOM;
  const paramSpecs = meta ? Object.entries(meta.params) : [];
  const requiredOk = paramSpecs.every(([k, spec]) => !spec.required || (params[k] || '').trim());

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const effOS = isCustom ? form.os : meta?.os;
  const effType = form.instance_type || meta?.instance_type || 't3.small';
  const hourly = HOURLY_USD[effType];
  const nUsers = form.users.length;

  const steps = ['เลือก product', 'ตั้งค่า lab', 'ข้อมูล product', 'ตรวจสอบ'];
  // custom lab ไม่มี step 3
  const stepSeq = isCustom ? [0, 1, 3] : [0, 1, 2, 3];
  const pos = stepSeq.indexOf(step);

  const canNext = {
    0: product !== null,
    1: /^[a-z0-9-]{1,32}$/.test(form.lab_name) && nUsers > 0,
    2: requiredOk,
  }[step] ?? true;

  async function submit() {
    setBusy(true); setErr(null);
    const body = {
      lab_name: form.lab_name.trim(),
      users: form.users,
      region: form.region || null,
      allowed_cidr: form.allowed_cidr || null,
      expire_date: form.expire_date || null,
    };
    if (isCustom) {
      body.os = form.os;
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
    } catch (e) {
      setErr(e.message); setBusy(false);
    }
  }

  return html`
    <div>
      <h1>สร้าง lab ใหม่ <span class="muted mono" style="font-size:13px">· ขั้นตอน ${pos + 1}/${stepSeq.length}</span></h1>
      <div class="card">
        <div class="stepper">
          ${stepSeq.map((s, i) => html`<div class="s ${i < pos ? 'done' : i === pos ? 'now' : ''}"></div>`)}
        </div>
        <${AwsBanner} aws=${aws} />
        <${ErrBox} err=${err} />

        ${step === 0 && html`
          <div class="step-title">Lab นี้สอนอะไร?</div>
          <div class="prod-grid">
            ${catalog.products.map(p => html`
              <button class="prod-card ${product === p.id ? 'selected' : ''}" onClick=${() => setProduct(p.id)}>
                <div class="pname">${p.product_name}</div>
                <div class="pdesc">${p.description}</div>
                <div class="pspec">
                  <span>OS ${p.os}</span>
                  <span>${p.instance_type} · ${p.root_volume_gb}GB</span>
                </div>
              </button>`)}
            <button class="prod-card ${product === CUSTOM ? 'selected' : ''}" onClick=${() => setProduct(CUSTOM)}>
              <div class="pname">Custom OS lab</div>
              <div class="pdesc">ไม่ติดตั้ง product agent — เลือก OS เอง (ใส่ bootstrap script ได้ผ่าน config CLI)</div>
              <div class="pspec"><span>${catalog.supported_os.length} OS ให้เลือก</span><span>กำหนดสเปคเองได้</span></div>
            </button>
          </div>`}

        ${step === 1 && html`
          <div class="step-title">ตั้งค่า lab</div>
          <div class="row">
            <div class="field">
              <label>lab_name <span class="req">*</span></label>
              <input type="text" value=${form.lab_name} onInput=${set('lab_name')} placeholder="เช่น trendai-training" />
              ${form.lab_name && !/^[a-z0-9-]{1,32}$/.test(form.lab_name)
                && html`<div class="help" style="color:var(--crit)">ใช้ได้เฉพาะ a-z, 0-9, - (ไม่เกิน 32 ตัว)</div>`}
            </div>
            <div class="field">
              <label>region</label>
              <input type="text" value=${form.region} onInput=${set('region')} />
            </div>
          </div>
          <${UserEditor} users=${form.users} onChange=${(users) => setForm({ ...form, users })} />
          <div class="row">
            <div class="field">
              <label>allowed_cidr</label>
              <input type="text" value=${form.allowed_cidr} onInput=${set('allowed_cidr')} placeholder="0.0.0.0/0" />
              <div class="help">จำกัดให้แคบที่สุด เช่น IP ของสถานที่อบรม — เลี่ยง 0.0.0.0/0 ถ้าทำได้</div>
            </div>
            <div class="field">
              <label>expire_date</label>
              <input type="date" value=${form.expire_date} onInput=${set('expire_date')} />
            </div>
          </div>
          ${isCustom && html`
            <div class="field">
              <label>os</label>
              <select value=${form.os} onChange=${set('os')}>
                ${catalog.supported_os.map(o => html`<option value=${o}>${OS_LABELS[o] || o}</option>`)}
              </select>
            </div>`}
          <details>
            <summary>ขั้นสูง (instance_type, root_volume_gb, extra_open_ports)</summary>
            <div class="row">
              <div class="field">
                <label>instance_type</label>
                <input type="text" value=${form.instance_type} onInput=${set('instance_type')}
                  placeholder=${meta?.instance_type || 't3.small'} />
              </div>
              <div class="field">
                <label>root_volume_gb</label>
                <input type="number" value=${form.root_volume_gb} onInput=${set('root_volume_gb')}
                  placeholder=${meta?.root_volume_gb || 30} />
              </div>
              <div class="field">
                <label>extra_open_ports</label>
                <input type="text" value=${form.extra_open_ports} onInput=${set('extra_open_ports')} placeholder="80, 8080" />
              </div>
            </div>
          </details>`}

        ${step === 2 && meta && html`
          <div class="step-title">${meta.product_name} — ข้อมูล tenant</div>
          <div class="help" style="margin-bottom:16px">
            ค่าเหล่านี้จะถูกเติมลงใน bootstrap script และ lab guide แทนที่ placeholder ใน template
          </div>
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
          <table style="margin-bottom:16px">
            <tbody>
              <tr><td class="muted" style="width:130px">Lab</td>
                  <td>${form.lab_name} ${meta ? `— ${meta.product_name}` : '(custom OS)'}</td></tr>
              <tr><td class="muted">เครื่อง</td>
                  <td>${nUsers} × ${OS_LABELS[effOS] || effOS}, ${effType}${(form.root_volume_gb || meta?.root_volume_gb) ? `, ${form.root_volume_gb || meta.root_volume_gb}GB` : ''}</td></tr>
              <tr><td class="muted">Region</td><td>${form.region}</td></tr>
              <tr><td class="muted">ผู้เรียน</td><td>${form.users.join(', ')}</td></tr>
              ${!isCustom && paramSpecs.filter(([k, s]) => s.required).map(([k]) => html`
                <tr><td class="muted">${k}</td><td style="word-break:break-all">${params[k]}</td></tr>`)}
              <tr><td class="muted">ค่าใช้จ่ายโดยประมาณ</td>
                  <td>${hourly
                    ? `~$${(hourly * nUsers).toFixed(3)}/ชม. รวม · ~$${(hourly * nUsers * 8).toFixed(2)} ต่อ session 8 ชม.`
                    : 'ไม่มีข้อมูลราคา instance นี้'} <span class="muted">(on-demand + EBS เล็กน้อย)</span></td></tr>
            </tbody>
          </table>
          <div class="callout">
            อย่าลืม <b>ลบ lab (Destroy)</b> เมื่อจบ training — VM, network และ key ทั้งหมดจะถูกลบ ไม่มีอะไรค้างคิดเงินต่อ
          </div>`}

        <div class="btnbar">
          ${pos > 0 && html`<button class="btn ghost" style="margin-right:auto"
            onClick=${() => setStep(stepSeq[pos - 1])}>← ย้อนกลับ</button>`}
          <button class="btn ghost" onClick=${() => nav('/')}>ยกเลิก</button>
          ${pos < stepSeq.length - 1
            ? html`<button class="btn primary" disabled=${!canNext}
                onClick=${() => setStep(stepSeq[pos + 1])}>ถัดไป →</button>`
            : html`<button class="btn primary" disabled=${busy || (aws && !aws.connected)}
                title=${aws && !aws.connected ? 'ต้องเชื่อมต่อ AWS ก่อน' : ''}
                onClick=${submit}>${busy ? 'กำลังส่ง…' : 'สร้าง lab'}</button>`}
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
            <button title="ลบ ${u}" onClick=${() => onChange(users.filter(x => x !== u))}>×</button>
          </span>`)}
        <input type="text" value=${draft} placeholder="เพิ่มชื่อ…"
          onInput=${(e) => setDraft(e.target.value)}
          onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          onBlur=${add} />
        <input type="number" value=${genN} placeholder="userNN × N" style="width:110px"
          onInput=${(e) => setGenN(e.target.value)}
          onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); generate(); } }} />
        <button class="btn ghost small" onClick=${generate}>สร้างรายชื่อ</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// screen: provisioning (job view)
// ---------------------------------------------------------------------------

const PHASES = {
  create: [
    ['init', 'เตรียม Terraform'],
    ['workspace', 'สร้าง workspace ของ lab'],
    ['apply', 'สร้าง VPC + VM + key pairs บน AWS'],
    ['export', 'สร้าง handout + zip'],
    ['done', 'เสร็จสิ้น'],
  ],
  refresh: [['refresh', 'ดึงสถานะล่าสุดจาก AWS'], ['export', 'สร้าง handout ใหม่'], ['done', 'เสร็จสิ้น']],
  destroy: [['destroy', 'ลบ resource ทั้งหมดบน AWS'], ['cleanup', 'ลบ key/handout ในเครื่อง'], ['done', 'เสร็จสิ้น']],
};

function JobView({ jobId, labName }) {
  const [job, setJob] = useState(null);
  const [lines, setLines] = useState([]);
  const [phase, setPhase] = useState(null);
  const [done, setDone] = useState(null);
  const logRef = useRef(null);

  useEffect(() => {
    api(`/jobs/${jobId}`).then(setJob).catch(() => {});
    const es = new EventSource(`/api/jobs/${jobId}/events`);
    es.addEventListener('log', (e) => setLines(ls => [...ls.slice(-500), JSON.parse(e.data)]));
    es.addEventListener('phase', (e) => setPhase(JSON.parse(e.data).phase));
    es.addEventListener('done', (e) => { setDone(JSON.parse(e.data)); es.close(); });
    es.addEventListener('error', () => {});
    return () => es.close();
  }, [jobId]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines]);

  const kind = job?.kind || 'create';
  const phases = PHASES[kind] || PHASES.create;
  const phaseIdx = phases.findIndex(([p]) => p === phase);

  return html`
    <div>
      <h1>${kind === 'destroy' ? 'กำลังลบ' : kind === 'refresh' ? 'กำลัง refresh' : 'กำลังสร้าง'} ${labName}</h1>
      <div class="card">
        <div class="checklist">
          ${phases.map(([p, label], i) => html`
            <div class="check ${done?.state === 'success' || i < phaseIdx ? 'done' : i === phaseIdx && !done ? 'now' : ''}" key=${p}>
              <span class="mark">${done?.state === 'success' || i < phaseIdx ? '✓' : i === phaseIdx && !done ? '…' : ''}</span>
              ${label}
            </div>`)}
        </div>

        ${done?.state === 'error' && html`<div class="err">งานล้มเหลว: ${done.error || 'ดูรายละเอียดใน log ด้านล่าง'}</div>`}

        <div class="logpane" ref=${logRef}>
          ${lines.length === 0 ? html`<span class="muted">รอ log จาก terraform…</span>`
            : lines.map((l, i) => html`<div key=${i} class=${/Creation complete|เสร็จสิ้น/.test(l) ? 'ok' : /Error|ล้มเหลว/.test(l) ? 'bad' : ''}>${l}</div>`)}
        </div>

        <div class="btnbar">
          ${done ? html`
            <button class="btn primary" onClick=${() => nav(kind === 'destroy' ? '/' : `/lab/${labName}`)}>
              ${kind === 'destroy' ? '← กลับหน้าแรก' : 'เปิด dashboard ของ lab →'}
            </button>`
          : html`<span class="meta">ปิดหน้านี้ได้ — งานยังรันต่อเบื้องหลัง แล้วกลับมาดูจากหน้าแรกได้เสมอ</span>`}
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// screen: lab dashboard
// ---------------------------------------------------------------------------

function Dashboard({ labName }) {
  const [lab, setLab] = useState(null);
  const [err, setErr] = useState(null);
  const [revealed, setRevealed] = useState({});
  const [confirmText, setConfirmText] = useState('');
  const dlgRef = useRef(null);

  const load = useCallback(() => {
    api(`/labs/${labName}`).then(setLab).catch(e => setErr(e.message));
  }, [labName]);
  useEffect(load, [load]);

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
      dlgRef.current?.close();
      nav(`/job/${r.job_id}?lab=${labName}`);
    } catch (e) { setErr(e.message); }
  }

  if (!lab) return html`<div class="card"><div class="empty">${err || 'กำลังโหลด…'}</div></div>`;

  const busyJob = lab.job && ['queued', 'running'].includes(lab.job.state);

  return html`
    <div>
      <div class="headrow" style="margin-bottom:16px">
        <div>
          <h1 style="margin:0 0 8px">${lab.title || lab.product_name || lab.lab_name}
            <span class="muted mono" style="font-size:14px"> — ${lab.lab_name}</span></h1>
          <${Pill} status=${lab.status} />
          <span class="meta" style="margin-left:10px">
            ${lab.os || ''} · ${lab.region}${lab.expire_date ? ` · หมดอายุ ${lab.expire_date}` : ''}
          </span>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap">
          ${busyJob && html`<button class="btn ghost small"
            onClick=${() => nav(`/job/${lab.job.id}?lab=${labName}`)}>ดูงานที่กำลังรัน →</button>`}
          <button class="btn ghost small" onClick=${doRefresh} disabled=${busyJob}>↻ Refresh</button>
          <a class="btn ghost small" href="/api/labs/${labName}/handouts/export" download>⤓ Export ทั้งหมด</a>
          <button class="btn danger small" disabled=${busyJob}
            onClick=${() => { setConfirmText(''); dlgRef.current?.showModal(); }}>Destroy…</button>
        </div>
      </div>
      <${ErrBox} err=${err} />

      <div class="card">
        ${lab.machines.length === 0
          ? html`<div class="empty">
              ${lab.status === 'destroyed' ? 'lab นี้ถูกลบแล้ว'
               : lab.status === 'queued' || lab.status === 'provisioning' ? 'กำลังสร้างเครื่อง — ดูความคืบหน้าจากปุ่มด้านบน'
               : 'ยังอ่านข้อมูลเครื่องไม่ได้ — ลอง Refresh'}
            </div>`
          : html`
          <div class="scrollx"><table>
            <thead><tr><th>User</th><th>Public IP</th><th>เข้าผ่าน</th><th>Username</th><th>Password</th><th>Handout</th></tr></thead>
            <tbody>
              ${lab.machines.map(m => html`
                <tr key=${m.user}>
                  <td>${m.user}</td>
                  <td class="num">${m.public_ip || '—'}</td>
                  <td>${m.is_windows ? 'RDP' : 'SSH'}</td>
                  <td>${m.username}</td>
                  <td>
                    ${!m.is_windows ? html`<span class="muted">key ในไฟล์ handout</span>`
                    : m.password === null ? html`<span class="muted">ยังไม่พร้อม (~4-5 นาทีหลังบูต — กด Refresh)</span>`
                    : revealed[m.user]
                      ? html`<span class="mono">${m.password}</span>
                          <button class="btn ghost small" style="margin-left:6px"
                            onClick=${() => setRevealed({ ...revealed, [m.user]: false })}>ซ่อน</button>`
                      : html`<span class="masked">••••••••</span>
                          <button class="btn ghost small" style="margin-left:6px"
                            onClick=${() => setRevealed({ ...revealed, [m.user]: true })}>แสดง</button>`}
                  </td>
                  <td>
                    ${m.handout_ready
                      ? html`<a class="btn ghost small" href="/api/labs/${labName}/handouts/${m.user}.zip" download>⤓ .zip</a>`
                      : html`<span class="muted">—</span>`}
                  </td>
                </tr>`)}
            </tbody>
          </table></div>`}
      </div>

      <dialog ref=${dlgRef}>
        <h2 style="margin-top:0">ลบ lab ทั้งหมด?</h2>
        <p style="font-size:14px">VM ทุกเครื่อง, network, key pair และ handout ของ
          <b class="mono">${labName}</b> จะถูกลบถาวร</p>
        <div class="field">
          <label>พิมพ์ชื่อ lab เพื่อยืนยัน</label>
          <input type="text" value=${confirmText} placeholder=${labName}
            onInput=${(e) => setConfirmText(e.target.value)} />
        </div>
        <div class="btnbar" style="border:none; padding-top:6px">
          <button class="btn ghost" onClick=${() => dlgRef.current?.close()}>ยกเลิก</button>
          <button class="btn danger" disabled=${confirmText !== labName} onClick=${doDestroy}>ลบทั้งหมด</button>
        </div>
      </dialog>
    </div>`;
}

// ---------------------------------------------------------------------------
// app shell
// ---------------------------------------------------------------------------

function App() {
  const route = useRoute();
  const [aws, setAws] = useState(null); // null = กำลังเช็ค
  useEffect(() => {
    api('/aws/status')
      .then(setAws)
      .catch(() => setAws({ connected: false, reason: 'เช็คสถานะ AWS ไม่ได้' }));
  }, []);

  let screen;
  const jobMatch = route.match(/^\/job\/([^?]+)\?lab=(.+)$/);
  const labMatch = route.match(/^\/lab\/(.+)$/);
  if (route === '/new') screen = html`<${Wizard} aws=${aws} />`;
  else if (route === '/aws') screen = html`<${AwsConnect} aws=${aws} onAws=${setAws} />`;
  else if (jobMatch) screen = html`<${JobView} jobId=${jobMatch[1]} labName=${decodeURIComponent(jobMatch[2])} />`;
  else if (labMatch) screen = html`<${Dashboard} labName=${decodeURIComponent(labMatch[1])} />`;
  else screen = html`<${Home} aws=${aws} />`;

  return html`
    <div class="shell">
      <div class="topbar">
        <div class="brand" onClick=${() => nav('/')}>labctl<span>·lab generator</span></div>
        <${AwsChip} aws=${aws} />
      </div>
      ${screen}
    </div>`;
}

render(html`<${App} />`, document.getElementById('root'));
