import type { BomLineOut } from "./api";

export type BomKind =
  | "Mamul"
  | "Bitirme"
  | "Bileşen"
  | "Yarı mamül"
  | "Yarı mamül adımı"
  | "Hammadde"
  | "Alt yarı mamül"
  | "Diğer";

export interface BomTreeNode {
  id: string;
  line: BomLineOut;
  kind: BomKind;
  children: BomTreeNode[];
}

export interface BomOperationRef {
  seq: number;
  operation_name: string;
  semi_finished_code: string;
  wip_code?: string;
}

export function baseWip(code: string): string {
  const c = (code || "").trim();
  return c.includes("-") ? c.split("-")[0]! : c;
}

export function isFgCode(code: string): boolean {
  return /^6\d{5,}$/.test(code || "");
}

export function isWipOutputCode(code: string): boolean {
  return /^5\d{5,}(-\d+)?$/.test(code || "");
}

export function isWipAsmLink(code: string, src: string, recipeSeq?: number | null): boolean {
  const c = (code || "").trim();
  const s = (src || "").trim();
  const seq = recipeSeq == null ? 0 : recipeSeq;
  return isWipOutputCode(c) && c === s && seq === 0;
}

export function bomLineKind(b: BomLineOut): BomKind {
  const code = b.component_code || "";
  const src = b.source_wip || "";
  if (isWipAsmLink(code, src, b.recipe_seq)) return "Yarı mamül";
  if (/^5\d{5,}-/.test(code)) return "Yarı mamül adımı";
  if (/^[1-4]/.test(code)) return "Hammadde";
  if (/^5\d{5,}$/.test(code)) return "Alt yarı mamül";
  return "Diğer";
}

export function bomKindBadge(kind: BomKind): string {
  if (kind === "Mamul") return "ok";
  if (kind === "Bitirme") return "ok";
  if (kind === "Bileşen") return "warn";
  if (kind === "Yarı mamül") return "ok";
  if (kind === "Yarı mamül adımı") return "info";
  if (kind === "Hammadde") return "muted";
  return "warn";
}

function isStepBomLine(b: BomLineOut): boolean {
  if (isWipAsmLink(b.component_code, b.source_wip || "", b.recipe_seq)) return false;
  return /^5\d{5,}-/.test(b.component_code || "");
}

function materialLinesForRange(lines: BomLineOut[], source: string, stepSeq: number, nextStepSeq: number | null): BomLineOut[] {
  return lines.filter((l) => {
    if ((l.source_wip || "") !== source) return false;
    if (isWipAsmLink(l.component_code, l.source_wip || "", l.recipe_seq)) return false;
    if (isStepBomLine(l)) return false;
    const rs = l.recipe_seq ?? 99999;
    if (rs <= stepSeq) return false;
    if (nextStepSeq != null && rs >= nextStepSeq) return false;
    return true;
  });
}

function uniqueOpsBySemi(ops: BomOperationRef[]): BomOperationRef[] {
  const out: BomOperationRef[] = [];
  const seen = new Set<string>();
  for (const op of [...ops].sort((a, b) => a.seq - b.seq)) {
    const sf = (op.semi_finished_code || "").trim();
    if (!sf || seen.has(sf)) continue;
    seen.add(sf);
    out.push(op);
  }
  return out;
}

/** Operasyonun hammaddelerinin source_wip anahtari. */
export function sourceKeyForOp(op: BomOperationRef, allOps: BomOperationRef[]): string {
  const wip = (op.wip_code || "").trim();
  if (wip) return wip;
  const finishOps = allOps.filter((o) => !(o.wip_code || "").trim());
  const unique = uniqueOpsBySemi(finishOps.length ? finishOps : allOps);
  const last = unique.length ? unique[unique.length - 1] : undefined;
  return last?.semi_finished_code || op.semi_finished_code || "";
}

function indexMaterialsByOp(ops: BomOperationRef[], lines: BomLineOut[]): Map<string, BomLineOut[]> {
  const map = new Map<string, BomLineOut[]>();
  const sources = new Set<string>();
  for (const l of lines) {
    if (l.source_wip) sources.add(l.source_wip);
  }

  for (const source of sources) {
    const groupOps = ops.filter((op) => sourceKeyForOp(op, ops) === source);
    const unique = uniqueOpsBySemi(groupOps);
    if (!unique.length) continue;
    for (let i = 0; i < unique.length; i++) {
      const stepSeq = (i + 1) * 10;
      const nextSeq = i + 1 < unique.length ? (i + 2) * 10 : null;
      const sf = unique[i]!.semi_finished_code;
      map.set(`${source}::${sf}`, materialLinesForRange(lines, source, stepSeq, nextSeq));
    }
  }
  return map;
}

