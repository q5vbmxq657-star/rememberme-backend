type Draft = { answers: Blob[]; prompts: string; expiresAt: number };

async function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("stay-interview-drafts", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("Close other STAY interview tabs and try again."));
  });
}

export async function draftKey(apiBaseURL: string, token: string): Promise<string> {
  const bytes = new TextEncoder().encode(`${apiBaseURL.replace(/\/$/, "")}\n${token}`);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, "0")).join("");
}

async function transaction<T>(mode: IDBTransactionMode, work: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await database();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction("drafts", mode);
      const request = work(tx.objectStore("drafts"));
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = () => reject(tx.error ?? request.error ?? new Error("The recording could not be saved on this device."));
      tx.onerror = () => reject(tx.error);
    });
  } finally { db.close(); }
}

export async function deleteDraft(key: string): Promise<void> {
  await transaction("readwrite", store => store.delete(key));
}

export async function readDraft(key: string, prompts: string): Promise<Blob[]> {
  const draft = await transaction<Draft | undefined>("readonly", store => store.get(key));
  if (!draft) return [];
  if (draft.prompts !== prompts || !Number.isFinite(draft.expiresAt) || draft.expiresAt <= Date.now()
      || !Array.isArray(draft.answers) || !draft.answers.every(answer => answer instanceof Blob && answer.size > 0)) {
    await deleteDraft(key);
    return [];
  }
  return draft.answers;
}

export async function saveDraft(key: string, prompts: string, expiresAt: number, answers: Blob[]): Promise<void> {
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) throw new Error("This interview link has expired.");
  await transaction("readwrite", store => store.put({ answers, prompts, expiresAt } satisfies Draft, key));
}
