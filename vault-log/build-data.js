#!/usr/bin/env node
/* ----------------------------------------------------------------------------
 * build-data.js — atualiza o dataset da aplicação a partir de um arquivo de log.
 *
 * Uso:
 *   node build-data.js <arquivo-de-log>
 *
 * Aceita:
 *   - .rtf  (export do Terminal/macOS) — o ruído de Envoy/consul-dataplane é
 *           descartado e só as linhas de log válidas (JSON e acessos INFO:) ficam.
 *   - .txt / .log / .jsonl — texto puro, uma linha de log por linha.
 *
 * Gera/atualiza:
 *   - sample-logs.txt  (linhas limpas, em texto puro)
 *   - logs-data.js     (mesmas linhas embutidas no app)
 *
 * Depois é só recarregar o index.html no navegador.
 * ------------------------------------------------------------------------- */

const fs = require("fs");
const path = require("path");

const input = process.argv[2];
if (!input) {
  console.error("Uso: node build-data.js <arquivo-de-log .rtf|.txt|.log|.jsonl>");
  process.exit(1);
}

const raw = fs.readFileSync(input, "utf8");
const isRtf = input.toLowerCase().endsWith(".rtf") || raw.startsWith("{\\rtf");
const out = [];

for (let line of raw.split(/\r?\n/)) {
  line = line.replace(/\s+$/, "");
  if (isRtf && line.endsWith("\\")) line = line.slice(0, -1); // RTF line-break
  line = line.trim();
  if (!line) continue;

  if (isRtf) {
    const jsonIdx = line.indexOf('\\{"');
    if (jsonIdx !== -1) {
      let s = line.slice(jsonIdx).replace(/\\\{/g, "{").replace(/\\\}/g, "}").replace(/\\\\/g, "\\");
      try {
        JSON.parse(s);
        out.push(s);
      } catch (e) {
        console.error("ignorado (json inválido):", s.slice(0, 70));
      }
      continue;
    }
    if (line.indexOf("INFO:") === 0 && line.includes("HTTP/1.1")) out.push(line);
    continue;
  }

  // Texto puro: mantém linhas JSON e acessos INFO:, ignora o resto.
  if (line.startsWith("{") || /^\d{4}-\d{2}-\d{2}T/.test(line) || line.startsWith("INFO:")) {
    out.push(line);
  }
}

if (!out.length) {
  console.error("Nenhuma linha de log reconhecida em", input);
  process.exit(2);
}

const text = out.join("\n");
const dir = __dirname;

fs.writeFileSync(path.join(dir, "sample-logs.txt"), text + "\n");

if (text.includes("`") || text.includes("${")) {
  console.error("Aviso: o log contém ` ou ${ — incompatível com String.raw. Ajuste manual necessário.");
  process.exit(3);
}
const header =
  "/* Logs embutidos para a aplicação funcionar via servidor local.\n" +
  " * Gerado por build-data.js. String.raw preserva os escapes \\n do JSON. */\n";
fs.writeFileSync(path.join(dir, "logs-data.js"), header + "const SAMPLE_LOGS = String.raw`" + text + "`;\n");

console.log(`OK: ${out.length} linhas -> sample-logs.txt e logs-data.js atualizados.`);
console.log("Recarregue o index.html no navegador.");
