import { copyFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

const assets = [
  ["node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js", "public/vad/vad.worklet.bundle.min.js"],
  ["node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx", "public/vad/silero_vad_v5.onnx"],
  ["node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs", "public/ort/ort-wasm-simd-threaded.mjs"],
  ["node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm", "public/ort/ort-wasm-simd-threaded.wasm"]
];

for (const [source, destination] of assets) {
  const output = join(root, destination);
  await mkdir(dirname(output), { recursive: true });
  await copyFile(join(root, source), output);
}
