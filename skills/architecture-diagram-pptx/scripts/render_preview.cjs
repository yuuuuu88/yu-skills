#!/usr/bin/env node
/*
 * Render the diagram contract to a reviewable SVG/PNG image.
 *
 * This is deliberately a spec renderer rather than a screenshot of a
 * PowerPoint window.  It uses the same coordinates, icon manifest and
 * semantic line styles as create_drawio_pptx.cjs, so visual review can happen
 * in CI even when PowerPoint/LibreOffice is unavailable.
 */
const fs = require('node:fs');
const path = require('node:path');

const W = 13.333;
const H = 7.5;
const PX_PER_IN = 120;
const ROOT = path.resolve(__dirname, '..');
const ICON_DIR = path.join(ROOT, 'assets', 'icons');
const MANIFEST_PATH = path.join(ICON_DIR, 'manifest.json');
const FONT = "'Noto Sans CJK JP', 'Noto Sans JP', 'DejaVu Sans', sans-serif";

const RELATIONS = {
  data_flow: { color: '#2563EB', dash: '', arrow: true, legend: 'データフロー：データ' },
  dependency: { color: '#64748B', dash: '7 5', arrow: true, legend: '依存関係：利用・参照' },
  control: { color: '#7C3AED', dash: '', arrow: true, legend: '制御：操作・命令' },
  monitoring: { color: '#059669', dash: '2 5', arrow: true, legend: '監視：ログ・メトリクス' },
  message: { color: '#0F766E', dash: '', arrow: true, legend: 'メッセージ：イベント' },
  response: { color: '#2563EB', dash: '7 5', arrow: true, legend: '応答：返却データ' },
  association: { color: '#64748B', dash: '', arrow: false, legend: '関連' },
};

const KINDS = {
  actor: { icon: 'user.svg' },
  person: { icon: 'user.svg' },
  external: { icon: 'internet.svg' },
  application: { icon: 'app.svg' },
  service: { icon: 'app.svg' },
  gateway: { icon: 'api.svg' },
  security: { icon: 'firewall.svg' },
  data: { icon: 'database.svg' },
  'data-store': { icon: 'database.svg' },
  queue: { icon: 'queue.svg' },
  operations: { icon: 'server.svg' },
  identity: { icon: 'network.svg' },
  default: { icon: 'server.svg' },
};

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function px(value) {
  return Number(value) * PX_PER_IN;
}

function pt(value) {
  return Number(value) * (4 / 3);
}

