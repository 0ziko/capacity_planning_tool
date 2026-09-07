import { useState } from "react";
import { api, type Role, type User } from "../api";
import { ErrorText, useAsync } from "../components";

const ROLES: { v: Role; l: string; d: string }[] = [
  { v: "admin", l: "Admin", d: "Kullanıcı yönetimi + tüm yetkiler" },
  { v: "poweruser", l: "Power user", d: "Import, tanımlar, planlama, raporlar" },
  { v: "user", l: "User", d: "Yalnızca görüntüleme ve rapor indirme" },
];

export default function Users() {
  const list = useAsync(() => api.get<User[]>("/api/users"), []);
  const [form, setForm] = useState({ username: "", full_name: "", password: "", role: "user" as Role });
  const [err, setErr] = useState("");
  const create = async () => {
    setErr("");
    try {
      await api.post("/api/users", form);
      setForm({ username: "", full_name: "", password: "", role: "user" });
      list.reload();
    } catch (e) { setErr((e as Error).message); }
  };
  const patch = async (u: User, body: Record<string, unknown>) => {
    try { await api.patch(`/api/users/${u.id}`, body); list.reload(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <>
      <h1>Kullanıcılar & Yetki Matrisi</h1>
      <div className="panel">
        <table style={{ width: "auto" }}>
          <thead><tr><th>Rol</th><th>Yetki</th></tr></thead>
          <tbody>{ROLES.map((r) => <tr key={r.v}><td><b>{r.l}</b></td><td>{r.d}</td></tr>)}</tbody>
        </table>
      </div>
      <div className="panel row">
        <label>Kullanıcı adı<input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
        <label>Ad Soyad<input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
        <label>Şifre<input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
        <label>Rol<select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}</select></label>
        <button onClick={create} disabled={!form.username || form.password.length < 6}>Kullanıcı ekle</button>
      </div>
      <ErrorText err={err || list.err} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Kullanıcı</th><th>Ad Soyad</th><th>Rol</th><th>Aktif</th><th></th></tr></thead>
          <tbody>
            {list.data?.map((u) => (
              <tr key={u.id}>
                <td><b>{u.username}</b></td><td>{u.full_name}</td>
                <td><select value={u.role} onChange={(e) => patch(u, { role: e.target.value })}>{ROLES.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}</select></td>
                <td><input type="checkbox" checked={u.is_active} onChange={(e) => patch(u, { is_active: e.target.checked })} /></td>
                <td><button className="secondary small" onClick={() => { const p = prompt(`${u.username} için yeni şifre (min 6)`); if (p && p.length >= 6) patch(u, { password: p }); }}>Şifre sıfırla</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
