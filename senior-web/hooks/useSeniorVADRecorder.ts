"use client";

import type { MicVAD } from "@ricky0123/vad-web";
import { useCallback, useEffect, useRef, useState } from "react";

export type SeniorRecorderState =
  | "IDLE" | "PLAYING_PROMPT" | "RECORDING" | "SILENCE_DETECTED"
  | "UPLOADING" | "SUCCESS" | "ERROR";

type RecorderOptions = {
  token: string;
  promptAudioURL: string | null;
  silenceMilliseconds?: number;
  noiseThreshold?: number;
  onSuccess: (message: string) => void;
};

const backendURL = process.env.NEXT_PUBLIC_STAY_API_URL?.replace(/\/$/, "") ?? "";
const maximumRecordingMilliseconds = 20 * 60 * 1000;

export function useSeniorVADRecorder({
  token,
  promptAudioURL,
  silenceMilliseconds = 5000,
  noiseThreshold = 0.018,
  onSuccess
}: RecorderOptions) {
  const [state, setState] = useState<SeniorRecorderState>("IDLE");
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const vadRef = useRef<MicVAD | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const animationRef = useRef<number | null>(null);
  const silenceStartedRef = useRef<number | null>(null);
  const speechHeardRef = useRef(false);
  const stoppingRef = useRef(false);
  const maximumDurationRef = useRef<number | null>(null);

  const cleanup = useCallback(async () => {
    if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
    animationRef.current = null;
    if (maximumDurationRef.current !== null) window.clearTimeout(maximumDurationRef.current);
    maximumDurationRef.current = null;
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    await vadRef.current?.destroy().catch(() => undefined);
    vadRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    await contextRef.current?.close().catch(() => undefined);
    contextRef.current = null;
    setLevel(0);
  }, []);

  useEffect(() => () => { void cleanup(); }, [cleanup]);

  const uploadWithRetry = useCallback(async (blob: Blob) => {
    if (!backendURL) throw new Error("STAY ist gerade nicht erreichbar.");
    const extension = blob.type.includes("webm") ? "webm" : blob.type.includes("mp4") ? "m4a" : "wav";
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const form = new FormData();
      form.append("file", blob, `antwort.${extension}`);
      let terminalError: Error | null = null;
      try {
        const response = await fetch(`${backendURL}/v1/public/podcast/${encodeURIComponent(token)}/upload`, {
          method: "POST",
          body: form,
          cache: "no-store"
        });
        const payload = await response.json().catch(() => ({})) as { detail?: string; message?: string };
        if (response.ok) return payload.message ?? "Vielen Dank! Deine Antwort wurde sicher gespeichert.";
        if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
          terminalError = new Error(payload.detail ?? "Die Aufnahme konnte nicht verarbeitet werden.");
        }
      } catch (uploadError) {
        if (attempt === 3) throw uploadError;
      }
      if (terminalError) throw terminalError;
      await new Promise((resolve) => window.setTimeout(resolve, 1000 * 2 ** attempt));
    }
    throw new Error("Die Verbindung war zu schwach. Bitte versuche es noch einmal.");
  }, [token]);

  const finish = useCallback(async () => {
    if (stoppingRef.current || !recorderRef.current) return;
    stoppingRef.current = true;
    setState("SILENCE_DETECTED");
    const recorder = recorderRef.current;
    const stopped = new Promise<Blob>((resolve) => {
      recorder.addEventListener("stop", () => resolve(new Blob(chunksRef.current, { type: recorder.mimeType })), { once: true });
    });
    if (recorder.state !== "inactive") recorder.stop();
    const blob = await stopped;
    await cleanup();
    try {
      if (blob.size < 1500) throw new Error("Ich konnte noch keine Antwort hören. Bitte sprich etwas länger.");
      setState("UPLOADING");
      const message = await uploadWithRetry(blob);
      setState("SUCCESS");
      onSuccess(message);
    } catch (finishError) {
      setError(finishError instanceof Error ? finishError.message : "Die Antwort konnte nicht gespeichert werden.");
      setState("ERROR");
    } finally {
      stoppingRef.current = false;
    }
  }, [cleanup, onSuccess, uploadWithRetry]);

  const startRecording = useCallback(async (stream: MediaStream, context: AudioContext) => {
    chunksRef.current = [];
    speechHeardRef.current = false;
    silenceStartedRef.current = null;
    const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"]
      .find((candidate) => MediaRecorder.isTypeSupported(candidate));
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorder.addEventListener("dataavailable", (event) => { if (event.data.size > 0) chunksRef.current.push(event.data); });
    recorderRef.current = recorder;
    recorder.start(1000);
    maximumDurationRef.current = window.setTimeout(() => { void finish(); }, maximumRecordingMilliseconds);

    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    const monitor = () => {
      analyser.getFloatTimeDomainData(samples);
      const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
      setLevel(Math.min(1, rms / 0.16));
      if (rms >= noiseThreshold) {
        speechHeardRef.current = true;
        silenceStartedRef.current = null;
      } else if (speechHeardRef.current) {
        silenceStartedRef.current ??= performance.now();
        if (performance.now() - silenceStartedRef.current >= silenceMilliseconds) { void finish(); return; }
      }
      animationRef.current = requestAnimationFrame(monitor);
    };
    monitor();

    const { MicVAD } = await import("@ricky0123/vad-web");
    vadRef.current = await MicVAD.new({
      model: "v5",
      baseAssetPath: "/vad/",
      onnxWASMBasePath: "/ort/",
      startOnLoad: false,
      audioContext: context,
      getStream: async () => stream,
      pauseStream: async () => undefined,
      resumeStream: async () => stream,
      onSpeechStart: () => { speechHeardRef.current = true; silenceStartedRef.current = null; },
      onSpeechEnd: () => { if (speechHeardRef.current) silenceStartedRef.current ??= performance.now(); }
    });
    await vadRef.current.start();
    setState("RECORDING");
  }, [finish, noiseThreshold, silenceMilliseconds]);

  const start = useCallback(() => {
    setError(null);
    stoppingRef.current = false;
    const AudioContextClass = window.AudioContext ?? (window as typeof window & { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const context = new AudioContextClass();
    contextRef.current = context;
    void context.resume();
    const microphonePromise = navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false
    });

    void (async () => {
      try {
        const stream = await microphonePromise;
        streamRef.current = stream;
        if (promptAudioURL) {
          setState("PLAYING_PROMPT");
          const audioData = await fetch(promptAudioURL, { cache: "no-store" }).then((response) => {
            if (!response.ok) throw new Error("Die Frage konnte nicht abgespielt werden.");
            return response.arrayBuffer();
          });
          const buffer = await context.decodeAudioData(audioData);
          const source = context.createBufferSource();
          source.buffer = buffer;
          source.connect(context.destination);
          await new Promise<void>((resolve) => { source.onended = () => resolve(); source.start(); });
        }
        await startRecording(stream, context);
      } catch (startError) {
        await cleanup();
        const denied = startError instanceof DOMException && ["NotAllowedError", "SecurityError"].includes(startError.name);
        setError(denied ? "Bitte erlaube den Mikrofonzugriff in den Browser-Einstellungen und tippe erneut auf Start." : "Die Aufnahme konnte nicht gestartet werden. Bitte versuche es erneut.");
        setState("ERROR");
      }
    })();
  }, [cleanup, promptAudioURL, startRecording]);

  return { state, level, error, start, finish, retry: start };
}
