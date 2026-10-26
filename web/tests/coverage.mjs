// Convert Chromium's execution ranges back to repository sources, excluding dependencies.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import v8toIstanbul from 'v8-to-istanbul';
import coverageLibrary from 'istanbul-lib-coverage';
import reportLibrary from 'istanbul-lib-report';
import reports from 'istanbul-reports';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const merged = coverageLibrary.createCoverageMap({});
for (const file of fs.readdirSync(path.join(root, 'coverage/raw'))) {
  for (const script of JSON.parse(fs.readFileSync(path.join(root, 'coverage/raw', file), 'utf8'))) {
    if (!script.url.includes('/_app/immutable/') || !script.url.endsWith('.js')) continue;
    const local = path.join(root, 'build', new URL(script.url).pathname);
    if (!fs.existsSync(local + '.map')) continue;
    const converter = v8toIstanbul(local);
    await converter.load();
    converter.applyCoverage(script.functions);
    const map = converter.toIstanbul();
    for (const [name, data] of Object.entries(map)) {
      if (name.startsWith(path.join(root, 'src') + path.sep)) merged.addFileCoverage(data);
    }
  }
}
if (!merged.files().length) throw new Error('No application coverage was collected');
const context = reportLibrary.createContext({ dir: path.join(root, 'coverage'), coverageMap: merged });
for (const kind of ['text', 'html', 'json-summary']) reports.create(kind).execute(context);
const summary = merged.getCoverageSummary();
if (summary.statements.pct < 70) {
  throw new Error(`Application statement coverage ${summary.statements.pct}% is below 70%`);
}
