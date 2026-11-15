function App() {
  return (
    <main className="grid min-h-dvh place-items-center px-6 py-12">
      <section className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-8">
        <h1 className="text-3xl font-semibold tracking-tight">ExoCortex</h1>
        <p className="mt-3 text-slate-300">Интерфейс чатов и веток в разработке.</p>
        <a
          className="mt-6 inline-flex rounded-lg bg-slate-100 px-4 py-2 font-medium text-slate-950 hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-slate-100"
          href="/api/v1/chats"
        >
          Проверить API чатов
        </a>
      </section>
    </main>
  )
}

export default App