function safeHex(value, fallback) {
  const text = String(value || fallback).replace(/^#/, '');
  return /^[0-9a-f]{6}$/i.test(text) ? `#${text.toUpperCase()}` : fallback;
}

function loadManifest() {
  const manifest = readJson(MANIFEST_PATH);
  return { ...manifest, byId: new Map((manifest.icons || []).map((item) => [String(item.id), item])) };
}

function resolveIcon(node, manifest) {
  const requested = String(node.icon || '').trim().replace(/\.svg$/i, '');
  const provider = String(node.provider || node.vendor || '').trim().toLowerCase();
  const candidates = [];
  if (requested) {
    if (requested.includes('.')) candidates.push(requested);
    if (provider) candidates.push(`${provider}.${requested}`);
    candidates.push(`generic.${requested}`, requested);
  }
  const base = KINDS[node.kind] || KINDS.default;
  const kindIcon = String(base.icon || '').replace(/\.svg$/i, '');
  if (provider) candidates.push(`${provider}.${kindIcon}`);
  candidates.push(`generic.${kindIcon}`, kindIcon, 'generic.server');
  for (const candidate of [...new Set(candidates)]) {
    const entry = manifest.byId.get(candidate);
    if (!entry) continue;
    const file = path.resolve(ICON_DIR, entry.file);
    if (!file.startsWith(`${ICON_DIR}${path.sep}`) || !fs.existsSync(file)) continue;
    return file;
  }
  return null;
}

function imageData(file) {
  if (!file) return null;
  const encoded = fs.readFileSync(file).toString('base64');
  return `data:image/svg+xml;base64,${encoded}`;
}

function lines(text, widthPx, fontPx) {
  const source = String(text ?? '');
  if (!source) return [''];
  const maxUnits = Math.max(4, widthPx / Math.max(fontPx * 0.58, 1));
  const result = [];
  let current = '';
  let units = 0;
  for (const char of source) {
    const charUnits = /[\x00-\x7f]/.test(char) ? 0.58 : 1;
    if (current && units + charUnits > maxUnits) {
      result.push(current);
      current = '';
      units = 0;
    }
    current += char;
    units += charUnits;
  }
  if (current) result.push(current);
  return result.slice(0, 2);
}

function textBox(parts, text, box, options = {}) {
  const fontPx = pt(options.fontSize || 10);
  const color = options.color || '#1F2937';
  const align = options.align || 'left';
  const wrapped = options.wrap === false ? [String(text)] : lines(text, px(box.w), fontPx);
  const lineHeight = fontPx * 1.14;
  const centerX = px(box.x + box.w / 2);
  const startY = px(box.y) + (px(box.h) - wrapped.length * lineHeight) / 2 + fontPx;
  const x = align === 'center' ? centerX : px(box.x);
  const anchor = align === 'center' ? 'middle' : 'start';
  const weight = options.bold ? '700' : '400';
  parts.push(`<text x="${x}" y="${startY}" fill="${color}" font-family="${FONT}" font-size="${fontPx}" font-weight="${weight}" text-anchor="${anchor}" dominant-baseline="alphabetic">`);
  wrapped.forEach((line, index) => {
    const dy = index === 0 ? 0 : lineHeight;
    parts.push(`<tspan x="${x}" dy="${index === 0 ? 0 : dy}">${esc(line)}</tspan>`);
  });
  parts.push('</text>');
}

function rect(parts, box, options = {}) {
  const fill = options.fill || 'none';
  const stroke = options.stroke || 'none';
  const dash = options.dash ? ` stroke-dasharray="${options.dash}"` : '';
  parts.push(`<rect x="${px(box.x)}" y="${px(box.y)}" width="${px(box.w)}" height="${px(box.h)}" rx="${px(options.radius || 0)}" fill="${fill}" fill-opacity="${options.opacity ?? 1}" stroke="${stroke}" stroke-width="${px(options.strokeWidth || 0)}"${dash}/>`.replaceAll('undefined', ''));
}

function anchor(box, name, source) {
  const side = name || (source ? 'right' : 'left');
  if (side === 'left') return [box.x, box.y + box.h / 2];
  if (side === 'top') return [box.x + box.w / 2, box.y];
  if (side === 'bottom') return [box.x + box.w / 2, box.y + box.h];
  return [box.x + box.w, box.y + box.h / 2];
}

function edgePoints(edge, nodes) {
  const source = nodes[edge.from];
  const target = nodes[edge.to];
  if (!source || !target) return [];
  const points = [anchor(source.position, edge.from_anchor, true)];
  for (const point of Array.isArray(edge.route) ? edge.route : []) points.push([Number(point.x), Number(point.y)]);
  points.push(anchor(target.position, edge.to_anchor, false));
  return points;
}

function addLine(parts, points, relation, width = 1.1) {
  const style = RELATIONS[relation] || RELATIONS.association;
  const coordinates = points.map(([x, y]) => `${px(x)},${px(y)}`).join(' ');
  const dash = style.dash ? ` stroke-dasharray="${style.dash}"` : '';
  const marker = style.arrow ? ` marker-end="url(#arrow-${relation})"` : '';
  parts.push(`<polyline points="${coordinates}" fill="none" stroke="${style.color}" stroke-width="${px(width / 72)}" stroke-linecap="round" stroke-linejoin="round"${dash}${marker}/>`);
}

function labelBox(edge, points, nodes) {
  if (edge.label_position) return edge.label_position;
  if (edge.label_box) return edge.label_box;
  if (!edge.label || points.length < 2) return null;
  const longest = points.slice(0, -1).map((point, index) => {
    const next = points[index + 1];
    return { point, next, length: Math.hypot(next[0] - point[0], next[1] - point[1]) };
  }).sort((left, right) => right.length - left.length)[0];
  const centerX = (longest.point[0] + longest.next[0]) / 2;
  const centerY = (longest.point[1] + longest.next[1]) / 2;
  const width = Math.max(0.55, Math.min(1.8, String(edge.label).length * 0.13 + 0.2));
  const height = 0.28;
  const isHorizontal = Math.abs(longest.next[1] - longest.point[1]) < 0.001;
  const sourceTop = Number(nodes[edge.from].position.y);
  const targetTop = Number(nodes[edge.to].position.y);
  return {
    x: isHorizontal ? centerX - width / 2 : centerX + 0.12,
    y: isHorizontal ? Math.min(sourceTop, targetTop) - height - 0.12 : centerY - height / 2,
    w: width,
    h: height,
  };
}

function addEdge(parts, edge, nodes) {
  const relation = edge.relation || 'data_flow';
  const points = edgePoints(edge, nodes);
  if (points.length < 2) return;
  addLine(parts, points, relation, relation === 'data_flow' ? 1.5 : 1.1);
  if (!edge.label) return;
  const position = labelBox(edge, points, nodes);
  if (!position) return;
  rect(parts, position, { fill: '#FFFFFF', opacity: 0.96, stroke: '#CBD5E1', strokeWidth: 0.5 / 72, radius: 0.04 });
  textBox(parts, edge.label, position, { fontSize: Number(position.font_size || 9), align: 'center', color: '#334155' });
}

function addBoundary(parts, boundary, nodes) {
  let position = boundary.position;
  if (!position) {
    const members = (boundary.node_ids || []).map((id) => nodes[id]).filter(Boolean);
    if (!members.length) return;
    const boxes = members.map((node) => node.position);
    const left = Math.min(...boxes.map((box) => box.x)) - 0.22;
    const top = Math.min(...boxes.map((box) => box.y)) - 0.45;
    const right = Math.max(...boxes.map((box) => box.x + box.w)) + 0.22;
    const bottom = Math.max(...boxes.map((box) => box.y + box.h)) + 0.35;
    position = { x: left, y: top, w: right - left, h: bottom - top };
  }
  const style = boundary.style || {};
  const fill = safeHex(style.fill, '#F8FAFC');
  const stroke = safeHex(style.line, '#CBD5E1');
  const dash = style.line_style === 'dashed' ? '7 5' : '';
  rect(parts, position, { fill, opacity: 1 - Number(style.transparency ?? 6) / 100, stroke, strokeWidth: Number(style.line_width || 1) / 72, radius: 0.08, dash });
  const label = String(boundary.label || boundary.id || 'ゾーン');
  const labelWidth = Math.max(0.8, Math.min(position.w - 0.25, label.length * 0.13 + 0.35));
  const labelPosition = boundary.label_position || { x: position.x + 0.14, y: position.y + 0.08, w: labelWidth, h: 0.25 };
  textBox(parts, label, labelPosition, { fontSize: Number(labelPosition.font_size || 10), color: '#475569', bold: true, wrap: false });
}

function addNode(parts, node, manifest) {
  const { x, y, w, h } = node.position;
  const icon = imageData(resolveIcon(node, manifest));
  const iconSize = Math.max(0.42, Math.min(0.68, w * 0.58, h * 0.58));
  const iconX = x + (w - iconSize) / 2;
  const iconY = y + 0.06;
  if (icon) parts.push(`<image href="${icon}" x="${px(iconX)}" y="${px(iconY)}" width="${px(iconSize)}" height="${px(iconSize)}" preserveAspectRatio="xMidYMid meet"/>`);
  const labelY = y + Math.max(0.58, iconSize + 0.07);
  const labelBox = { x: x + 0.03, y: labelY, w: w - 0.06, h: Math.min(0.30, Math.max(0.18, h - (labelY - y) - (node.role ? 0.16 : 0.04))) };
  textBox(parts, String(node.label || node.id), labelBox, { fontSize: Number(node.font_size || 10.5), align: 'center', bold: true, color: '#1F2937' });
  if (node.role) textBox(parts, String(node.role), { x: x + 0.04, y: y + h - 0.17, w: w - 0.08, h: 0.14 }, { fontSize: 7.2, align: 'center', color: '#64748B', wrap: false });
}

function addLegend(parts, spec) {
  const relations = spec.legend_relations || [...new Set((spec.edges || []).map((edge) => edge.relation).filter(Boolean))];
  const items = relations.map((relation) => [relation, RELATIONS[relation]]).filter(([, style]) => style);
  if (!items.length) return;
  textBox(parts, '凡例', { x: 0.55, y: 6.38, w: 0.55, h: 0.22 }, { fontSize: 9.5, color: '#334155', bold: true, wrap: false });
  let x = 1.1;
  for (const [relation, style] of items) {
    const width = relation === 'monitoring' ? 1.95 : relation === 'message' ? 1.75 : relation === 'dependency' ? 1.65 : 1.55;
    addLine(parts, [[x, 6.5], [x + 0.38, 6.5]], relation, relation === 'data_flow' ? 1.4 : 1.0);
    textBox(parts, style.legend || style.label, { x: x + 0.48, y: 6.37, w: width - 0.48, h: 0.28 }, { fontSize: 8.1, color: '#475569', wrap: false });
    x += width + 0.12;
    if (x > 12.8) break;
  }
}

function addTextParts(parts, spec) {
  textBox(parts, String(spec.title || '構成図'), { x: 0.55, y: 0.27, w: 8.8, h: 0.42 }, { fontSize: 25, color: '#1E293B', bold: true, wrap: false });
  textBox(parts, String(spec.purpose || ''), { x: 0.58, y: 0.75, w: 10.8, h: 0.25 }, { fontSize: 10.5, color: '#64748B', wrap: false });
  textBox(parts, String(spec.diagram_type || 'architecture').toUpperCase(), { x: 11.35, y: 0.35, w: 1.4, h: 0.25 }, { fontSize: 8.5, color: '#64748B', bold: true, align: 'center', wrap: false });
  for (const annotation of spec.annotations || []) {
    if (annotation && annotation.text) textBox(parts, String(annotation.text), annotation.position || { x: 0.58, y: 6.9, w: 9.8, h: 0.2 }, { fontSize: Number(annotation.position?.font_size || 8), color: '#64748B', wrap: false });
  }
  const metadata = spec.metadata || {};
  const footer = `図種: ${spec.diagram_type || 'architecture'}  /  版: ${metadata.version || '0.1'}  /  更新: ${metadata.updated_at || ''}`;
  textBox(parts, footer, { x: 0.55, y: 7.12, w: 12.2, h: 0.18 }, { fontSize: 7.5, color: '#94A3B8', wrap: false });
}

function buildSvg(spec) {
  const parts = [];
  const nodes = Object.fromEntries((spec.nodes || []).map((node) => [String(node.id), node]));
  const manifest = loadManifest();
  parts.push(`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${px(W)}" height="${px(H)}" viewBox="0 0 ${px(W)} ${px(H)}" role="img" aria-label="${esc(spec.title || 'architecture diagram')}">`);
  parts.push(`<defs>`);
  for (const [relation, style] of Object.entries(RELATIONS)) {
    if (!style.arrow) continue;
    parts.push(`<marker id="arrow-${relation}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,4 L0,8 z" fill="${style.color}"/></marker>`);
  }
  parts.push('</defs>');
  parts.push(`<rect width="${px(W)}" height="${px(H)}" fill="#F8FAFC"/>`);
  addTextParts(parts, spec);
  for (const boundary of spec.boundaries || []) addBoundary(parts, boundary, nodes);
  for (const edge of spec.edges || []) addEdge(parts, edge, nodes);
  for (const node of Object.values(nodes)) addNode(parts, node, manifest);
  addLegend(parts, spec);
  parts.push('</svg>');
  return parts.join('');
}

async function main() {
  const args = process.argv.slice(2);
  const specIndex = args.indexOf('--spec');
  const specPath = specIndex >= 0 ? args[specIndex + 1] : null;
  const svgPath = args.includes('--svg') ? args[args.indexOf('--svg') + 1] : null;
  const pngPath = args.includes('--png') ? args[args.indexOf('--png') + 1] : null;
  if (!specPath || (!svgPath && !pngPath)) {
    console.error('Usage: node render_preview.cjs --spec diagram.json [--svg preview.svg] [--png preview.png]');
    process.exit(2);
  }
  const svg = buildSvg(readJson(path.resolve(specPath)));
  if (svgPath) fs.writeFileSync(path.resolve(svgPath), `${svg}\n`);
  if (pngPath) {
    let Resvg;
    try {
      ({ Resvg } = require('@resvg/resvg-js'));
    } catch (error) {
      throw new Error(`PNG preview requires @resvg/resvg-js; run npm ci --prefix skills/architecture-diagram-pptx/scripts (${error.message})`);
    }
    const fontRoot = path.join(__dirname, 'node_modules', '@fontsource', 'noto-sans-jp', 'files');
    const fontFiles = [
      'noto-sans-jp-japanese-400-normal.woff',
      'noto-sans-jp-latin-400-normal.woff',
      'noto-sans-jp-japanese-700-normal.woff',
      'noto-sans-jp-latin-700-normal.woff',
    ].map((name) => path.join(fontRoot, name)).filter((file) => fs.existsSync(file));
    const fontDirs = process.env.PREVIEW_FONT_DIR ? [path.resolve(process.env.PREVIEW_FONT_DIR)] : [];
    const image = new Resvg(svg, { fitTo: { mode: 'width', value: 1600 }, font: { loadSystemFonts: true, fontFiles, fontDirs, defaultFontFamily: 'Noto Sans CJK JP', sansSerifFamily: 'Noto Sans CJK JP' } }).render().asPng();
    fs.writeFileSync(path.resolve(pngPath), image);
  }
  console.log(`preview rendered${svgPath ? ` svg=${svgPath}` : ''}${pngPath ? ` png=${pngPath}` : ''}`);
}

main().catch((error) => { console.error(error.stack || error); process.exit(1); });
