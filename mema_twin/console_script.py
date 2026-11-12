r"""本地 console /twin 页的前端脚本。

自 mema 仓 console_script.py 迁入（拆分方案 §3.6）：
- SHARED_SCRIPT 裁剪为最小子集：TWIN_BOOT 只依赖 loadLang/state/t/esc 与
  markdown 渲染（inlineMd/renderMd）——console 主脚本整块（路由/侧栏/
  知识库/反馈模态）不迁；localStorage 键沿用 'memoryArbiterConsoleLang'，
  与 mema console 共享语言偏好；
- TWIN_BOOT 按 v0.5.0 架构简化改造，其余形态/文案原样保留（b67b857 改向
  后的「版本卡→类型详情」两级 + 可用性五批打磨）：
  * 无 /api/twin/locate：引导卡数据改由 overview 响应判定（多租户拒绝/
    无账本/库不可读三态 + import 迁移横幅）；
  * mema 联查退役：证据抽屉不再 fetch /api/memories/{id}，直接渲染行内
    自带的 content/tags/source_ref（twin_evidence 正文自持）；
  * 「在记忆页打开」跳转链接随联查退役。

XSS 硬纪律（mema 方案 §5 R2 P1-2，tests/test_console_twin.py 正则钉）：
进 innerHTML 模板串的动态值一律 esc()（onclick 数值参数一律 Number() 归一
——esc 不转义 ( ) ; /，挡不住 JS 上下文注入）；twin 透传的 note/reason 只
进 alert()/textContent，绝不拼 innerHTML。
注意：script 内含正则反斜杠，Python 串内一律双写（\\* → JS 的 \*）。
"""
from .console_i18n import I18N_JS

SHARED_SCRIPT = """
const I18N = {
""" + I18N_JS + """
};
const state = { lang: loadLang(), twin: null };
function loadLang(){ const saved=localStorage.getItem('memoryArbiterConsoleLang'); if(saved==='zh'||saved==='en') return saved; return (navigator.language||'').toLowerCase().startsWith('zh')?'zh':'en'; }
function t(k){ return (I18N[state.lang]&&I18N[state.lang][k]) || I18N.en[k] || k; }
function esc(v){ return String(v ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
function inlineMd(s){ s=s.replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>'); s=s.replace(/(^|[^*])\\*([^*\\n]+)\\*(?!\\*)/g,'$1<em>$2</em>'); s=s.replace(/`([^`\\n]+)`/g,'<code>$1</code>'); return s; }
function renderMd(text){
  const lines=esc(String(text||'')).split('\\n');
  const out=[]; let i=0; let listType=null; let para=[];
  const flushPara=()=>{ if(para.length){ out.push('<p>'+inlineMd(para.join(' '))+'</p>'); para=[]; } };
  const closeList=()=>{ if(listType){ out.push(listType==='ul'?'</ul>':'</ol>'); listType=null; } };
  while(i<lines.length){
    const line=lines[i];
    if(/^```/.test(line)){ flushPara(); closeList(); const code=[]; i++; while(i<lines.length && !/^```/.test(lines[i])){ code.push(lines[i]); i++; } i++; out.push('<pre><code>'+code.join('\\n')+'</code></pre>'); continue; }
    if(/^\\s*(---+|\\*\\*\\*+)\\s*$/.test(line)){ flushPara(); closeList(); out.push('<hr>'); i++; continue; }
    const h=line.match(/^(#{1,4})\\s+(.*)$/);
    if(h){ flushPara(); closeList(); const n=h[1].length; out.push('<h'+n+'>'+inlineMd(h[2])+'</h'+n+'>'); i++; continue; }
    const ul=line.match(/^\\s*[-*]\\s+(.*)$/);
    const ol=line.match(/^\\s*\\d+[.)]\\s+(.*)$/);
    if(ul||ol){ flushPara(); const type=ul?'ul':'ol'; if(listType!==type){ closeList(); out.push(type==='ul'?'<ul>':'<ol>'); listType=type; } out.push('<li>'+inlineMd((ul||ol)[1])+'</li>'); i++; continue; }
    const bq=line.match(/^\\s*&gt;\\s?(.*)$/);
    if(bq){ flushPara(); closeList(); out.push('<blockquote>'+inlineMd(bq[1])+'</blockquote>'); i++; continue; }
    if(/^\\s*\\|.*\\|\\s*$/.test(line) && i+1<lines.length && /^\\s*\\|[\\s:|\\-]+\\|\\s*$/.test(lines[i+1])){
      flushPara(); closeList();
      const cells=r=>r.trim().replace(/^\\||\\|$/g,'').split('|').map(c=>c.trim());
      const head=cells(line); i+=2; const body=[];
      while(i<lines.length && /^\\s*\\|.*\\|\\s*$/.test(lines[i])){ body.push(cells(lines[i])); i++; }
      out.push('<table><thead><tr>'+head.map(c=>'<th>'+inlineMd(c)+'</th>').join('')+'</tr></thead><tbody>'+body.map(r=>'<tr>'+r.map(c=>'<td>'+inlineMd(c)+'</td>').join('')+'</tr>').join('')+'</tbody></table>'); continue;
    }
    if(!line.trim()){ flushPara();
      /* 松散列表：空行后仍是同类列表项时不断开——否则每题各成一个单条
         <ol>，HTML 序号一律从 1 重排 */
      let j=i+1; while(j<lines.length && !lines[j].trim()){ j++; }
      if(j<lines.length && listType){
        const nxtUl=/^\\s*[-*]\\s+/.test(lines[j]);
        const nxtOl=/^\\s*\\d+[.)]\\s+/.test(lines[j]);
        if((listType==='ul'&&nxtUl)||(listType==='ol'&&nxtOl)){ i++; continue; }
      }
      closeList(); i++; continue; }
    para.push(line.trim()); i++;
  }
  flushPara(); closeList(); return out.join('\\n');
}
"""

