import type { GanttBar } from '../../api';
export type FlowKind = 'order' | 'item' | 'wip';
export type FlowBar = GanttBar & { work_center_code: string };
export function parseCodes(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean))];
}
export function flowGroups(bars: FlowBar[], kind: FlowKind, codes: string[]) {
  return codes.map(code => ({ code, bars: bars.filter(b => {
    if (kind === 'item') return b.item_code === code;
    if (kind === 'wip') return b.semi_finished_code === code;
    return b.order_no === code || `${b.order_no}/${b.position_no}` === code || b.batch_order_nos.some(n => n === code || n.startsWith(code + '/'));
  }).sort((a,b) => a.planned_start.localeCompare(b.planned_start) || a.operation_seq-b.operation_seq || a.plan_line_id-b.plan_line_id) }));
}
// Reserve room for the operation label as well as the actual time interval.
// Parallel operations stay at their real dates in separate lanes within one entity row.
export function flowLayout(bars: FlowBar[], start: string, end: string, pixelsPerDay: number) {
  const day = (s: string) => Date.parse(s+'T00:00:00Z') / 86400000;
  const first=day(start), total=day(end)-first+1;
  const occupied: number[]=[];
  const boxes=bars.map(bar => {
    const x=Math.max(0,day(bar.planned_start)-first)*pixelsPerDay;
    const width=Math.max(1,(Math.min(day(end),day(bar.planned_end))-Math.max(first,day(bar.planned_start))+1)*pixelsPerDay);
    let lane=occupied.findIndex(right => right<=x);
    if(lane<0) lane=occupied.length;
    occupied[lane]=x+Math.max(width,140)+8;
    return {bar,x,width,lane};
  });
  return {boxes,lanes:Math.max(1,occupied.length),width:total*pixelsPerDay+150};
}
