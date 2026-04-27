/**
 * Корневой placeholder. Реальная навигация появится в Phase 4.2 (auth +
 * dashboard). До тех пор — статичная страница с указанием куда смотреть
 * для smoke-проверки.
 */
export default function HomePage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-24">
      <h1 className="text-4xl font-semibold tracking-tight">AutoTreeGen</h1>
      <p className="mt-4 text-lg text-[color:var(--color-ink-500)]">
        Read-only tree view (Phase 4.1) — coming soon.
      </p>
      <p className="mt-8 text-sm text-[color:var(--color-ink-500)]">
        For local smoke: open{" "}
        <code className="rounded bg-[color:var(--color-surface-muted)] px-1.5 py-0.5">
          /trees/&lt;tree-id&gt;/persons
        </code>{" "}
        once the persons-list page lands.
      </p>
    </main>
  );
}
