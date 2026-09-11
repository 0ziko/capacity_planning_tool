import { useState } from "react";
import { fmt, type BomLineOut } from "../api";
import { bomKindBadge, buildBomTree, type BomOperationRef, type BomTreeNode } from "../bomTree";

function NodeRow({
  node,
  qty,
  depth,
  open,
  onToggle,
}: {
  node: BomTreeNode;
  qty: number;
  depth: number;
  open: boolean;
  onToggle: () => void;
}) {
  const hasChildren = node.children.length > 0;
  const totalQty = node.line.quantity * qty;
  const dashIdx = node.line.component_name.lastIndexOf("-");
  const stepLabel =
    node.kind === "Yarı mamül adımı" && dashIdx >= 0 ? node.line.component_name.slice(dashIdx + 1).trim() : "";

  return (
    <div className={`bom-tree-row depth-${Math.min(depth, 5)} kind-${node.kind.replace(/\s+/g, "-")}`}>
      {hasChildren ? (
        <button type="button" className="bom-tree-toggle" onClick={onToggle} aria-expanded={open} aria-label={open ? "Daralt" : "Genişlet"}>
          {open ? "▾" : "▸"}
        </button>
      ) : (
        <span className="bom-tree-toggle spacer" />
      )}
      <span className={`badge ${bomKindBadge(node.kind)}`}>{node.kind}</span>
      {node.kind === "Yarı mamül adımı" && node.line.recipe_seq != null && (
        <span className="muted bom-tree-seq">{node.line.recipe_seq}</span>
      )}
      <code className="bom-tree-code">{node.line.component_code}</code>
      <span className="bom-tree-name" title={node.line.component_name}>
        {node.kind === "Yarı mamül adımı" && stepLabel ? stepLabel : node.line.component_name}
      </span>
      <span className="bom-tree-qty">
        {fmt(node.line.quantity, 3)} {node.line.unit}
        {qty !== 1 && <span className="muted"> → {fmt(totalQty, 3)}</span>}
      </span>
    </div>
  );
}

function BomTreeBranch({ node, qty, depth = 0 }: { node: BomTreeNode; qty: number; depth?: number }) {
  const [open, setOpen] = useState(depth < 3);
  const hasChildren = node.children.length > 0;

  return (
    <li className="bom-tree-node">
      <NodeRow node={node} qty={qty} depth={depth} open={open} onToggle={() => setOpen((v) => !v)} />
      {hasChildren && open && (
        <ul className="bom-tree-children">
          {node.children.map((child) => (
            <BomTreeBranch key={child.id} node={child} qty={qty} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

function countNodes(nodes: BomTreeNode[]): { branches: number; components: number; steps: number; materials: number } {
  let branches = 0;
  let components = 0;
  let steps = 0;
  let materials = 0;
  const walk = (n: BomTreeNode) => {
    if (n.kind === "Mamul" || n.kind === "Bitirme") branches += 1;
    else if (n.kind === "Bileşen") components += 1;
    else if (n.kind === "Yarı mamül adımı") steps += 1;
    else if (n.kind === "Hammadde" || n.kind === "Alt yarı mamül") materials += 1;
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return { branches, components, steps, materials };
}

export default function BomTreeView({
  lines,
  qty,
  productCode,
  productName,
  operations = [],
}: {
  lines: BomLineOut[];
  qty: number;
  productCode?: string;
  productName?: string;
  operations?: BomOperationRef[];
}) {
  const forest = buildBomTree(lines, productCode, productName, operations);
  const stats = countNodes(forest);

  if (!forest.length) {
    return <p className="muted">BOM yok</p>;
  }

  return (
    <div className="bom-tree-panel">
      <div className="bom-tree-legend">
        <span className="badge info">Yarı mamül adımı</span>
        <span className="badge muted">Hammadde</span>
        <span className="badge ok">Mamul</span>
        <span className="bom-tree-stats muted">
          {stats.steps} adım · {stats.materials} malzeme
        </span>
      </div>

      <ul className="bom-tree-forest">
        {forest.map((root) => (
          <BomTreeBranch key={root.id} node={root} qty={qty} depth={0} />
        ))}
      </ul>

      <p className="muted bom-tree-foot">
        Sıra operasyon listesi ile aynıdır. Her yarı mamulün altında o adımda tüketilen
        hammaddeler yer alır; mamul en altta oluşur.
      </p>
    </div>
  );
}
