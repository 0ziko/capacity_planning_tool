import { useState, useEffect, useRef } from 'react';
import { api, qs, fmt, mondayOf, addDays, shortDate, weekLabel, type WorkCenterLoad, type GanttData } from '../api';
import { ErrorText, useAsync, useWorkCenters, StringMultiSelect } from '../components';
import { parseCodes, flowGroups, flowLayout, type FlowKind, type FlowBar } from './planning/ganttFlow';
import './Gantt.css';

export default function GanttPage() {
  const {wcs}=useWorkCenters();
  const planned=wcs.filter(w=>w.is_planned);
  const [tab,setTab]=useState<'centers'|'flow'>('centers');
  const [start,setStart]=useState(mondayOf(new Date()));
  const [weeks,setWeeks]=useState(8);
  const end=addDays(start,weeks*7-1);
  const [centers,setCenters]=useState<string[]>([]);
  const [kind,setKind]=useState<FlowKind>('item');
  const [input,setInput]=useState('');
  const [selection,setSelection]=useState<{kind:FlowKind;codes:string[]}|null>(null);
  const [zoom,setZoom]=useState(0);
  const scrollRef=useRef<HTMLDivElement>(null);
  const [availableWidth,setAvailableWidth]=useState(1200);
  const [reload,setReload]=useState(0);
  const load=useAsync(()=>api.get<WorkCenterLoad[]>(`/api/plan/load${qs({start,weeks})}`),[start,weeks,reload],tab==='centers');
  const flow=useAsync(async()=>{
    if(!selection?.codes.length)return [] as FlowBar[];
    const rows:FlowBar[]=[];
    for(const wc of planned){
      const g=await api.get<GanttData>(`/api/plan/gantt${qs({work_center_id:wc.id,start,end,selection_kind:selection.kind,selection_codes:selection.codes})}`);
      rows.push(...g.bars.map(b=>({...b,work_center_code:wc.code})));
    }
    return rows;
  },[selection,start,end,planned.map(w=>w.id).join(','),reload],tab==='flow'&&!!selection?.codes.length&&planned.length>0);
  useEffect(()=>{
    const el=scrollRef.current;
    if(!el)return;
    const observer=new ResizeObserver(entries=>setAvailableWidth(entries[0].contentRect.width));
    observer.observe(el);
    return ()=>observer.disconnect();
  },[tab,flow.loading,selection]);
  const pixelsPerDay=zoom||Math.max(6,(availableWidth-382)/(weeks*7));
  const groups=selection?flowGroups(flow.data??[],selection.kind,selection.codes):[];
  const days=Array.from({length:weeks*7},(_,i)=>addDays(start,i));
  return <>
    <h1>Gantt</h1>
    <div className="tabs" role="tablist" aria-label="Gantt görünümleri">
      <button role="tab" aria-selected={tab==='centers'} className={tab==='centers'?'active':''} onClick={()=>setTab('centers')}>İş merkezi dolulukları</button>
      <button role="tab" aria-selected={tab==='flow'} className={tab==='flow'?'active':''} onClick={()=>setTab('flow')}>Sipariş / Mamul / Yarımamül</button>
    </div>
    <div className="panel row">
      <label>Başlangıç haftası<input type="date" value={start} onChange={e=>{if(e.target.value)setStart(mondayOf(new Date(e.target.value+'T12:00:00')));}} /></label>
      <label>Hafta sayısı<input type="number" min={1} max={26} value={weeks} onChange={e=>setWeeks(Math.min(26,Math.max(1,Number(e.target.value)||1)))} /></label>
      <span className="muted">{shortDate(start)} – {shortDate(end)}</span>
      <button className="secondary" disabled={load.loading||flow.loading} onClick={()=>setReload(x=>x+1)}>Yenile</button>
    </div>
    {tab==='centers'?<>
      <div className="row"><StringMultiSelect label="İş merkezleri" options={planned.map(w=>w.code)} value={centers} onChange={setCenters}/></div>
      <p className="muted">Her satır bir iş merkezi, her kutu bir haftadır. Doluluk, planlanan saat / planlanabilir kapasite hesabıdır; %100 üzeri kapasite aşımıdır.</p>
      <ErrorText err={load.err}/>
      {!load.loading && (load.data??[]).filter(r=>!centers.length||centers.includes(r.work_center_code)).map(r=>(r.conditional_line_count||r.unknown_material_line_count)?<p className="muted" key={r.work_center_id}>{r.work_center_code}: {r.conditional_line_count||0} koşullu plan satırı; {r.unknown_material_line_count||0} satırın oluşturulma anındaki malzeme koşulu kaydedilmemiş.</p>:null)}
      {load.loading?<p>Doluluklar yükleniyor…</p>:<div className="table-wrap"><table className="gantt-util"><thead><tr><th>İş merkezi</th>{Array.from({length:weeks},(_,i)=><th key={i}>{weekLabel(addDays(start,i*7))}</th>)}</tr></thead><tbody>
        {planned.filter(w=>!centers.length||centers.includes(w.code)).map(w=>{const row=load.data?.find(r=>r.work_center_id===w.id);return <tr key={w.id}><th>{w.code}</th>{Array.from({length:weeks},(_,i)=>{const v=row?.weeks.find(x=>x.week_start===addDays(start,i*7));const percent=v&&v.planning_capacity_hours>0?v.planned_hours/v.planning_capacity_hours*100:null;return <td key={i}><div className={`util-cell ${percent!==null&&percent>100?'over':''}`} title={v?`Plan ${fmt(v.planned_hours)} saat / kapasite ${fmt(v.planning_capacity_hours)} saat`:'Veri yok'}><b>{percent!==null?`${fmt(percent,0)}%`:v?.planned_hours?'Kapasite yok':'—'}</b><small>{v?`${fmt(v.planned_hours)} / ${fmt(v.planning_capacity_hours)} sa`:'Veri yok'}</small><span style={{width:`${Math.min(100,percent??0)}%`}}/></div></td>;})}</tr>;})}
      </tbody></table></div>}
    </>:<>
      <form className="panel row" onSubmit={e=>{e.preventDefault();setSelection({kind,codes:parseCodes(input)});}}>
        <label>Arama türü<select value={kind} onChange={e=>{setKind(e.target.value as FlowKind);setSelection(null);}}><option value="order">Sipariş</option><option value="item">Mamul</option><option value="wip">Yarımamül</option></select></label>
        <label style={{flex:1}}>Kod / sipariş numarası (tek veya çoklu)<input value={input} onChange={e=>setInput(e.target.value)} placeholder="Örn. 6007710, 6010758 — virgül, boşluk veya noktalı virgülle ayır"/></label>
        <button type="submit" disabled={!parseCodes(input).length||flow.loading}>Gantt oluştur</button>
        <button type="button" className="secondary" onClick={()=>{setInput('');setSelection(null);}}>Temizle</button>
        <label>Görünüm<select value={zoom} onChange={e=>setZoom(Number(e.target.value))}><option value={0}>Ekrana sığdır</option><option value={24}>Kompakt</option><option value={48}>Yakın</option><option value={96}>Ayrıntılı</option></select></label>
      </form>
      <p className="muted">Tam kodla arama yapılır. Her seçim tek ana satırdır; eşzamanlı operasyonlar aynı satır içinde alt alta görünür. Kutularda operasyon adı, altında iş merkezi bulunur. Tarihler haftalık plandan tahmindir; kesin günlük çizelge değildir.</p>
      <ErrorText err={flow.err}/>
      {!selection?.codes.length?<div className="panel muted">Akışını görmek istediğin kodları girip “Gantt oluştur” düğmesine bas.</div>:flow.loading?<div className="panel">Seçilen kayıtların operasyonları yükleniyor…</div>:!flow.err&&<div className="flow-scroll" ref={scrollRef}>
        <div className="flow-header" style={{width:230+days.length*pixelsPerDay+150}}><div className="flow-label">{selection.kind==='order'?'Sipariş':selection.kind==='item'?'Mamul':'Yarımamül'}</div><div className="flow-dates">{days.map((d,i)=>pixelsPerDay<24 ? (i%7===0&&<span key={d} style={{width:pixelsPerDay*7}} title={d}>{shortDate(d)}</span>) : <span key={d} style={{width:pixelsPerDay}} title={d}>{shortDate(d)}</span>)}</div></div>
        {groups.map(group=>{const layout=flowLayout(group.bars,start,end,pixelsPerDay);return <div key={group.code} className="flow-row" style={{width:230+layout.width,minHeight:Math.max(70,layout.lanes*58+12)}}><div className="flow-label"><b>{group.code}</b><small>{group.bars.length} plan operasyon kaydı</small>{!group.bars.length&&<small>Seçilen tarihlerde plan yok.</small>}</div><div className="flow-track" style={{width:layout.width}}>{layout.boxes.map(({bar,x,width,lane})=><div key={bar.plan_line_id} className={`flow-operation ${bar.status}`} style={{left:x,top:lane*58+6,width}} title={`${bar.operation_name} · ${bar.work_center_code}\n${bar.semi_finished_code} ${bar.semi_finished_name}\n${bar.order_no} ${bar.position_no}\n${bar.planned_start} – ${bar.planned_end}\nÜretim ${bar.produced_qty} / Plan ${bar.planned_qty} adet`}><span className="flow-earned" style={{width:`${bar.planned_qty?Math.min(100,bar.produced_qty/bar.planned_qty*100):0}%`}}/><strong>{bar.operation_name||'Operasyon tanımı yok'}</strong><small title={bar.material_note}>{bar.work_center_code}{bar.material_unverified !== false ? (bar.material_unverified ? " · Koşullu" : " · Malzeme koşulu bilinmiyor") : ""}</small></div>)}</div></div>;})}
      </div>}
    </>}
  </>;
}
