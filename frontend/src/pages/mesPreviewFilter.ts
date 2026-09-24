type PreviewRow = {
  preview_category?: "mapped" | "pending" | "free_stock" | "unresolved";
  action?: string;
};

// Review categories are supplied by the API, using the same rule as its counters.
export function filterMesPreview<T extends PreviewRow>(rows: T[], filter: string): T[] {
  return rows.filter(row => filter === "all" || row.preview_category === filter ||
    filter === "changed" && (row.action === "new" || row.action === "updated"));
}
