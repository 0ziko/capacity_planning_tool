import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { setToken } from "./api";
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
import OwnerPanel from "./pages/OwnerPanel";
import Scenarios from "./pages/Scenarios";
import Stock from "./pages/Stock";
import DataFreshnessBar from "./DataFreshnessBar";
import SidebarNav from "./SidebarNav";

export default function App() {
  const { user, loading, bootstrapError, logout, can } = useAuth();
  if (loading) return <div className="content">Yükleniyor…</div>;
  if (!user) {
    if (bootstrapError) {
      return (
        <div className="content" style={{ maxWidth: 520, margin: "48px auto" }}>
          <div className="error">{bootstrapError}</div>
          <p className="muted">Backend (port 8000) çalışmıyor olabilir veya oturum süresi dolmuş olabilir.</p>
          <button onClick={() => { setToken(null); window.location.href = "/login"; }}>Giriş sayfasına git</button>
        </div>
      );
    }
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="brand">
          Bilge İnox
          <small>İş Gücü Kapasite Planlama</small>
        </div>
        <SidebarNav canAdmin={can("admin")} canOwner={can("owner")} />
        <div className="spacer" />
        <div className="user">
          {user.full_name || user.username} · {user.role}
          <br />
          <button className="secondary small" onClick={logout}>Çıkış</button>
        </div>
      </nav>
      <main className="content main-column">
        <DataFreshnessBar />
        <div className="page-body">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/workcenters" element={<WorkCenters />} />
          <Route path="/employees" element={<Employees />} />
          <Route path="/items" element={<Items />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/scenarios" element={<Scenarios />} />
          <Route path="/planning" element={<Planning />} />
          <Route path="/stock" element={<Stock />} />
          <Route path="/progress" element={<ProgressPage />} />
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/imports" element={<Imports />} />
          {can("admin") && <Route path="/users" element={<Users />} />}
          {can("owner") && <Route path="/owner" element={<OwnerPanel />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </div>
      </main>
    </div>
  );
}
