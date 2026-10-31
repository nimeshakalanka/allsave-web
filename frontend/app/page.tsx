import Downloader from "@/components/Downloader";

export default function Home() {
  return (
    <div className="relative flex min-h-dvh flex-col overflow-x-hidden">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 -top-40 mx-auto h-96 max-w-3xl rounded-full bg-gradient-to-r from-violet-600/25 via-fuchsia-500/20 to-sky-500/10 blur-3xl" />
      <main className="relative mx-auto w-full max-w-2xl flex-1 px-4 pb-10 pt-12 sm:pt-20">
        <header className="mb-8 text-center">
          <div className="mb-4 inline-flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500 text-lg font-bold text-white shadow-lg shadow-fuchsia-500/30">A</span>
            <span className="text-xl font-semibold tracking-tight">AllSave</span>
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-white sm:text-4xl">Save videos in one tap</h1>
          <p className="mt-3 text-sm text-zinc-400 sm:text-base">TikTok, YouTube, Instagram and Facebook: video in your quality, or MP3 audio.</p>
        </header>
        <Downloader />
      </main>
      <footer className="relative border-t border-white/5 px-4 py-6 text-center text-xs text-zinc-500">
        For personal use. Respect creators&apos; rights and platform terms.
      </footer>
    </div>
  );
}
