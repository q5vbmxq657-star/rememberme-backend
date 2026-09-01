import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SeniorRecorder } from "@/components/SeniorRecorder";

type PodcastMetadata = {
  requester_name: string;
  subject_name: string;
  prompt: string;
  prompt_audio_url: string | null;
  theme: string;
  prompts: Array<{
    prompt_id: string;
    category: string;
    question: string;
    audio_url: string | null;
  }>;
};

const apiURL = process.env.STAY_API_URL?.replace(/\/$/, "") ?? process.env.NEXT_PUBLIC_STAY_API_URL?.replace(/\/$/, "") ?? "";

async function loadMetadata(token: string): Promise<PodcastMetadata | null> {
  if (!apiURL) throw new Error("STAY_API_URL is not configured.");
  const response = await fetch(`${apiURL}/v1/public/podcast/${encodeURIComponent(token)}`, {
    cache: "no-store",
    headers: { Accept: "application/json" }
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("Podcast invitation lookup failed.");
  return response.json() as Promise<PodcastMetadata>;
}

export async function generateMetadata({ params }: { params: Promise<{ token: string }> }): Promise<Metadata> {
  const { token } = await params;
  const metadata = await loadMetadata(token).catch(() => null);
  if (!metadata) return { title: "Persönliche Frage | STAY", robots: { index: false, follow: false } };
  const title = `${metadata.requester_name} lädt dich zu einem privaten Gespräch ein`;
  return {
    title,
    description: `Erzähle deine Geschichte für ${metadata.subject_name}. Keine App und kein Konto nötig.`,
    robots: { index: false, follow: false },
    openGraph: {
      title,
      description: `Erzähle deine Geschichte für ${metadata.subject_name}. Keine App und kein Konto nötig.`,
      type: "website"
    }
  };
}

export default async function PodcastPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const metadata = await loadMetadata(token);
  if (!metadata) notFound();
  return <SeniorRecorder token={token} apiBaseURL={apiURL} metadata={metadata} />;
}