function materialNode(line: BomLineOut): BomTreeNode {
  return {
    id: `mat-${line.id}-${line.component_code}`,
    line,
    kind: bomLineKind(line) === "Diğer" ? "Hammadde" : bomLineKind(line),
    children: [],
  };
}

function stepNode(op: BomOperationRef, mats: BomLineOut[], source: string): BomTreeNode {
  const sf = op.semi_finished_code || "";
  return {
    id: `op-${op.seq}-${sf}`,
    kind: "Yarı mamül adımı",
    line: {
      id: -op.seq,
      component_code: sf,
      component_name: op.operation_name,
      quantity: 1,
      unit: "AD",
      source_wip: source,
      recipe_seq: op.seq,
    },
    children: mats.map(materialNode),
  };
}

function fgNode(code: string, name: string): BomTreeNode {
  return {
    id: `fg-${code}`,
    kind: "Mamul",
    line: {
      id: 0,
      component_code: code,
      component_name: name || code,
      quantity: 1,
      unit: "AD",
      source_wip: "",
    },
    children: [],
  };
}

/**
 * Malzeme reçetesi = operasyon sıralaması.
 * Her yarı mamul adımının altında o adımda tüketilen hammaddeler;
 * mamul en altta.
 */
export function buildBomTreeFromOperations(
  lines: BomLineOut[],
  operations: BomOperationRef[],
  productCode?: string,
  productName?: string,
): BomTreeNode[] {
  const ops = [...operations].sort((a, b) => a.seq - b.seq);
  if (!ops.length) return [];

  const matsByOp = indexMaterialsByOp(ops, lines);
  const attached = new Set<string>();
  const nodes: BomTreeNode[] = [];

  for (const op of ops) {
    const source = sourceKeyForOp(op, ops);
    const key = `${source}::${op.semi_finished_code}`;
    const mats = attached.has(key) ? [] : matsByOp.get(key) || [];
    attached.add(key);
    nodes.push(stepNode(op, mats, source));
  }

  if (productCode && isFgCode(productCode)) {
    nodes.push(fgNode(productCode, productName || productCode));
  }

  return nodes;
}

function buildFromLinesOnly(lines: BomLineOut[], productCode?: string, productName?: string): BomTreeNode[] {
  const bySource = new Map<string, BomLineOut[]>();
  for (const l of lines) {
    const s = l.source_wip || "—";
    if (!bySource.has(s)) bySource.set(s, []);
    bySource.get(s)!.push(l);
  }

  const nodes: BomTreeNode[] = [];
  const sources = [...bySource.keys()].sort((a, b) => {
    const sa = Math.max(0, ...(bySource.get(a) || []).map((l) => l.branch_listing_sira ?? 0));
    const sb = Math.max(0, ...(bySource.get(b) || []).map((l) => l.branch_listing_sira ?? 0));
    return sb - sa;
  });

  for (const source of sources) {
    if (source === "—") continue;
    const bls = [...(bySource.get(source) || [])].sort((a, b) => (a.recipe_seq ?? 0) - (b.recipe_seq ?? 0));
    const steps = bls.filter(isStepBomLine);
    if (!steps.length) {
      const mats = bls.filter((l) => !isWipAsmLink(l.component_code, l.source_wip || "", l.recipe_seq) && !isStepBomLine(l));
      if (!mats.length) continue;
      nodes.push({
        id: `src-${source}`,
        kind: "Yarı mamül adımı",
        line: {
          id: -1,
          component_code: source,
          component_name: source,
          quantity: 1,
          unit: "AD",
          source_wip: source,
        },
        children: mats.map(materialNode),
      });
      continue;
    }
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i]!;
      const stepSeq = step.recipe_seq ?? (i + 1) * 10;
      const nextSeq = i + 1 < steps.length ? (steps[i + 1]!.recipe_seq ?? (i + 2) * 10) : null;
      const mats = materialLinesForRange(bls, source, stepSeq, nextSeq);
      const name = step.component_name.includes("-")
        ? step.component_name.slice(step.component_name.lastIndexOf("-") + 1).trim()
        : step.component_name;
      nodes.push({
        id: `step-${step.id}-${step.component_code}`,
        kind: "Yarı mamül adımı",
        line: { ...step, component_name: name || step.component_name },
        children: mats.map(materialNode),
      });
    }
  }

  if (productCode && isFgCode(productCode)) {
    nodes.push(fgNode(productCode, productName || productCode));
  }
  return nodes;
}

export function buildBomTree(
  lines: BomLineOut[],
  productCode?: string,
  productName?: string,
  operations: BomOperationRef[] = [],
): BomTreeNode[] {
  if (!lines.length && !operations.length) return [];

  if (operations.length) {
    return buildBomTreeFromOperations(lines, operations, productCode, productName);
  }

  if (lines.length) {
    return buildFromLinesOnly(lines, productCode, productName);
  }

  return [];
}
