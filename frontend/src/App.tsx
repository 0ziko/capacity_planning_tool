import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import WorkCenters from "./pages/WorkCenters";
import Employees from "./pages/Employees";
import Items from "./pages/Items";
import Orders from "./pages/Orders";
import Planning from "./pages/Planning";
import ProgressPage from "./pages/Progress";
import Analysis from "./pages/Analysis";
import Imports from "./pages/Imports";
import Users from "./pages/Users";

export default function App() {
  const { user, loading, logout, can } = useAuth();
  if (loading) return <div className="content">Yükleniyor…</div>;
  if (!user)
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );

  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="brand">
          Bilge İnox
          <small>İş Gücü Kapasite Planlama</small>
        </div>
        <NavLink to="/">Özet</NavLink>
        <NavLink to="/workcenters">İş Merkezleri</NavLink>
        <NavLink to="/employees">Personel</NavLink>
        <NavLink to="/items">Stok / BOM / Rota</NavLink>
        <NavLink to="/orders">Siparişler & İhtiyaç</NavLink>
        <NavLink to="/planning">Planlama</NavLink>
        <NavLink to="/progress">Günlük İlerleme</NavLink>
        <NavLink to="/analysis">Duruş & Çevrim Süresi</NavLink>
        <NavLink to="/imports">Excel Import / Yedek</NavLink>
        {can("admin") && <NavLink to="/users">Kullanıcılar</NavLink>}
        <div className="spacer" />
        <div className="user">
          {user.full_name || user.username} · {user.role}
          <br />
          <button className="secondary small" onClick={logout}>Çıkış</button>
        </div>
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/workcenters" element={<WorkCenters />} />
          <Route path="/employees" element={<Employees />} />
          <Route path="/items" element={<Items />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/planning" element={<Planning />} />
          <Route path="/progress" element={<ProgressPage />} />
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/imports" element={<Imports />} />
          {can("admin") && <Route path="/users" element={<Users />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
