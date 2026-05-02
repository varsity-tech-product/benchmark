const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..', '..');
const webSource = process.env.QTB_WEB_SOURCE_DIR
  ? path.resolve(process.env.QTB_WEB_SOURCE_DIR)
  : path.join(repoRoot, 'bench', 'server', 'web');
const outputDir = process.env.QTB_VERCEL_OUTPUT_DIR
  ? path.resolve(process.env.QTB_VERCEL_OUTPUT_DIR)
  : path.join(__dirname, '..', 'public');

const templatePath = path.join(webSource, 'templates', 'index.html');
const staticPath = path.join(webSource, 'static');
const outputStaticPath = path.join(outputDir, 'static');
const outputIndexPath = path.join(outputDir, 'index.html');

function ensurePathExists(target, label) {
  if (!fs.existsSync(target)) {
    throw new Error(`${label} does not exist: ${target}`);
  }
}

function injectPreviewFlag(html) {
  if (process.env.VERCEL_ENV !== 'preview') {
    return html;
  }
  const marker = '</head>';
  const snippet = [
    '  <script>',
    '    window.QTB = window.QTB || {};',
    '    window.QTB.staticPreview = true;',
    '  </script>',
  ].join('\n');
  if (!html.includes(marker)) {
    return `${snippet}\n${html}`;
  }
  return html.replace(marker, `${snippet}\n${marker}`);
}

ensurePathExists(templatePath, 'UI template');
ensurePathExists(staticPath, 'UI static directory');

fs.mkdirSync(outputDir, { recursive: true });
fs.rmSync(outputStaticPath, { recursive: true, force: true });
fs.cpSync(staticPath, outputStaticPath, { recursive: true });

const indexHtml = fs.readFileSync(templatePath, 'utf8');
fs.writeFileSync(outputIndexPath, injectPreviewFlag(indexHtml));

console.log(`Copied QuantTutorBench UI assets to ${outputDir}`);
