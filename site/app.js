'use strict';
let snapshot = null, filter = 'all', requestId = 0;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = (value, digits=2) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
const stamp = value => { const d = new Date(value); return value && Number.isFinite(d.getTime()) ? d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) + ' CST' : '未采集'; };
const expired = data => data.stale_collection || !data.valid_until || !Number.isFinite(Date.parse(data.valid_until)) || Date.now() > Date.parse(data.valid_until) || !Number.isFinite(Date.parse(data.collected_at)) || Date.parse(data.collected_at) > Date.now() + 60000 || Date.now() - Date.parse(data.collected_at) > 36*3600000;
function spark(values, label='近30日收盘趋势') {
  if (!values || values.length < 2 || !values.every(Number.isFinite)) return '<span class="detail-sub">数据待核验</span>';
  const min = Math.min(...values), range = Math.max(...values)-min || 1;
  const path = values.map((v,i)=>`${i?'L':'M'}${(i/(values.length-1)*100).toFixed(2)},${(26-(v-min)/range*23).toFixed(2)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 100 30" aria-label="${esc(label)}"><path d="${path}" fill="none" stroke="#70e4c1" stroke-width="1.2"/></svg>`;
}
function showDialog(html) { $('detail-content').innerHTML = html; $('detail').showModal(); }
function renderStocks() {
  const rows = snapshot.stocks.filter(s=>filter==='all' || (filter==='candidate' ? s.buy_point.includes('候选') : s.blockers.length));
  $('stocks').innerHTML = rows.length ? rows.map(s=>`<button class="stock-row" data-code="${esc(s.code)}"><span><strong>${esc(s.name)}</strong><small>${esc(s.code)}</small></span><span class="price">${number(s.indicators?.close)}<small class="${(s.indicators?.change_pct??0)>=0?'up':'down'}">${s.indicators?number(s.indicators.change_pct)+'%':'行情待核验'}</small></span><span class="chart-cell">${spark(s.chart)}</span><span class="score">${number(s.score??s.technical_subtotal,0)}<small>/${s.score==null?'60 小计':'100'}</small></span><span class="row-state">${esc(s.buy_point)}<small>${s.research_permission&&!expired(snapshot)?'研究条件通过':'新开仓阻断'} ↗</small></span></button>`).join('') : '<p class="empty">当前没有符合此筛选条件的标的。</p>';
}
function renderSectors(sector) {
  const items=sector?.items||[];
  $('sector-list').innerHTML=items.length?items.map(s=>`<div class="sector-row"><strong>${esc(s.name)}</strong> <small>${esc(s.hot_phase)} · ${number(s.score,0)}/20</small><p>${s.quality.length?esc(s.quality.join('；')):`近5日 ${number(s.return_5d_pct)}% · 净流入 ${number(s.flow?.net_5d/1e8)}亿元`}</p><small>${esc(s.source)}</small></div>`).join(''):'<strong>行业与资金数据未通过</strong><p>缺失数据不补分，无法确认资金主线。</p>';
  $('sector-list').innerHTML+=`<p>${esc(sector?.description||'仅分析自选股所属行业')}</p>`;
}
function render(data) {
  if (!data || data.schema_version !== '2.0' || !Array.isArray(data.stocks) || !Array.isArray(data.indices)) throw new Error('数据结构无法识别');
  snapshot=data;
  const stale=expired(data), demo=data.mode==='demo';
  $('notice-title').textContent=demo?'合成演示数据 · 所有开仓许可关闭':stale?'数据已过期或采集时间异常 · 禁止新开仓':data.market.quality_pass?'日线研究快照 · 需本地账户复核':'真实接口数据未通过核验 · 禁止新开仓';
  $('notice-text').textContent=demo?'价格、名称、资金流和日历均由本地生成，不是实时或历史真实行情。':stale?'当前显示仅供查看旧结果。请重新采集行情与交易日历。':'研究条件需行情、行业、资金与风险核验同时通过；收盘值不是实时成交价。';
  $('source').textContent='来源：'+data.source;
  $('quote-date').textContent='目标完整交易日：'+(data.expected_quote_date||'无法确认');
  $('collected').textContent='采集：'+stamp(data.collected_at);
  $('delay').textContent=demo?'延迟状态：合成演示 / 非行情':stale?'延迟状态：已过期/异常':!data.market.quality_pass?'延迟状态：行情缺失/异常':'延迟状态：日线 / 非实时';
  $('indices').innerHTML=data.indices.map(row=>`<article class="index-card"><div class="index-top">${esc(row.name)} <small>${esc(row.code)}</small></div><div class="index-value">${number(row.indicators?.close)}</div><div class="index-change ${(row.indicators?.change_pct??0)>=0?'up':'down'}">${row.indicators?number(row.indicators.change_pct)+'%':'行情不可用'}</div><div class="index-bottom"><span>${esc(row.quote_date||'无行情日期')}</span><span>${row.quality.length?'质量阻断':'完整日线'}</span></div></article>`).join('');
  $('cycle').textContent=stale?'?':(data.market.cycle==='UNKNOWN'?'?':data.market.cycle);
  $('cycle-name').textContent=stale?'快照过期':'数据判断：'+(data.market.cycle==='UNKNOWN'?'无法判断':data.market.cycle+'周期');
  $('cycle-note').textContent=stale?'禁止依据旧信号开仓':data.market.description;
  renderSectors(data.sector); renderStocks();
  const plans=data.stocks.filter(s=>s.model_plan?.shares>0);
  $('plans').innerHTML=plans.length?'<div class="plans-list">'+plans.map(s=>`<div class="plan-row"><span>${esc(s.code)}<small>模型测算 · 无买入许可</small></span><span><b>${s.model_plan.shares}股</b><small>预算占用 ¥${number(s.model_plan.cost)}</small></span><span><b>¥${number(s.model_plan.planned_loss)}</b><small>含成本计划损失</small></span></div>`).join('')+'</div>':'<p class="empty">当前没有通过数据核验且满足整手、预算和风险约束的模型股数测算。</p>';
  $('export').disabled=false;
}
async function load() {
  const id=++requestId;
  $('refresh').disabled=true;
  try {
    const mode=$('mode').value;
    const response=await fetch(mode==='demo'?'data/demo.json':'data/analysis.json',{cache:'no-store'});
    if (!response.ok) throw new Error('快照读取失败');
    const data=await response.json();
    if(id!==requestId) return;
    if(data.mode!==mode) throw new Error('数据模式与文件不一致');
    render(data);
  } catch(error) {
    if(id!==requestId) return;
    snapshot=null;
    $('notice-title').textContent='行情快照不可用 · 禁止新开仓';
    $('notice-text').textContent='请先运行本地采集命令；合成演示需手动切换，不会自动替代真实行情。';
    $('indices').innerHTML=''; $('stocks').innerHTML='<p class="empty">无可验证数据</p>';
    $('plans').innerHTML='<p class="empty">无可验证交易计划</p>'; $('sector-list').innerHTML='<strong>无可验证行业数据</strong>';
    $('cycle').textContent='?'; $('cycle-name').textContent='无法判断'; $('cycle-note').textContent='快照读取失败';
    for(const name of ['source','quote-date','collected','delay']) $(name).textContent='数据不可用';
    $('export').disabled=true;
  } finally { if(id===requestId) $('refresh').disabled=false; }
}
$('mode').addEventListener('change',load); $('refresh').addEventListener('click',load);
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b===button));if(snapshot)renderStocks();}));
$('stocks').addEventListener('click',event=>{
  const code=event.target.closest('[data-code]')?.dataset.code;
  const stock=snapshot?.stocks.find(s=>s.code===code); if(!stock)return;
  const labels={ma5:'MA5',ma10:'MA10',ma20:'MA20',ma30:'MA30',dif:'MACD DIF',dea:'MACD DEA',macd:'MACD柱',volume_ratio:'量比 / 前5日平均'};
  showDialog(`<span class="eyebrow">STOCK DETAIL / ${esc(code)}</span><h2>${esc(stock.name)}</h2><p class="detail-sub">${esc(stock.quote_date)} · ${snapshot.mode==='demo'?'合成演示':'日线收盘数据'} · 综合 ${number(stock.score,0)}/100</p><p class="detail-sub">行业：${esc(stock.industry)} / ${esc(stock.hot_phase)} · 行情来源：${esc(stock.provenance?.selected_source)} / 交叉核验：${esc(stock.provenance?.cross_check?.status)}</p><div class="detail-grid">${Object.entries(labels).map(([k,v])=>`<div class="detail-item"><small>${v}</small><b>${number(stock.indicators?.[k],/^ma\d/.test(k)?2:4)}</b></div>`).join('')}</div><h2>五维评分</h2><p class="detail-sub">行业 ${number(stock.scores.industry,0)}/20 · 资金 ${number(stock.scores.funds,0)}/20 · 趋势 ${number(stock.scores.trend,0)}/25 · 买点 ${number(stock.scores.entry,0)}/20 · 账户适配 ${number(stock.scores.account,0)}/15</p><p class="detail-sub">${esc(stock.permission_scope)}</p><h2>风险与开仓检查</h2><ul class="detail-list">${stock.blockers.length?stock.blockers.map(b=>`<li>${esc(b)}</li>`).join(''):'<li>研究条件通过；本地模拟账户仍须核验可用资金、持仓与执行价格。</li>'}${expired(snapshot)?'<li>当前快照过期或采集时间异常</li>':''}</ul>${stock.model_plan?`<h2>模型测算参考</h2><p class="detail-sub">收盘参考 ¥${number(stock.model_plan.entry)} / 计划止损 ¥${number(stock.model_plan.stop)} / ${stock.model_plan.shares}股。单标的预算2500元；不是可执行委托。</p>`:''}`);
});
$('interfaces').addEventListener('click',()=>{const reports=snapshot?.interface_report||[];showDialog('<span class="eyebrow">SOURCE HEALTH</span><h2>数据接口测试</h2><p class="detail-sub">这是采集当时的测试记录；接口成功不等于行情质量通过。</p>'+ (reports.length?reports.map(r=>`<div class="report-row">${esc(r.key)} · ${esc(r.status)}<small>${esc(r.interface)} / ${r.rows??0}行 / ${r.duration_seconds}秒 / ${esc(r.error||'无请求错误')}</small><small>${esc(r.collected_at)}</small></div>`).join(''):'<p class="empty">演示视图不含真实接口测试。请切换真实接口采集视图。</p>'));});
$('export').addEventListener('click',()=>{if(!snapshot)return;const blob=new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`ashare-stage2-${snapshot.mode}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
const tradeReason = reason => ({next_session_open:'次日开盘',stop_or_gap:'止损 / 跳空',confirmed_market_C:'弱市退出',max_10_sessions:'持有期到期',manual_simulated_exit:'手动模拟退出'}[reason] || '模拟退出');
function renderReplay(report) {
  if(report?.schema_version!=='backtest-2'||report.kind!=='simulated_only'||!['live','demo'].includes(report.mode)||!report.metrics||!Array.isArray(report.trades)||!Array.isArray(report.equity_curve))throw new Error('仅接受 backtest-2 格式模拟报告');
  const m=report.metrics, demo=report.mode==='demo';
  $('report-status').textContent=demo?'合成演示回放 · 所有价格、日期与成交均为虚构；不代表策略真实表现。':'本地模拟报告 · 仅在此浏览器读取，无上传；仅对提供的时点快照样本有效。';
  const stats=[['区间',esc(report.start_date)+' → '+esc(report.end_date)],['模拟期末权益','¥'+number(m.ending_equity)],['样本净变化',number(m.net_return_pct)+'%'],['样本最大回撤',number(m.max_drawdown_pct)+'%'],['已平仓交易',number(m.closed_trades,0)+'笔'],['累计模型费用','¥'+number(m.total_fees)]];
  $('replay-results').innerHTML=`<div class="replay-banner">${demo?'虚构行情流程测试，不能作为投资收益证据。':'开放持仓按收盘估值，未扣除未来卖出成本；数据不完整时收益指标留空。'} ${esc(report.source)}</div><div class="replay-metrics">${stats.map(([label,value])=>`<div><small>${label}</small><strong>${value}</strong></div>`).join('')}</div><div class="curve-wrap"><span class="detail-sub">${demo?'合成演示':'本地模拟'}权益曲线 / 非真实账户</span>${spark(report.equity_curve.map(r=>r.equity_cents/100),'模拟权益曲线')}</div><div class="replay-trades"><div class="replay-trade head"><span>最近10条记录</span><span>代码 / 方向</span><span>价格 / 股数</span><span>费用 / 已实现盈亏</span></div>${report.trades.slice(-10).reverse().map(t=>`<div class="replay-trade"><span>${esc(t.date)}<small>${esc(tradeReason(t.reason))}</small></span><span>${esc(t.code)}<small>${t.side==='BUY'?'模拟买入':'模拟卖出'}</small></span><span>¥${number(t.price)}<small>${number(t.shares,0)}股</small></span><span>¥${number(t.fee_cents/100)}<small>${t.side==='SELL'?'¥'+number(t.pnl_cents/100):'—'}</small></span></div>`).join('')||'<p class="empty">当前样本无模拟成交</p>'}</div>`;
}
$('load-demo-replay').addEventListener('click',async()=>{try{const response=await fetch('data/replay-demo.json',{cache:'no-store'});if(!response.ok)throw new Error('演示报告读取失败');const report=await response.json();if(report.mode!=='demo')throw new Error('演示报告模式不匹配');renderReplay(report);}catch(error){$('report-status').textContent=error.message;$('replay-results').innerHTML='';}});
$('report-file').addEventListener('change',async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>10*1024*1024)throw new Error('模拟报告超过10MB，请使用精简报告');renderReplay(JSON.parse(await file.text()));}catch(error){$('report-status').textContent='报告读取失败：'+error.message;$('replay-results').innerHTML='';}});
$('detail').querySelector('.close').addEventListener('click',()=> $('detail').close());
setInterval(()=>{if(snapshot&&expired(snapshot))render(snapshot)},60000);
load();
