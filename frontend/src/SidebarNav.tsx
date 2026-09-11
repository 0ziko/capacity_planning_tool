import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

type NavItem = { to: string; label: string; show?: boolean };

type NavGroupDef = { id: string; title: string; items: NavItem[] };

function NavGroupSection({ id, title, items }: NavGroupDef) {
  const loc = useLocation();
  const visible = items.filter((i) => i.show !== false);
  const active = visible.some((i) =>
    i.to === "/" ? loc.pathname === "/" : loc.pathname === i.to || loc.pathname.startsWith(`${i.to}/`),
  );
  const [open, setOpen] = useState(active);

  useEffect(() => {
    if (active) setOpen(true);
  }, [active]);

  if (!visible.length) return null;

  return (
    <div className={`nav-group${open ? " open" : ""}`}>
      <button type="button" className="nav-group-title" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span>{title}</span>
        <span className="nav-chevron" aria-hidden>›</span>
      </button>
      <div className="nav-group-items">
        {visible.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-sub">
            {item.label}
          </NavLink>
        ))}
      </div>
    </div>
  );
}

export default function SidebarNav({ canAdmin, canOwner }: { canAdmin: boolean; canOwner: boolean }) {
  const groups: NavGroupDef[] = [
    {
      id: "master",
      title: "Temel Tanımlar",
      items: [
        { to: "/items", label: "Stok / BOM / Rota" },
        { to: "/workcenters", label: "İş Merkezleri" },
        { to: "/employees", label: "Personel Tanımları" },
        { to: "/users", label: "Kullanıcılar", show: canAdmin },
        { to: "/owner", label: "Owner Panel", show: canOwner },
      ],
    },
    {
      id: "planning",
      title: "Planlama Süreç Yönetimi",
      items: [
        { to: "/planning", label: "Kapasite Planlama" },
        { to: "/scenarios", label: "Senaryo Matrisi" },
        { to: "/orders", label: "Sipariş Yönetimi" },
        { to: "/stock", label: "Stok & Rezervasyon" },
      ],
    },
    {
      id: "mes",
      title: "MES Entegrasyonu",
      items: [
        { to: "/progress", label: "Günlük İlerleme" },
        { to: "/analysis", label: "Duruş & Çevrim Süresi" },
        { to: "/imports", label: "Excel Import / Yedek" },
      ],
    },
  ];

  return (
    <>
      <NavLink to="/" end className="nav-top">
        Özet
      </NavLink>
      {groups.map((g) => (
        <NavGroupSection key={g.id} {...g} />
      ))}
    </>
  );
}
