"use client";

import { useCallback, useState } from "react";
import { AudioWave } from "@/components/AudioWave";
import { type PodcastPrompt, useSeniorVADRecorder } from "@/hooks/useSeniorVADRecorder";

type Metadata = {
  requester_name: string;
  subject_name: string;
  prompt: string;
  prompt_audio_url: string | null;
  prompts: PodcastPrompt[];
};

export function SeniorRecorder({
  token,
  apiBaseURL,
  metadata
}: {
  token: string;
  apiBaseURL: string;
  metadata: Metadata;
}) {
  const [successMessage, setSuccessMessage] = useState("");
  const [speakerConfirmedSubject, setSpeakerConfirmedSubject] = useState(false);
  const [voiceTrainingConsent, setVoiceTrainingConsent] = useState(false);
  const onSuccess = useCallback((message: string) => setSuccessMessage(message), []);
  const prompts = metadata.prompts.length > 0 ? metadata.prompts : [{
    prompt_id: "legacy_prompt",
    category: "life_story",
    question: metadata.prompt,
    audio_url: metadata.prompt_audio_url
  }];
  const recorder = useSeniorVADRecorder({ token, apiBaseURL, prompts, onSuccess });
  const currentPrompt = prompts[recorder.currentPromptIndex] ?? prompts[0];
  const busy = ["PLAYING_PROMPT", "RECORDING", "SILENCE_DETECTED", "UPLOADING"].includes(recorder.state);

  if (recorder.state === "SUCCESS") {
    return (
      <main className="flex min-h-[100svh] items-center justify-center bg-[#fffaf9] px-6 py-10 text-center">
        <section className="w-full max-w-md" aria-live="polite">
          <div className="mx-auto mb-8 grid h-28 w-28 place-items-center rounded-full bg-emerald-100 text-6xl text-emerald-700" aria-hidden="true">✓</div>
          <p className="text-lg font-bold uppercase tracking-[0.18em] text-[#d85048]">Sicher angekommen</p>
          <h1 className="mt-3 text-4xl font-bold tracking-tight text-zinc-950">Danke für deine Geschichte.</h1>
          <p className="mt-5 text-2xl font-semibold leading-snug text-zinc-700">{successMessage}</p>
          {voiceTrainingConsent && (
            <p className="mt-6 rounded-3xl bg-white p-5 text-xl font-semibold leading-snug text-zinc-700 shadow-sm">
              Deine Stimme darf in STAY als mögliche Stimmvorlage angezeigt werden.
            </p>
          )}
        </section>
      </main>
    );
  }

  if (recorder.state === "CONSENT") {
    return (
      <main className="flex min-h-[100svh] items-center justify-center bg-[#fffaf9] px-6 py-8">
        <section className="w-full max-w-md">
          <div className="mx-auto grid h-24 w-24 place-items-center rounded-full bg-emerald-100 text-5xl font-bold text-emerald-700" aria-hidden="true">✓</div>
          <p className="mt-7 text-center text-lg font-bold uppercase tracking-[0.16em] text-[#d85048]">{prompts.length} Antworten aufgenommen</p>
          <h1 className="mt-3 text-center text-4xl font-bold tracking-tight text-zinc-950">Deine Geschichte ist bereit.</h1>
          <p className="mt-4 text-center text-xl leading-relaxed text-zinc-600">
            Sie wird als persönliche Erinnerungen für {metadata.subject_name} gespeichert.
          </p>

          <div className="mt-8 rounded-3xl border-2 border-zinc-200 bg-white p-5 shadow-sm">
            <label className="flex min-h-16 cursor-pointer items-center gap-4 text-xl font-bold text-zinc-900">
              <input
                type="checkbox"
                checked={speakerConfirmedSubject}
                onChange={(event) => {
                  setSpeakerConfirmedSubject(event.target.checked);
                  if (!event.target.checked) setVoiceTrainingConsent(false);
                }}
                className="h-8 w-8 shrink-0 accent-[#e85650]"
              />
              Ich bin {metadata.subject_name}.
            </label>

            <div className="my-4 h-px bg-zinc-200" />

            <label className={`flex min-h-20 items-start gap-4 text-xl font-bold ${speakerConfirmedSubject ? "cursor-pointer text-zinc-900" : "text-zinc-400"}`}>
              <input
                type="checkbox"
                checked={voiceTrainingConsent}
                disabled={!speakerConfirmedSubject}
                onChange={(event) => setVoiceTrainingConsent(event.target.checked)}
                className="mt-1 h-8 w-8 shrink-0 accent-[#e85650]"
              />
              <span>
                Meine Aufnahme darf helfen, meine Avatar-Stimme zu erstellen.
                <span className="mt-2 block text-base font-medium leading-relaxed text-zinc-500">Optional. Die Familie entscheidet später, ob sie die Aufnahme dafür verwendet.</span>
              </span>
            </label>
          </div>

          {recorder.error && (
            <p className="mt-5 rounded-3xl bg-red-50 p-5 text-center text-lg font-bold leading-relaxed text-red-800" role="alert">
              {recorder.error}
            </p>
          )}

          <button
            type="button"
            onClick={() => void recorder.submit(speakerConfirmedSubject, voiceTrainingConsent)}
            className="mt-7 min-h-20 w-full rounded-3xl bg-[#ef6558] px-8 text-2xl font-bold text-white shadow-[0_12px_30px_rgba(201,64,54,0.24)] active:scale-[0.98] focus:outline-none focus-visible:ring-4 focus-visible:ring-[#8f211f]"
          >
            Sicher speichern
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="flex min-h-[100svh] items-center justify-center bg-[#fffaf9] px-6 py-8 text-center">
      <section className="flex w-full max-w-md flex-col items-center">
        <div className="mb-5 grid h-24 w-24 place-items-center rounded-full bg-[#ffe8e2] text-4xl font-bold text-[#a42d2a]" aria-hidden="true">
          {metadata.requester_name.trim().charAt(0).toUpperCase()}
        </div>
        <p className="text-xl font-semibold text-zinc-600">{metadata.requester_name} lädt dich zu einem privaten Gespräch ein</p>

        {recorder.state === "IDLE" || recorder.state === "ERROR" ? (
          <>
            <h1 className="mt-5 text-[clamp(2rem,8vw,2.75rem)] font-bold leading-tight tracking-tight text-zinc-950">Erzähl deine Geschichte.</h1>
            <p className="mt-4 text-xl leading-relaxed text-zinc-600">Drei Fragen. Sprich in deinem Tempo. Eine längere Pause beendet jeweils deine Antwort.</p>
          </>
        ) : (
          <>
            <p className="mt-5 text-lg font-bold uppercase tracking-[0.16em] text-[#d85048]">
              Frage {recorder.currentPromptIndex + 1} von {recorder.totalPrompts}
            </p>
            <h1 className="mt-3 text-[clamp(1.75rem,7vw,2.5rem)] font-bold leading-tight tracking-tight text-zinc-950">{currentPrompt.question}</h1>
          </>
        )}

        <div className="mt-7 flex w-full gap-2" aria-label={`${recorder.completedTurns} von ${recorder.totalPrompts} Antworten aufgenommen`}>
          {prompts.map((prompt, index) => (
            <div
              key={prompt.prompt_id}
              className={`h-2 flex-1 rounded-full transition-colors ${index < recorder.completedTurns ? "bg-emerald-500" : index === recorder.currentPromptIndex && busy ? "bg-[#ef6558]" : "bg-zinc-200"}`}
            />
          ))}
        </div>

        <div className="mt-6 h-24 w-full">
          {busy && <AudioWave level={recorder.level} active={recorder.state === "PLAYING_PROMPT" || recorder.state === "RECORDING"} />}
        </div>

        <p className="min-h-16 text-2xl font-bold text-zinc-800" aria-live="polite">
          {recorder.state === "PLAYING_PROMPT" && "Hör kurz zu …"}
          {recorder.state === "RECORDING" && "Ich höre zu …"}
          {recorder.state === "SILENCE_DETECTED" && "Danke …"}
          {recorder.state === "UPLOADING" && "Deine Geschichte wird sicher gespeichert …"}
          {recorder.state === "ERROR" && recorder.error}
        </p>

        {(recorder.state === "IDLE" || recorder.state === "ERROR") && (
          <button
            type="button"
            onClick={recorder.state === "ERROR" ? recorder.retry : recorder.start}
            className="mt-4 min-h-24 w-full rounded-3xl bg-[#ef6558] px-8 text-3xl font-bold text-white shadow-[0_12px_30px_rgba(201,64,54,0.28)] active:scale-[0.98] focus:outline-none focus-visible:ring-4 focus-visible:ring-[#8f211f] focus-visible:ring-offset-4"
          >
            {recorder.state === "ERROR" ? "Noch einmal" : "Gespräch starten"}
          </button>
        )}

        {recorder.state === "RECORDING" && (
          <button
            type="button"
            onClick={() => void recorder.finish()}
            className="mt-4 min-h-20 w-full rounded-3xl border-4 border-zinc-900 bg-white px-8 text-2xl font-bold text-zinc-950 active:scale-[0.98] focus:outline-none focus-visible:ring-4 focus-visible:ring-[#8f211f]"
          >
            Antwort fertig
          </button>
        )}
      </section>
    </main>
  );
}
