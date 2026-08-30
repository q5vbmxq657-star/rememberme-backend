"use client";

import { useCallback, useState } from "react";
import { AudioWave } from "@/components/AudioWave";
import { useSeniorVADRecorder } from "@/hooks/useSeniorVADRecorder";

type Metadata = {
  requester_name: string;
  subject_name: string;
  prompt: string;
  prompt_audio_url: string | null;
};

export function SeniorRecorder({ token, metadata }: { token: string; metadata: Metadata }) {
  const [successMessage, setSuccessMessage] = useState("");
  const onSuccess = useCallback((message: string) => setSuccessMessage(message), []);
  const recorder = useSeniorVADRecorder({ token, promptAudioURL: metadata.prompt_audio_url, onSuccess });
  const busy = ["PLAYING_PROMPT", "RECORDING", "SILENCE_DETECTED", "UPLOADING"].includes(recorder.state);

  if (recorder.state === "SUCCESS") {
    return (
      <main className="flex min-h-[100svh] items-center justify-center px-6 py-10 text-center">
        <section className="w-full max-w-md" aria-live="polite">
          <div className="mx-auto mb-8 grid h-28 w-28 place-items-center rounded-full bg-emerald-100 text-6xl text-emerald-700" aria-hidden="true">✓</div>
          <h1 className="text-4xl font-bold tracking-tight text-zinc-950">Vielen Dank!</h1>
          <p className="mt-5 text-2xl font-semibold leading-snug text-zinc-700">{successMessage}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="flex min-h-[100svh] items-center justify-center px-6 py-8 text-center">
      <section className="flex w-full max-w-md flex-col items-center">
        <div className="mb-6 grid h-24 w-24 place-items-center rounded-full bg-[#ffe8e2] text-4xl font-bold text-[#a42d2a]" aria-hidden="true">
          {metadata.requester_name.trim().charAt(0).toUpperCase()}
        </div>
        <p className="text-xl font-semibold text-zinc-600">{metadata.requester_name} möchte dich etwas fragen</p>
        <h1 className="mt-5 text-[clamp(1.75rem,7vw,2.5rem)] font-bold leading-tight tracking-tight text-zinc-950">{metadata.prompt}</h1>

        <div className="mt-8 h-24 w-full">
          {busy && <AudioWave level={recorder.level} active={recorder.state === "PLAYING_PROMPT" || recorder.state === "RECORDING"} />}
        </div>

        <p className="min-h-16 text-2xl font-bold text-zinc-800" aria-live="polite">
          {recorder.state === "PLAYING_PROMPT" && "Hör kurz zu …"}
          {recorder.state === "RECORDING" && "Ich höre zu …"}
          {recorder.state === "SILENCE_DETECTED" && "Antwort wird vorbereitet …"}
          {recorder.state === "UPLOADING" && "Antwort wird sicher gespeichert …"}
          {recorder.state === "ERROR" && recorder.error}
        </p>

        {(recorder.state === "IDLE" || recorder.state === "ERROR") && (
          <button
            type="button"
            onClick={recorder.state === "ERROR" ? recorder.retry : recorder.start}
            className="mt-4 min-h-24 w-full rounded-3xl bg-[#ef6558] px-8 text-3xl font-bold text-white shadow-[0_12px_30px_rgba(201,64,54,0.28)] transition active:scale-[0.98] focus:outline-none focus-visible:ring-4 focus-visible:ring-[#8f211f] focus-visible:ring-offset-4"
          >
            {recorder.state === "ERROR" ? "Noch einmal" : "Start"}
          </button>
        )}

        {recorder.state === "RECORDING" && (
          <button
            type="button"
            onClick={() => void recorder.finish()}
            className="mt-4 min-h-20 w-full rounded-3xl border-4 border-zinc-900 bg-white px-8 text-2xl font-bold text-zinc-950 active:scale-[0.98] focus:outline-none focus-visible:ring-4 focus-visible:ring-[#8f211f]"
          >
            Fertig
          </button>
        )}
      </section>
    </main>
  );
}
