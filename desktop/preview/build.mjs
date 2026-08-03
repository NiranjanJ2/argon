// Render argon.jsx to a static HTML page so the design can actually be looked
// at. Übersicht supplies React + emotion; here a ~40-line `h` shim turns the
// same JSX into an HTML string, and the emotion className becomes a nested CSS
// block (natively supported by the Chromium Playwright drives).
import { readFileSync, writeFileSync } from "node:fs";
import { transformSync } from "esbuild";

const SRC = process.argv[2];
const OUT = process.argv[3];
const FIXTURES = JSON.parse(readFileSync(process.argv[4], "utf8"));

const kebab = (s) => s.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const VOID = new Set(["path", "br", "img"]);
// A String object marks "already markup"; primitive strings are text to escape.
const raw = (s) => new String(s);

function h(tag, props, ...kids) {
  props = props || {};
  if (typeof tag === "function") return tag({ ...props, children: kids.flat() });
  const attrs = Object.entries(props)
    .filter(([k, val]) => k !== "children" && k !== "key" && val != null && val !== false)
    .map(([k, val]) => {
      if (k === "style" && typeof val === "object") {
        // React appends px to bare numbers for length properties; without this
        // the preview silently drops the declaration as invalid CSS.
        const css = Object.entries(val)
          .map(([p, q]) => `${kebab(p)}:${typeof q === "number" ? q + "px" : q}`)
          .join(";");
        return `style="${esc(css)}"`;
      }
      return `${k === "className" ? "class" : k}="${esc(val)}"`;
    })
    .join(" ");
  const open = attrs ? `<${tag} ${attrs}>` : `<${tag}>`;
  if (VOID.has(tag)) return raw(open);
  // React escapes text children. Without this an error string like
  // "<urlopen error ...>" parses as an unknown tag and renders as nothing.
  // Markup is tagged rather than sniffed — a "starts with <" heuristic fails
  // on exactly that string, which is how this went unnoticed the first time.
  const inner = kids
    .flat(Infinity)
    .filter((c) => c != null && c !== false)
    .map((c) => (c instanceof String ? c : esc(c)))
    .join("");
  return raw(`${open}${inner}</${tag}>`);
}

// Übersicht module semantics: strip the exports, keep the bindings.
const source = readFileSync(SRC, "utf8").replace(/^export const /gm, "const ");
const { code } = transformSync(source, {
  loader: "jsx",
  jsxFactory: "h",
  jsxFragment: '"div"',
  format: "cjs",
});

const module = new Function("h", "module", "exports", code + "\n;return {className, render};");
const { className, render } = module(h, {}, {});

const cards = FIXTURES.map(
  ({ name, data }) => `
  <figure>
    <figcaption>${esc(name)}</figcaption>
    <div class="argon-widget">${render({ output: JSON.stringify(data) })}</div>
  </figure>`
).join("\n");

writeFileSync(
  OUT,
  `<!doctype html><meta charset="utf-8"><title>Argon widget preview</title>
<style>
  body { margin:0; padding:34px; background:#12161d;
         font-family:-apple-system,BlinkMacSystemFont,sans-serif;
         display:flex; gap:34px; align-items:flex-start; flex-wrap:wrap; }
  figure { margin:0; }
  figcaption { color:#6b7684; font-size:11px; letter-spacing:1.4px;
               text-transform:uppercase; margin-bottom:11px; }
  /* Übersicht applies the widget className to the wrapper. The top/right in it
     are for absolute desktop placement and would break a side-by-side sheet. */
  .argon-widget { position:relative !important; top:auto !important;
                  right:auto !important; ${className} }
</style>
${cards}`
);
console.log("wrote " + OUT);
