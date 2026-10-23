export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const { next = "/", error } = await searchParams;
  return (
    <div className="card" style={{ maxWidth: 380, margin: "60px auto" }}>
      <h1>Sign in</h1>
      <p className="muted">Enter the team passcode to open Freshroots Demand Planning.</p>
      <form method="post" action="/auth/login" className="grid" style={{ gap: 10 }}>
        <input type="hidden" name="next" value={next} />
        <input type="password" name="passcode" placeholder="Passcode" autoFocus required />
        {error && <div className="error">That passcode is not right.</div>}
        <button className="primary" type="submit">Sign in</button>
      </form>
    </div>
  );
}
