import type { Order, PlanRevisionDiffSummary, PlanRevisionOrderDiff } from "../../api";

/** Revizyon karşılaştırma tablosu filtreleri. */
export type DiffFilter = "requested" | "unmet" | "pushed" | "newly_late" | "pulled" | "bumped" | "all";

export const DIFF_FILTERS: { id: DiffFilter; label: string; hint: string }[] = [
  { id: "requested", label: "Talepler", hint: "Bu revizyonda termin/taşıma girilen siparişler" },
  { id: "unmet", label: "Karşılanamayan", hint: "Yeni termine yetişmeyen talepler" },
  { id: "pushed", label: "Ötelenen", hint: "Talep dışı olup bitişi geriye kayan siparişler" },
  { id: "newly_late", label: "Yeni geç", hint: "Canlı planda zamanında iken öneride geç kalanlar" },
  { id: "pulled", label: "Öne alınan", hint: "Bitişi öne gelen siparişler" },
  { id: "bumped", label: "Kaydırılan", hint: "İş taşıma yolunda çakışıp kaydırılan işler" },
  { id: "all", label: "Tümü", hint: "Tüm açık siparişler" },
];

export function matchesFilter(r: PlanRevisionOrderDiff, f: DiffFilter): boolean {
  switch (f) {
    case "requested":
      return r.requested;
    case "unmet":
      return r.requested && r.met === false;
    case "pushed":
      return !r.requested && r.pushed;
    case "newly_late":
      return r.newly_late;
    case "pulled":
      return r.pulled_forward;
    case "bumped":
      return r.bumped;
    default:
      return true;
  }
}

export function filterDiffs(rows: PlanRevisionOrderDiff[], f: DiffFilter, query = ""): PlanRevisionOrderDiff[] {
  const q = query.trim().toLocaleLowerCase("tr");
  return rows.filter((r) => matchesFilter(r, f) && (!q || `${r.order_no} ${r.position_no} ${r.customer} ${r.item_code}`.toLocaleLowerCase("tr").includes(q)));
}

/** Sunucu özeti yoksa aynı kuralla istemcide hesaplanır (aynı sayılar). */
export function summarize(rows: PlanRevisionOrderDiff[]): PlanRevisionDiffSummary {
  const s: PlanRevisionDiffSummary = { requested: 0, met: 0, unmet: 0, pushed: 0, newly_late: 0, pulled_forward: 0, unchanged: 0, bumped: 0 };
  for (const r of rows) {
    if (r.requested) {
      s.requested += 1;
      if (r.met) s.met += 1;
      else s.unmet += 1;
    }
    if (r.pushed) s.pushed += 1;
    if (r.newly_late) s.newly_late += 1;
    if (r.pulled_forward) s.pulled_forward += 1;
    if (r.bumped) s.bumped += 1;
    if (!r.requested && (r.delta_days === 0 || r.delta_days === null) && r.status_after === r.status_before) s.unchanged += 1;
  }
  return s;
}

/** Karşılanamayan taleplerin taslak girdi kimlikleri (toplu geri çekme için). */
export function unmetChangeIds(rows: PlanRevisionOrderDiff[]): number[] {
  return rows.filter((r) => r.requested && r.met === false && r.change_id !== null).map((r) => r.change_id as number);
}

export function deltaLabel(d: number | null): string {
  if (d === null || d === undefined) return "—";
  if (d === 0) return "0";
  return d > 0 ? `+${d} gün` : `−${Math.abs(d)} gün`;
}

const STATUS: Record<string, [string, string]> = {
  on_time: ["ok", "Zamanında"],
  late: ["bad", "Geç"],
  partial: ["warn", "Kısmi"],
  unplanned: ["bad", "Plansız"],
  no_ops: ["muted", "Rota yok"],
  finish_unknown: ["muted", "Bitiş belirsiz"],
};

export function statusBadge(s: string): [string, string] {
  return STATUS[s] || ["muted", s || "—"];
}

/** Talep girişi: seçili siparişler için sipariş başına yeni termin taslağı. */
export interface DueDraft {
  order_id: number;
  order_no: string;
  position_no: string;
  customer: string;
  item_code: string;
  current_due: string;
  planned_end: string | null;
  new_due: string;
}

export function buildDueDrafts(orders: Order[], ids: number[], previous: Record<number, string> = {}): DueDraft[] {
  const byId = new Map(orders.map((o) => [o.id, o]));
  return ids
    .map((id) => byId.get(id))
    .filter((o): o is Order => !!o)
    .map((o) => ({
      order_id: o.id,
      order_no: o.order_no,
      position_no: o.position_no,
      customer: o.customer,
      item_code: o.item_code,
      current_due: o.effective_due_date || o.due_date,
      planned_end: o.planned_end,
      new_due: previous[o.id] ?? "",
    }));
}

export function shiftIsoDate(iso: string, days: number): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return "";
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Tüm taslaklara aynı tarihi ya da mevcut termine göre gün kaydırması uygular. */
export function applyToDrafts(drafts: DueDraft[], mode: "same" | "shift", value: string | number): DueDraft[] {
  return drafts.map((d) => ({ ...d, new_due: mode === "same" ? String(value) : shiftIsoDate(d.current_due, Number(value)) }));
}

export function draftsToChanges(drafts: DueDraft[]) {
  return drafts
    .filter((d) => d.new_due && d.new_due !== d.current_due)
    .map((d) => ({ entity_type: "order", entity_id: d.order_id, field: "revised_due_date", new_value: d.new_due }));
}

/** Karşılaştırma tablosunu Excel'de açılabilir CSV'ye çevirir (UTF-8 BOM, noktalı virgül). */
export function diffsToCsv(rows: PlanRevisionOrderDiff[]): string {
  const head = ["Sipariş", "Poz", "Müşteri", "Stok", "Miktar", "Termin (önce)", "Termin (sonra)", "Bitiş (önce)", "Bitiş (sonra)", "Kayma (gün)", "Durum (önce)", "Durum (sonra)", "Talep", "Karşılandı", "Ötelendi", "Yeni geç", "Kaydırıldı"];
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = rows.map((r) =>
    [r.order_no, r.position_no, r.customer, r.item_code, r.quantity, r.due_before, r.due_after, r.end_before, r.end_after, r.delta_days,
      statusBadge(r.status_before)[1], statusBadge(r.status_after)[1], r.requested ? "E" : "H", r.requested ? (r.met ? "E" : "H") : "", r.pushed ? "E" : "H", r.newly_late ? "E" : "H", r.bumped ? "E" : "H"]
      .map(esc)
      .join(";"),
  );
  return `﻿${head.map(esc).join(";")}\n${lines.join("\n")}`;
}