TWIN_BOOT = """
/* ---- twin 独立页面入口（/twin，自有账本 twin.sqlite3）----
   v0.5.0：无 locate 探测（写面进程内直调 + 库路径 db.db_path() 单点），
   引导卡三态由 overview 响应判定；mema 联查退役，抽屉直读本地正文。 */
function twinStateInit(){
  if(!state.twin){
    state.twin = { overview:null, overviewExpanded:false, evidence:null, detail:null, persona:null, evidenceFilters:{status:'', work_type:''}, evidencePage:{offset:0, limit:50}, pending:null, pendingStatus:'pending', types:{}, audit:null, tab:'evidence', busy:false, resolveOpen:new Set(), evOpen:null };
  }
  return state.twin;
}
async function twinPost(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-Console-Request':'1'}, body: JSON.stringify(body)});
  const j = await r.json();
  if(!r.ok) throw new Error(j && j.reason ? (String(j.error)+': '+String(j.reason)) : ((j && j.error) || r.statusText));
  return j;
}
function twinSetLang(lang){ state.lang=lang; localStorage.setItem('memoryArbiterConsoleLang', lang); twinRender(); }
function twinBindLang(){
  const zh=document.getElementById('langZh'), en=document.getElementById('langEn');
  if(zh) zh.onclick=()=>twinSetLang('zh');
  if(en) en.onclick=()=>twinSetLang('en');
  document.documentElement.lang = state.lang==='zh' ? 'zh-CN' : 'en';
  document.title = state.lang==='zh' ? 'Twin · 分身画像 · mema-twin' : 'Twin · mema-twin';
  document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=t(el.dataset.i18n));
}
async function twinBoot(){
  const ts=twinStateInit();
  twinLoadOverview();
  if(ts.tab==='evidence' && !ts.evidence) twinLoadEvidence();
  /* 类型映射预载：overview 版本卡/警示键把内部 code 翻成人话 label；aud-*
     前缀是受众伪类型（types 表存去前缀 code），其余查 work_type kind。
     到达后重渲一次总览。 */
  Promise.all([twinEnsureTypes('work_type'), twinEnsureTypes('audience')])
    .then(()=>{ if(ts.overview) twinRender(); }).catch(()=>{});
}
/* 引导卡（无 locate 的三态 + import 迁移横幅）：数据全部来自 overview 响应。 */
function twinGuide(ts){
  const ov=ts.overview; let out='';
  if(!ov) return out;
  if(ov.ok===false){
    const err=String(ov.error||'');
    if(err==='multi_tenant_unsupported'){
      out+=`<div class="callout" style="margin-bottom:12px"><div class="callout-icon">⛔</div><div><strong>${esc(t('twinMultiTenant'))}</strong><p>${esc(t('twinMultiTenantHint'))}</p></div></div>`;
    } else if(err==='twin_db_not_found'){
      out+=`<div class="callout" style="margin-bottom:12px"><div class="callout-icon">⏳</div><div><strong>${esc(t('twinNoDb'))}</strong><p>${esc(t('twinNoDbHint'))}</p></div></div>`;
    } else {
      out+=`<div class="callout" style="margin-bottom:12px"><div class="callout-icon">!</div><div><strong>${esc(t('twinLoadError'))}</strong><p>${esc(String(ov.reason||ov.error||''))}</p></div></div>`;
    }
    return out;
  }
  /* 存量指针迁移横幅（判据=import_done 标记不在 且有空 content 在世行） */
  const imp=ov.import;
  if(imp && imp.pending && Number(imp.empty_content||0)>0){
    const hint=t('twinImportHint').replace('{n}', String(imp.empty_content));
    out+=`<div class="callout" style="margin-bottom:12px"><div class="callout-icon">⏳</div><div><strong>${esc(t('twinImportPending'))}</strong><p>${esc(hint)}</p></div></div>`;
  }
  return out;
}
/* ISO 时间 → 本地短格式（全格式 ISO 时间戳对人是噪音） */
function twinFmtTs(iso){
  const s=String(iso||''); if(!s) return '—';
  const d=new Date(s);
  if(isNaN(d.getTime())) return s.slice(0,19).replace('T',' ');
  const p=n=>String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
}
/* 内部 code → 人话 label：aud-<x> 查 audience 伪类型（types 表存去前缀 code），
   其余查 work_type；查不到回落 code 本身（绝不空）。 */
function twinTypeLabel(code){
  const ts=twinStateInit(); const c=String(code||'');
  const list = c.startsWith('aud-') ? (ts.types.audience||[]) : (ts.types.work_type||[]);
  const key = c.startsWith('aud-') ? c.slice(4) : c;
  const hit=list.find(tp=>String(tp.code)===key);
  if(!hit) return c || '—';
  const label=state.lang==='zh'?(hit.label_zh||hit.label_en):(hit.label_en||hit.label_zh);
  return label || c;
}
function twinOverviewStrip(ts){
  const ov=ts.overview;
  if(!ov) return `<article class="card"><div class="card-body"><div class="empty">${esc(t('kbLoading'))}…</div></div></article>`;
  if(ov.ok===false) return `<article class="card"><div class="card-body"><div class="empty">${esc(String(ov.error||t('twinLoadError')))}</div></div></article>`;
  /* 卡片显示人话 label（code 降为副标签）、去 chars 技术指标、按证据数降序、
     >6 折叠（复用共享 expandWorkspaces/collapseWorkspaces {n} 键）。 */
  const versions=(ov.versions||[]).slice().sort((a,b)=>(b.evidence_count||0)-(a.evidence_count||0));
  const expanded=!!ts.overviewExpanded;
  const shown = expanded || versions.length<=6 ? versions : versions.slice(0,6);
  const cards=shown.map(v=>{
    const label=twinTypeLabel(v.work_type);
    const sub = label!==String(v.work_type||'') ? String(v.work_type||'') : '';
    return `<button class="twin-vc" type="button" title="${esc(t('twinCardHint'))}" onclick="twinOpenPersona('${esc(String(v.work_type||''))}')">
      <div class="twin-vc-top"><strong title="${esc(String(v.work_type||''))}">${esc(label)}</strong><span class="twin-chip">v${esc(String(v.version??'—'))}</span></div>
      <small>${esc(String(v.evidence_count??0))} ${esc(t('twinEvidenceTab'))}${v.activated_at?' · '+esc(twinFmtTs(v.activated_at)):''}</small>
      ${sub?`<code class="twin-vc-code">${esc(sub)}</code>`:''}
      <span class="twin-vc-open">${esc(t('twinCardHint'))} →</span>
    </button>`;
  }).join('');
  const more = versions.length>6
    ? `<button class="minibtn twin-vc-more" type="button" onclick="state.twin.overviewExpanded=!state.twin.overviewExpanded;twinRender();">${expanded?esc(t('collapseWorkspaces')):esc(t('expandWorkspaces').replace('{n}',versions.length))}</button>`
    : '';
  /* stale 键人话化：persona_stale:<code> → <label> 待重编；nightly_reject:<code> → 夜间编译被拒 */
  const zh=state.lang==='zh';
  const stale=(ov.stale_keys||[]).map(k=>{
    const s=String(k); let label=s;
    if(s.startsWith('persona_stale:')) label=twinTypeLabel(s.slice('persona_stale:'.length))+(zh?' 待重编':' · stale');
    else if(s.startsWith('nightly_reject:')) label=(zh?'夜间编译被拒：':'nightly reject: ')+twinTypeLabel(s.slice('nightly_reject:'.length));
    return `<span class="twin-chip warn" title="${esc(s)}">${esc(label)}</span>`;
  }).join('');
  const audit=ov.audit;
  const degraded=(ov.degraded||[]);
  return `<article class="card"><div class="card-head"><div><h3>${esc(t('twinOverview'))}</h3><p class="desc">${esc(t('twinActiveVersions'))}</p></div><span class="pill" title="${esc(t('twinPendingCount'))}">${esc(t('twinPendingTab'))}: ${esc(String(ov.pending_pending??0))}</span></div><div class="card-body">
    ${cards?`<div class="twin-vc-row">${cards}</div>${more}`:`<div class="empty">—</div>`}
    ${stale?`<div class="twin-stale"><span class="mini">${esc(t('twinStaleWarn'))}:</span> ${stale}</div>`:''}
    ${audit?`<div class="mini" style="opacity:.75">${esc(t('twinAuditTab'))}: ${esc(String(audit.ok_n??0))}/${esc(String(audit.total??0))} ${esc(t('twinAuditOk'))}${audit.last_ts?' · '+esc(twinFmtTs(audit.last_ts)):''}</div>`:''}
    ${degraded.length?`<div class="mini" style="opacity:.7">${esc(t('twinDegraded'))} (${esc(degraded.map(String).join(', '))})</div>`:''}
  </div></article>`;
}
/* 目录卡点开 → 类型详情视图——prompt_md（该类型全部偏好的人话编译产物，
   即「做这类工作时分身知道什么」）+ 该类型证据列表。 */
function twinOpenPersona(wt){
  const ts=twinStateInit();
  ts.detail=String(wt||''); ts.persona=null; ts.evidence=null;
  ts.evidenceFilters={status:'', work_type:ts.detail};
  ts.evidencePage={offset:0, limit:100};
  twinRender();
  twinPost('/api/twin/persona', {work_type:ts.detail})
    .then(r=>{ ts.persona=r; twinRender(); })
    .catch(e=>{ ts.persona={ok:false, error:(e&&e.message)?String(e.message):'error'}; twinRender(); });
  twinPost('/api/twin/evidence', {work_type:ts.detail, limit:100})
    .then(r=>{ ts.evidence=r; twinRender(); })
    .catch(e=>{ ts.evidence={ok:false, error:(e&&e.message)?String(e.message):'error'}; twinRender(); });
}
function twinClosePersona(){
  const ts=twinStateInit();
  ts.detail=null; ts.persona=null;
  twinRender();
}
function twinPersonaView(ts){
  const p=ts.persona;
  const label=twinTypeLabel(ts.detail);
  const head=`<div class="twin-persona-head">
      <button class="minibtn" type="button" onclick="twinClosePersona()">← ${esc(t('twinBackToIndex'))}</button>
      <strong title="${esc(String(ts.detail))}">${esc(label)}</strong>
      <span class="twin-chip">v${esc(String((p&&p.version)??'—'))}</span>
      ${p&&p.activated_at?`<span class="mini">${esc(twinFmtTs(p.activated_at))}</span>`:''}
      <span class="spacer"></span>
      ${p?`<span class="mini">${esc(String(p.evidence_count??0))} ${esc(t('twinEvidenceTab'))}</span>`:''}
    </div>`;
  if(!p) return `${head}<div class="empty">${esc(t('kbLoading'))}…</div>`;
  if(p.ok===false) return `${head}<div class="empty">${esc(String(p.error||t('twinLoadError')))}</div>`;
  return `${head}
    <h3 class="twin-persona-title">${esc(t('twinPersonaTitle'))}</h3>
    <div class="twin-persona-md md-body">${renderMd(String(p.prompt_md||''))}</div>
    <h4 class="twin-persona-sub">${esc(t('twinPersonaEvidence'))}</h4>
    ${twinEvidenceTabBody(ts)}`;
}
function twinRender(){
  const ts=twinStateInit();
  const root=document.getElementById('twinRoot'); if(!root) return;
  twinBindLang();
  const ov=(ts.overview&&ts.overview.ok)?ts.overview:null;
  /* hero 页头 */
  const hero=`<div class="twin-hero">
    <div class="twin-hero-mark">迷</div>
    <div class="twin-hero-text"><h2>${esc(t('twinTitle'))}</h2><p>${esc(t('twinDesc'))}</p></div>
    <span class="spacer"></span>
    ${ov?`<span class="pill" title="${esc(t('twinPendingCount'))}">${esc(t('twinPendingTab'))} · ${esc(String(ov.pending_pending??0))}</span>`:''}
  </div>`;
  if(ts.detail){
    /* 详情视图：证据列表按该类型过滤（evidenceFilters 同步，保证
       twinEvidenceTabBody 渲染的是本类型行） */
    ts.evidenceFilters={status:'', work_type:ts.detail};
    root.innerHTML=`${hero}${twinPersonaView(ts)}`;
    return;
  }
  root.innerHTML=`${hero}${twinGuide(ts)}
    ${twinOverviewStrip(ts)}
    <div class="twin-tabs">
      <button class="twin-tab ${ts.tab==='evidence'?'on':''}" type="button" onclick="twinSwitchTab('evidence')">${esc(t('twinEvidenceTab'))}</button>
      <button class="twin-tab ${ts.tab==='pending'?'on':''}" type="button" onclick="twinSwitchTab('pending')">${esc(t('twinPendingTab'))}${ov&&ov.pending_pending?` <span class="id-badge">${esc(String(ov.pending_pending))}</span>`:''}</button>
      <button class="twin-tab ${ts.tab==='audit'?'on':''}" type="button" onclick="twinSwitchTab('audit')">${esc(t('twinAuditTab'))}</button>
    </div>
    <div id="twinTabBody">${twinTabBody(ts)}</div>`;
}
function twinSwitchTab(tab){
  const ts=twinStateInit(); ts.tab=tab; twinRender();
  if(tab==='evidence'&&!ts.evidence) twinLoadEvidence();
  else if(tab==='pending'&&!ts.pending) twinLoadPending();
  else if(tab==='audit'&&!ts.audit) twinLoadAudit();
}
/* tab 名人话化 + 每个 tab 顶部一句话说明；票据状态枚举 → 人话标签（值不变）。 */
function twinKindLabel(k){
  const zh=state.lang==='zh';
  const map={work_type:zh?'工作类型':'work type',audience:zh?'受众':'audience',purpose:zh?'用途':'purpose'};
  return map[String(k||'')]||String(k||'—');
}
function twinPendStatusLabel(s){
  const map={pending:'twinPendPending', mapped:'twinPendMapped', canonicalized:'twinPendCanonicalized', rejected:'twinPendRejected'};
  const k=map[String(s||'')];
  return k ? t(k) : String(s||'—');
}
function twinTabHint(key){
  return `<p class="twin-hint">${esc(t(key))}</p>`;
}
function twinTabBody(ts){
  if(ts.tab==='pending') return twinTabHint('twinPendingHint')+twinPendingTab(ts);
  if(ts.tab==='audit') return twinTabHint('twinAuditHint')+twinAuditTab(ts);
  return twinTabHint('twinEvidenceHint')+twinEvidenceTabBody(ts);
}
/* ---- 总览加载（twinBoot/void/resolve 三处调用，漏定义即页面卡 Loading、
   写操作成功后弹 ReferenceError——被调用函数必须有定义，静态断言兜这层）---- */
async function twinLoadOverview(){
  const ts=twinStateInit();
  try{ ts.overview=await twinPost('/api/twin/overview', {}); }
  catch(e){ ts.overview={ok:false, error:(e&&e.message)?String(e.message):'error'}; }
  twinRender();
}
/* ---- 证据 tab ---- */
async function twinLoadEvidence(){
  const ts=twinStateInit(); const f=ts.evidenceFilters, p=ts.evidencePage;
  try{ ts.evidence=await twinPost('/api/twin/evidence', {status:f.status||undefined, work_type:f.work_type||undefined, limit:p.limit, offset:p.offset}); }
  catch(e){ ts.evidence={ok:false, error:(e&&e.message)?String(e.message):'error'}; }
  twinRender();
}
function twinSetEvFilter(field, value){
  const ts=twinStateInit(); ts.evidenceFilters[field]=value; ts.evidencePage.offset=0; ts.evidence=null; twinLoadEvidence();
}
function twinEvidencePage(delta){
  const ts=twinStateInit(); const p=ts.evidencePage;
  p.offset=Math.max(0, p.offset+delta*p.limit); ts.evidence=null; twinLoadEvidence();
}
function twinEvidenceTabBody(ts){
  const ev=ts.evidence;
  if(!ev) return `<div class="empty">${esc(t('kbLoading'))}…</div>`;
  if(ev.ok===false) return `<div class="empty">${esc(String(ev.error||t('twinLoadError')))}</div>`;
  const f=ts.evidenceFilters, p=ts.evidencePage, total=Number(ev.total||0);
  const inDetail=!!ts.detail;
  const statusOpts=['','uncompiled','compiled','void'];
  const filterBar=inDetail?'':`<div class="twin-filterbar">
    <label>${esc(t('twinStatus'))} <select onchange="twinSetEvFilter('status', this.value)">${statusOpts.map(s=>`<option value="${s}"${(f.status||'')===s?' selected':''}>${s?esc(s):esc(t('twinFilterAll'))}</option>`).join('')}</select></label>
    <label>${esc(t('twinWorkType'))} <input value="${esc(String(f.work_type||''))}" onchange="twinSetEvFilter('work_type', this.value.trim())"></label>
    <span class="spacer"></span>
    <span class="mini">${esc(t('total'))} ${esc(String(total))}</span>
  </div>`;
  const rows=(ev.items||[]).map(it=>{
    /* 抽屉开关挂在证据行 id 上（页内单开；正文/tags/source_ref 行内自带，
       无需懒加载——v0.5.0 正文自持）。 */
    const eid=Number(it.id);
    const drawerOpen=ts.evOpen===eid;
    const voidable = it.status!=='void';
    const rowHtml=`<tr class="${it.status==='void'?'twin-void-row':''}${drawerOpen?' twin-open-row':''}">
      <td>#${esc(String(it.id))}</td>
      <td><button class="twin-memlink" type="button" onclick="twinToggleEvDrawer(${Number(it.id)})" title="${esc(t('twinViewContent'))}">#${esc(String(it.memory_id??'—'))}</button></td>
      <td><span class="twin-chip" title="${esc(String(it.work_type||''))}">${esc(twinTypeLabel(it.work_type_raw||it.work_type))}</span></td>
      <td class="twin-subject">${esc(String(it.subject||'—'))}${(it.audience_raw||it.purpose_raw)?`<small class="twin-sub">${esc(String([it.audience_raw,it.purpose_raw].filter(Boolean).join(' · ')))}</small>`:''}</td>
      <td><span class="twin-badge s-${esc(String(it.status||''))}">${esc(String(it.status||'—'))}</span></td>
      <td>${(it.compiled_version!==null&&it.compiled_version!==undefined)?'v'+esc(String(it.compiled_version)):'—'}</td>
      <td class="twin-ts">${esc(twinFmtTs(it.created_at))}</td>
      <td>${voidable?`<button class="minibtn twin-void-btn" type="button" ${ts.busy?'disabled':''} onclick="twinVoidEvidence(${Number(it.memory_id)})">${esc(t('twinVoid'))}</button>`:''}</td>
    </tr>`;
    return drawerOpen ? rowHtml+twinEvDrawerRow(it) : rowHtml;
  }).join('');
  const pager=inDetail?'':`<div class="twin-pager">
    <button class="minibtn" type="button" ${p.offset<=0?'disabled':''} onclick="twinEvidencePage(-1)">${esc(t('prev'))}</button>
    <span class="mini">${esc(String(total?Math.min(p.offset+1, total):0))}-${esc(String(Math.min(p.offset+p.limit, total)))} / ${esc(String(total))}</span>
    <button class="minibtn" type="button" ${(p.offset+p.limit>=total)?'disabled':''} onclick="twinEvidencePage(1)">${esc(t('next'))}</button>
  </div>`;
  return `${filterBar}<div class="twin-table-wrap"><table class="twin-table"><thead><tr>
      <th>id</th><th>memory</th><th>${esc(t('twinWorkType'))}</th><th>${esc(t('twinSubject'))}</th><th>${esc(t('twinStatus'))}</th><th>${esc(t('twinCompiledVersion'))}</th><th>${esc(t('twinCreatedAt'))}</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table></div>${pager}`;
}
/* ---- 证据正文页内抽屉（v0.5.0：正文自持本库，行内数据直渲，无懒加载；
   「在记忆页打开」跳转随 mema 联查退役）----
   抽屉内容包 .twin-drawer-inner（width:0/min-width:100%），长 code/pre 不
   参与表格固有宽度计算——否则点开抽屉整表明显变宽。 */
function twinToggleEvDrawer(eid){
  const ts=twinStateInit();
  ts.evOpen = ts.evOpen===Number(eid) ? null : Number(eid);
  twinRender();
}
function twinEvDrawerRow(it){
  let tags=[];
  try{ const parsed=JSON.parse(String(it.tags||'[]')); if(Array.isArray(parsed)) tags=parsed.map(v=>String(v)); }catch(e){}
  const tagChips=tags.length?tags.map(x=>`<span class="twin-chip">${esc(x)}</span>`).join(''):'';
  const ref=String(it.source_ref||'');
  const body=`<div class="twin-mem-head">
      <strong>${esc(String(it.subject||'—'))}</strong>
      <span class="twin-badge s-${esc(String(it.status||'active'))}">${esc(String(it.status||'—'))}</span>
      <span class="mini">#${esc(String(it.memory_id??'—'))}</span>
      ${tagChips?`<span>${tagChips}</span>`:''}
      ${ref?`<span class="mini" title="source_ref">${esc(ref)}</span>`:''}
    </div>
    <div class="twin-mem-content md-body">${String(it.content||'').trim()?renderMd(String(it.content||'')):'—'}</div>`;
  return `<tr class="twin-drawer-row"><td colspan="8"><div class="twin-drawer-inner">${body}</div></td></tr>`;
}
async function twinVoidEvidence(memoryId){
  const ts=twinStateInit(); if(ts.busy) return;
  if(!confirm(t('twinVoidConfirm'))) return;
  ts.busy=true; twinRender();
  try{
    const r=await twinPost('/api/twin/void', {memory_id:Number(memoryId)});
    ts.busy=false;
    // twin 返回的 note（persona_stale 提示）对用户有价值，alert 展示。
    alert(r&&r.note?String(r.note):t('twinVoidDone'));
    ts.evidence=null; ts.overview=null;
    await Promise.all([twinLoadEvidence(), twinLoadOverview()]);
  }catch(e){
    ts.busy=false;
    alert((e&&e.message)?String(e.message):t('twinLoadError'));
    ts.evidence=null; twinLoadEvidence();
  }
}
/* ---- 票据 tab ---- */
async function twinLoadPending(){
  const ts=twinStateInit();
  try{ ts.pending=await twinPost('/api/twin/pending', {status:ts.pendingStatus, limit:100}); }
  catch(e){ ts.pending={ok:false, error:(e&&e.message)?String(e.message):'error'}; }
  twinRender();
}
function twinSetPendingStatus(value){
  const ts=twinStateInit(); ts.pendingStatus=value; ts.pending=null; ts.resolveOpen.clear(); twinLoadPending();
}
function twinToggleResolve(id){
  const ts=twinStateInit();
  if(ts.resolveOpen.has(id)) ts.resolveOpen.delete(id); else { ts.resolveOpen.add(id); twinEnsureTypes(twinPendingKind(id)); }
  twinRender();
}
function twinPendingKind(id){
  const ts=twinStateInit();
  const it=((ts.pending&&ts.pending.items)||[]).find(x=>Number(x.id)===Number(id))||{};
  return String(it.type_kind||'work_type');
}
async function twinEnsureTypes(kind){
  const ts=twinStateInit();
  if(ts.types[kind]) return;
  try{ const r=await twinPost('/api/twin/types', {type_kind:kind}); ts.types[kind]=r.items||[]; }
  catch(e){ ts.types[kind]=[]; }
  /* 原位刷新打开表单的 map 下拉——不整页重渲，保留用户已选的 decision/输入 */
  document.querySelectorAll('select[data-twin-kind]').forEach(sel=>{
    if(sel.dataset.twinKind===kind) sel.innerHTML=twinTypeOptions(kind);
  });
}
function twinTypeOptions(kind){
  const list=(twinStateInit().types[kind])||[];
  return list.map(tp=>`<option value="${esc(String(tp.code))}">${esc(String(tp.label_zh||tp.label_en||tp.code))} (${esc(String(tp.code))})${tp.is_custom?' ●':''}</option>`).join('') || `<option value="">—</option>`;
}
function twinPendingTab(ts){
  const pd=ts.pending;
  if(!pd) return `<div class="empty">${esc(t('kbLoading'))}…</div>`;
  if(pd.ok===false) return `<div class="empty">${esc(String(pd.error||t('twinLoadError')))}</div>`;
  const statusOpts=['pending','mapped','canonicalized','rejected'];
  const bar=`<div class="twin-filterbar"><label>${esc(t('twinStatus'))} <select onchange="twinSetPendingStatus(this.value)">${statusOpts.map(s=>`<option value="${s}"${ts.pendingStatus===s?' selected':''}>${esc(twinPendStatusLabel(s))}</option>`).join('')}</select></label><span class="spacer"></span><span class="mini">${esc(t('total'))} ${esc(String((pd.items||[]).length))}</span></div>`;
  const rows=(pd.items||[]).map(it=>{
    const open = ts.resolveOpen.has(Number(it.id)) && it.status==='pending';
    const rawTitle = t('twinRawFrom').replace('{kind}', twinKindLabel(it.type_kind));
    return `<div class="twin-pend">
      <div class="twin-pend-head">
        <span>#${esc(String(it.id))}</span>
        <span class="twin-chip" title="${esc(String(it.type_kind||''))}">${esc(twinKindLabel(it.type_kind))}</span>
        <code class="twin-raw" title="${esc(rawTitle)}">${esc(String(it.raw_value||''))}</code>
        <span class="mini" title="${esc(t('twinHitsTitle'))}">${esc(t('twinHitsN').replace('{n}', String(it.hit_count??0)))}</span>
        <span class="twin-badge s-${esc(String(it.status||''))}">${esc(twinPendStatusLabel(it.status))}</span>
        ${it.resolved_code?`<span class="mini">→ ${esc(String(it.resolved_code))}</span>`:''}
        <span class="spacer"></span>
        ${it.status==='pending'?`<button class="minibtn" type="button" onclick="twinToggleResolve(${esc(String(it.id))})">${esc(open?t('collapseWorkspaces'):t('twinResolve'))}</button>`:''}
      </div>
      ${open?twinResolveForm(Number(it.id), String(it.type_kind||'work_type')):''}
    </div>`;
  }).join('');
  return `${bar}${(pd.items||[]).length?rows:`<div class="empty">—</div>`}`;
}
function twinResolveForm(id, kind){
  /* 每个选项配一句后果说明；「code」→「归到哪个类型」；提交按钮实底增强。 */
  const radio = (val, labelKey, descKey, extra) => `
      <label class="twin-radio">
        <span class="twin-radio-main"><input type="radio" name="twinDecision${id}" value="${val}" ${extra} onchange="twinResolveMode(${id},'${val}')"> ${esc(t(labelKey))}</span>
        <small class="twin-radio-desc">${esc(t(descKey))}</small>
      </label>`;
  return `<div class="twin-resolve">
    <div class="twin-radio-row">
      ${radio('map', 'twinResolveMap', 'twinResolveMapDesc', 'checked')}
      ${radio('canonicalize', 'twinResolveCanon', 'twinResolveCanonDesc', '')}
      ${radio('reject', 'twinResolveReject', 'twinResolveRejectDesc', '')}
    </div>
    <div class="twin-resolve-body" id="twinResolveBody${id}">
      <label>${esc(t('twinCode'))} <select id="twinMapCode${id}" data-twin-kind="${esc(kind)}">${twinTypeOptions(kind)}</select></label>
    </div>
    <div class="support-actions"><button class="minibtn twin-submit-btn" type="button" id="twinResolveBtn${id}" onclick="twinResolveSubmit(${id})">${esc(t('twinSubmit'))}</button></div>
  </div>`;
}
function twinResolveMode(id, mode){
  const body=document.getElementById('twinResolveBody'+id); if(!body) return;
  const kind=twinPendingKind(id);
  if(mode==='map'){
    body.innerHTML=`<label>${esc(t('twinCode'))} <select id="twinMapCode${id}" data-twin-kind="${esc(kind)}">${twinTypeOptions(kind)}</select></label>`;
    twinEnsureTypes(kind);
  } else if(mode==='canonicalize'){
    twinEnsureTypes(kind);  /* 供提交前本地撞码预检（服务端仍是权威校验） */
    body.innerHTML=`<div class="twin-newtype">
      <label>${esc(t('twinNewCodeKey'))} <input id="twinNewCode${id}" placeholder="perf-audit"></label>
      <label>${esc(t('twinNewLabel'))} <input id="twinNewZh${id}"></label>
      <label>${esc(t('twinNewEn'))} <input id="twinNewEn${id}"></label>
      <label>${esc(t('twinNewDomain'))} <input id="twinNewDomain${id}"></label>
    </div><div class="mini" style="opacity:.7">${esc(t('twinResolveCanonDesc'))}</div>`;
  } else {
    body.innerHTML=`<div class="mini" style="opacity:.8">${esc(t('twinResolveRejectDesc'))}</div>`;
  }
}
async function twinResolveSubmit(id){
  const ts=twinStateInit(); if(ts.busy) return;
  const sel=document.querySelector(`input[name="twinDecision${id}"]:checked`);
  if(!sel) return;
  const d=sel.value;
  const body={pending_id:id, decision:d};
  if(d==='map'){ const el=document.getElementById('twinMapCode'+id); body.code=el?el.value.trim():''; }
  else if(d==='canonicalize'){
    const g=x=>{ const el=document.getElementById(x+id); return el?el.value.trim():''; };
    body.new_type={code:g('twinNewCode'), zh:g('twinNewZh'), en:g('twinNewEn'), domain:g('twinNewDomain')};
    /* 本地撞码预检（服务端仍是权威校验）：types 已加载才预检，
       未加载（懒加载未回）直接提交靠 twin invalid_input 兜底 */
    const list=(state.twin.types[twinPendingKind(id)])||[];
    if(list.length && list.some(tp=>String(tp.code)===body.new_type.code)){
      alert(body.new_type.code+': '+t('twinCodeExists')); return;
    }
  }
  if(d==='reject' && !confirm(t('twinResolveRejectConfirm'))) return;
  ts.busy=true;
  const btn=document.getElementById('twinResolveBtn'+id);
  if(btn){ btn.disabled=true; btn.textContent=t('twinRunning'); }
  try{
    const r=await twinPost('/api/twin/resolve', body);
    ts.busy=false;
    if(r&&r.note) alert(String(r.note));
    ts.resolveOpen.clear(); ts.pending=null; ts.overview=null;
    await Promise.all([twinLoadPending(), twinLoadOverview()]);
  }catch(e){
    ts.busy=false;
    alert((e&&e.message)?String(e.message):t('twinLoadError'));
    /* 失败（已裁定/别名冲突/撞码）→ 刷新票据列表 */
    ts.resolveOpen.clear(); ts.pending=null;
    await twinLoadPending();
  }
}
/* ---- 审计 tab ---- */
async function twinLoadAudit(){
  const ts=twinStateInit();
  try{ ts.audit=await twinPost('/api/twin/audit', {limit:50}); }
  catch(e){ ts.audit={ok:false, error:(e&&e.message)?String(e.message):'error'}; }
  twinRender();
}
function twinAuditTab(ts){
  const au=ts.audit;
  if(!au) return `<div class="empty">${esc(t('kbLoading'))}…</div>`;
  if(au.ok===false) return `<div class="empty">${esc(String(au.error||t('twinLoadError')))}</div>`;
  const rows=au.items||[];
  if(!rows.length) return `<div class="empty">${esc(t('twinAuditEmpty'))}</div>`;
  return `<div class="twin-table-wrap"><table class="twin-table"><thead><tr>
      <th>id</th><th>ts</th><th>${esc(t('twinAuditAction'))}</th><th>${esc(t('twinAuditOk'))}</th><th>error</th><th>ms</th><th>${esc(t('twinAuditClient'))}</th>
    </tr></thead><tbody>${rows.map(it=>`<tr>
      <td>#${esc(String(it.id))}</td><td class="twin-ts">${esc(twinFmtTs(it.ts))}</td>
      <td>${esc(String(it.action||''))}</td>
      <td>${Number(it.ok)?'<span class="twin-badge s-compiled">✓</span>':'<span class="twin-badge s-void">✗</span>'}</td>
      <td class="twin-err">${esc(String(it.error||''))}</td>
      <td>${esc(String(it.ms??''))}</td><td>${esc(String(it.client||''))}</td>
    </tr>`).join('')}</tbody></table></div>`;
}
twinBoot();
"""
