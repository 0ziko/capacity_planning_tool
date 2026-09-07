import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await login(u, p);
      nav("/");
    } catch (ex) {
      setErr((ex as Error).message || "Giriş başarısız");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="panel">
        <h1>Kapasite Planlama</h1>
        <form onSubmit={submit}>
          <input placeholder="Kullanıcı adı" value={u} onChange={(e) => setU(e.target.value)} autoFocus />
          <input placeholder="Şifre" type="password" value={p} onChange={(e) => setP(e.target.value)} />
          {err && <div className="error">{err}</div>}
          <button disabled={busy}>Giriş</button>
        </form>
      </div>
    </div>
  );
}
