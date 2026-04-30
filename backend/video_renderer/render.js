import path from "node:path";
import fs from "node:fs";
import {fileURLToPath, pathToFileURL} from "node:url";

function argValue(name) {
  const idx = process.argv.indexOf(name);
  return idx === -1 ? null : process.argv[idx + 1];
}

const source = argValue("--source");
const plan = argValue("--plan");
const output = argValue("--output");

if (!source || !plan || !output) {
  console.error("Usage: node render.js --source <video> --plan <edit_plan.json> --output <mp4>");
  process.exit(2);
}

let bundle;
let renderMedia;
let selectComposition;

try {
  ({bundle} = await import("@remotion/bundler"));
  ({renderMedia, selectComposition} = await import("@remotion/renderer"));
} catch (error) {
  console.error(
    "Remotion packages are not installed. Run `npm install` in backend/video_renderer. " +
      "The Python backend will use ffmpeg fallback. " +
      String(error?.message || error)
  );
  process.exit(78);
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const entry = path.join(__dirname, "src", "index.jsx");

const bundled = await bundle({
  entryPoint: entry,
  webpackOverride: (config) => config,
});

const inputProps = {
  source: pathToFileURL(path.resolve(source)).href,
  plan: JSON.parse(fs.readFileSync(path.resolve(plan), "utf8")),
};

const composition = await selectComposition({
  serveUrl: bundled,
  id: "EditedVideo",
  inputProps,
});

await renderMedia({
  composition,
  serveUrl: bundled,
  codec: "h264",
  outputLocation: path.resolve(output),
  inputProps,
  imageFormat: "jpeg",
  crf: 20,
  pixelFormat: "yuv420p",
});
