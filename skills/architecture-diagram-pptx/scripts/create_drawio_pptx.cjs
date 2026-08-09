#!/usr/bin/env node
/*
 * Generate a draw.io-inspired, editable PowerPoint slide.
 *
 * This intentionally uses PptxGenJS instead of hand-writing PresentationML.
 * PowerPoint sees every zone, card, icon, label and line as a separate object.
 */
const fs = require('node:fs');
const path = require('node:path');
const PptxGenJS = require('pptxgenjs');

const W = 13.333;
const H = 7.5;
const ROOT = path.resolve(__dirname, '..');
const ICON_DIR = path.join(ROOT, 'assets', 'icons');
const ICON_MANIFEST_PATH = path.join(ICON_DIR, 'manifest.json');
const FONT = 'Aptos';

const RELATIONS = {
  data_flow: { label: 'データフロー', color: '2563EB', dashType: 'solid', arrow: 'triangle', meaning: 'データを渡す', legend: 'データフロー：データ' },
  dependency: { label: '依存関係', color: '64748B', dashType: 'dash', arrow: 'triangle', meaning: '利用・参照する', legend: '依存関係：利用・参照' },
  control: { label: '制御', color: '7C3AED', dashType: 'solid', arrow: 'triangle', meaning: '操作・命令する', legend: '制御：操作・命令' },
  monitoring: { label: '監視・テレメトリ', color: '059669', dashType: 'sysDot', arrow: 'triangle', meaning: 'ログ・メトリクスを送る', legend: '監視：ログ・メトリクス' },
  message: { label: 'メッセージ／イベント', color: '0F766E', dashType: 'solid', arrow: 'triangle', meaning: 'イベントを発行する', legend: 'メッセージ：イベント' },
  response: { label: '応答', color: '2563EB', dashType: 'dash', arrow: 'triangle', meaning: '応答を返す', legend: '応答：返却データ' },
  association: { label: '関連', color: '64748B', dashType: 'solid', arrow: 'none', meaning: '流れ・依存を表さない', legend: '関連' },
};

const KINDS = {
  actor: { accent: '64748B', icon: 'user.svg', fill: 'F8FAFC' },
  person: { accent: '64748B', icon: 'user.svg', fill: 'F8FAFC' },
  external: { accent: '475569', icon: 'internet.svg', fill: 'F8FAFC' },
  application: { accent: '2563EB', icon: 'app.svg', fill: 'EFF6FF' },
  service: { accent: '2563EB', icon: 'app.svg', fill: 'EFF6FF' },
  gateway: { accent: '1D4ED8', icon: 'api.svg', fill: 'EFF6FF' },
  security: { accent: 'DC2626', icon: 'firewall.svg', fill: 'FEF2F2' },
  data: { accent: '16A34A', icon: 'database.svg', fill: 'F0FDF4' },
  'data-store': { accent: '16A34A', icon: 'database.svg', fill: 'F0FDF4' },
  queue: { accent: '0F766E', icon: 'queue.svg', fill: 'F0FDFA' },
  operations: { accent: 'EA580C', icon: 'server.svg', fill: 'FFF7ED' },
  identity: { accent: '7C3AED', icon: 'network.svg', fill: 'F5F3FF' },
  default: { accent: '64748B', icon: 'server.svg', fill: 'F8FAFC' },
};

function loadIconManifest() {
  if (!fs.existsSync(ICON_MANIFEST_PATH)) return { icons: [], byId: new Map() };
  const manifest = readJson(ICON_MANIFEST_PATH);
  const byId = new Map((manifest.icons || []).map((item) => [String(item.id), item]));
  return { ...manifest, byId };
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function safeHex(value, fallback) {
  const text = String(value || fallback).replace(/^#/, '');
  return /^[0-9a-f]{6}$/i.test(text) ? text.toUpperCase() : fallback;
}

function kindStyle(node) {
  const base = KINDS[node.kind] || KINDS.default;
  const override = node.style || {};
  return {
    accent: safeHex(override.accent || override.line, base.accent),
    fill: safeHex(override.fill, base.fill),
    icon: node.icon || base.icon,
  };
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
  candidates.push(`generic.${kindIcon}`, kindIcon);
  const seen = new Set();
  for (const candidate of candidates) {
    if (seen.has(candidate)) continue;
    seen.add(candidate);
    const entry = manifest.byId.get(candidate);
    if (!entry) continue;
    const iconPath = path.resolve(ICON_DIR, entry.file);
    if (!iconPath.startsWith(`${ICON_DIR}${path.sep}`) || !fs.existsSync(iconPath)) continue;
    return { requested: requested || null, provider: provider || null, resolvedId: candidate, path: iconPath, entry, fallback: candidate !== candidates[0] };
  }
  const fallback = manifest.byId.get('generic.server') || manifest.byId.get('server');
  if (fallback) {
    return { requested: requested || null, provider: provider || null, resolvedId: fallback.id, path: path.resolve(ICON_DIR, fallback.file), entry: fallback, fallback: true };
  }
  return { requested: requested || null, provider: provider || null, resolvedId: null, path: null, entry: null, fallback: true };
}

function anchor(box, name, source) {
  const side = name || (source ? 'right' : 'left');
  if (side === 'left') return [box.x, box.y + box.h / 2];
  if (side === 'top') return [box.x + box.w / 2, box.y];
  if (side === 'bottom') return [box.x + box.w / 2, box.y + box.h];
  return [box.x + box.w, box.y + box.h / 2];
}

function routePoints(edge, nodes) {
  const source = nodes[edge.from];
  const target = nodes[edge.to];
  if (!source || !target) return [];
  const points = [anchor(source.position, edge.from_anchor, true)];
  if (Array.isArray(edge.route)) {
    for (const point of edge.route) points.push([Number(point.x), Number(point.y)]);
  }
  points.push(anchor(target.position, edge.to_anchor, false));
  return points;
}

function addLine(slide, pptx, start, end, relation, width = 1.25, withArrow = true) {
  const style = RELATIONS[relation] || RELATIONS.association;
  let [x1, y1] = start;
  let [x2, y2] = end;
  // Avoid negative extents.  When a route travels left/up, reverse the line
  // object and move the arrow to the beginning; it keeps the semantic
  // direction while remaining PowerPoint-safe.
  const reversed = x1 > x2 || (Math.abs(x1 - x2) < 0.001 && y1 > y2);
  let beginArrowType = 'none';
  let endArrowType = withArrow ? style.arrow : 'none';
  if (reversed && withArrow) {
    [x1, x2] = [x2, x1];
    [y1, y2] = [y2, y1];
    beginArrowType = style.arrow;
    endArrowType = 'none';
  }
  const shape = (Math.abs(y2 - y1) < 0.001 && x2 < x1) ? pptx.ShapeType.lineInv : pptx.ShapeType.line;
  slide.addShape(shape, {
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    w: Math.abs(x2 - x1),
    h: Math.abs(y2 - y1),
    line: { color: style.color, width, dashType: style.dashType, beginArrowType, endArrowType },
  });
}

function addEdge(slide, pptx, edge, nodes) {
  const relation = edge.relation || 'data_flow';
  const points = routePoints(edge, nodes);
  if (points.length < 2) return;
  for (let i = 0; i < points.length - 1; i += 1) {
    addLine(slide, pptx, points[i], points[i + 1], relation, relation === 'data_flow' ? 1.5 : 1.1, i === points.length - 2);
  }
  if (!edge.label) return;
  const label = String(edge.label);
  const position = edge.label_position || edge.label_box || {};
  const longest = points.slice(0, -1).map((point, i) => {
    const next = points[i + 1];
    return { point, next, length: Math.hypot(next[0] - point[0], next[1] - point[1]) };
  }).sort((a, b) => b.length - a.length)[0];
  const centerX = (longest.point[0] + longest.next[0]) / 2;
  const centerY = (longest.point[1] + longest.next[1]) / 2;
  const height = Number(position.h || 0.28);
  const isHorizontal = Math.abs(longest.next[1] - longest.point[1]) < 0.001;
  let defaultWidth = Math.max(0.55, Math.min(1.8, label.length * 0.13 + 0.2));
  if (!position.w && isHorizontal && points.length === 2) {
    const source = nodes[edge.from].position;
    const target = nodes[edge.to].position;
    const gap = target.x - (source.x + source.w);
    if (gap > 0) defaultWidth = Math.min(defaultWidth, Math.max(0.4, gap - 0.04));
  }
  const width = Number(position.w || defaultWidth);
  // Keep labels attached to the route.  For a horizontal edge the readable
  // default is a small white pill on the arrow itself; if it does not fit in
  // the gap between nodes, the spec must provide wider spacing or a shorter
  // label instead of letting the label float above the diagram.
  const defaultX = isHorizontal ? centerX - width / 2 : centerX + 0.12;
  const defaultY = centerY - height / 2;
  const x = Number(position.x ?? defaultX);
  const y = Number(position.y ?? defaultY);
  slide.addText(label, {
    x, y, w: width, h: height,
    fontFace: FONT, lang: 'ja-JP', fontSize: Number(position.font_size || 9),
    color: '334155', align: 'center', valign: 'mid', margin: 0.03,
    fit: 'shrink',
    fill: { color: 'FFFFFF', transparency: 2 },
    line: { color: 'CBD5E1', transparency: 35, width: 0.5 },
    radius: 0.04,
  });
}

function addZone(slide, pptx, boundary, nodes) {
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
  const fill = safeHex(style.fill, 'F8FAFC');
  const line = safeHex(style.line, 'CBD5E1');
  slide.addShape(pptx.ShapeType.roundRect, {
    x: position.x, y: position.y, w: position.w, h: position.h,
    rectRadius: 0.08,
    fill: { color: fill, transparency: Number(style.transparency ?? 6) },
    line: { color: line, width: Number(style.line_width || 1), dashType: style.line_style === 'dashed' ? 'dash' : 'solid' },
  });
  const label = String(boundary.label || boundary.id || 'ゾーン');
  const labelWidth = Math.max(0.8, Math.min(position.w - 0.25, label.length * 0.13 + 0.35));
  const labelPosition = boundary.label_position || {};
  slide.addText(label, {
    x: Number(labelPosition.x ?? position.x + 0.14),
    y: Number(labelPosition.y ?? position.y + 0.08),
    w: Number(labelPosition.w ?? labelWidth),
    h: Number(labelPosition.h ?? 0.25),
    fontFace: FONT, lang: 'ja-JP', fontSize: Number(labelPosition.font_size || 10), bold: true, color: '475569',
    margin: 0, fit: 'shrink', fill: { color: fill, transparency: 0 },
  });
}

function addCard(slide, pptx, node, manifest, resolutions) {
  const style = kindStyle(node);
  const { x, y, w, h } = node.position;
  const resolved = resolveIcon({ ...node, icon: node.icon || style.icon }, manifest);
  resolutions.push({ node_id: String(node.id), requested: resolved.requested, provider: resolved.provider, resolved_id: resolved.resolvedId, fallback: resolved.fallback, source: resolved.entry?.source || null });
  if (resolved.fallback && resolved.requested) console.warn(`icon fallback: ${node.id}: ${resolved.requested} -> ${resolved.resolvedId || 'none'}`);

  // The glyph is the primary object.  Keep it standalone: a surrounding
  // ellipse competes with the icon and can collide with the label when a node
  // is compact.  The label and role stay as separate editable text objects
  // below it, following the icon-first feel of draw.io/cloud libraries.
  const iconSize = Math.max(0.42, Math.min(0.68, w * 0.58, h * 0.58));
  const iconX = x + (w - iconSize) / 2;
  const iconY = y + 0.06;
  if (resolved.path) slide.addImage({ path: resolved.path, x: iconX, y: iconY, w: iconSize, h: iconSize, transparency: 0 });
  const labelY = y + Math.max(0.58, iconSize + 0.07);
  slide.addText(String(node.label || node.id), {
    x: x + 0.03, y: labelY, w: w - 0.06, h: Math.min(0.30, Math.max(0.18, h - (labelY - y) - (node.role ? 0.16 : 0.04))),
    fontFace: FONT, lang: 'ja-JP', fontSize: Number(node.font_size || 10.5), bold: true,
    color: '1F2937', align: 'center', margin: 0.01, fit: 'shrink', valign: 'mid',
  });
  if (node.role) {
    slide.addText(String(node.role), {
      x: x + 0.04, y: y + h - 0.17, w: w - 0.08, h: 0.14,
      fontFace: FONT, lang: 'ja-JP', fontSize: 7.2, color: '64748B',
      align: 'center', margin: 0, fit: 'shrink', valign: 'mid',
    });
  }
}

function addLegend(slide, pptx, spec) {
  const relations = spec.legend_relations || [...new Set((spec.edges || []).map((edge) => edge.relation).filter(Boolean))];
  const items = relations.map((relation) => [relation, RELATIONS[relation]]).filter(([, style]) => style);
  if (!items.length) return;
  slide.addText('凡例', { x: 0.55, y: 6.38, w: 0.55, h: 0.22, fontFace: FONT, fontSize: 9.5, bold: true, color: '334155', margin: 0 });
  let x = 1.1;
  for (const [relation, style] of items) {
    const label = style.legend || `${style.label}：${style.meaning}`;
    const width = Math.min(2.25, Math.max(1.18, label.length * 0.075 + 0.45));
    addLine(slide, pptx, [x, 6.5], [x + 0.38, 6.5], relation, relation === 'data_flow' ? 1.4 : 1.0);
    slide.addText(label, { x: x + 0.48, y: 6.37, w: width - 0.48, h: 0.28, fontFace: FONT, fontSize: 8.1, color: '475569', margin: 0, fit: 'shrink', breakLine: false });
    x += width + 0.12;
    if (x > 12.8) break;
  }
}

function addFooter(slide, spec) {
  const metadata = spec.metadata || {};
  const footer = `図種: ${spec.diagram_type || 'architecture'}  /  版: ${metadata.version || '0.1'}  /  更新: ${metadata.updated_at || ''}`;
  slide.addText(footer, { x: 0.55, y: 7.12, w: 12.2, h: 0.18, fontFace: FONT, fontSize: 7.5, color: '94A3B8', margin: 0, fit: 'shrink' });
}

function addAnnotations(slide, spec) {
  for (const annotation of spec.annotations || []) {
    if (!annotation || !annotation.text) continue;
    const position = annotation.position || { x: 0.58, y: 6.9, w: 9.8, h: 0.2 };
    slide.addText(String(annotation.text), {
      x: Number(position.x), y: Number(position.y), w: Number(position.w), h: Number(position.h),
      fontFace: FONT, lang: 'ja-JP', fontSize: Number(position.font_size || 8), color: '64748B',
      margin: 0, fit: 'shrink',
    });
  }
}

function generate(spec, output) {
  const pptx = new PptxGenJS();
  pptx.layout = 'LAYOUT_WIDE';
  pptx.author = 'architecture-diagram-pptx Skill';
  pptx.company = 'architecture-diagram-pptx Skill';
  pptx.subject = spec.purpose || 'Architecture diagram';
  pptx.title = spec.title || 'Architecture diagram';
  pptx.lang = 'ja-JP';
  pptx.theme = { headFontFace: FONT, bodyFontFace: FONT, lang: 'ja-JP' };
  const slide = pptx.addSlide();
  slide.background = { color: 'F8FAFC' };
  slide.addText(String(spec.title || '構成図'), { x: 0.55, y: 0.27, w: 8.8, h: 0.42, fontFace: FONT, lang: 'ja-JP', fontSize: 25, bold: true, color: '1E293B', margin: 0, fit: 'shrink' });
  slide.addText(String(spec.purpose || ''), { x: 0.58, y: 0.75, w: 10.8, h: 0.25, fontFace: FONT, lang: 'ja-JP', fontSize: 10.5, color: '64748B', margin: 0, fit: 'shrink' });
  slide.addText(String(spec.diagram_type || 'architecture').toUpperCase(), { x: 11.35, y: 0.35, w: 1.4, h: 0.25, fontFace: FONT, fontSize: 8.5, bold: true, color: '64748B', align: 'right', margin: 0, fit: 'shrink' });
  const nodes = Object.fromEntries((spec.nodes || []).map((node) => [String(node.id), node]));
  const manifest = loadIconManifest();
  const resolutions = [];
  for (const boundary of spec.boundaries || []) addZone(slide, pptx, boundary, nodes);
  for (const edge of spec.edges || []) addEdge(slide, pptx, edge, nodes);
  for (const node of Object.values(nodes)) addCard(slide, pptx, node, manifest, resolutions);
  addLegend(slide, pptx, spec);
  addAnnotations(slide, spec);
  addFooter(slide, spec);
  return pptx.writeFile({ fileName: output }).then(() => {
    fs.writeFileSync(`${output}.icons.json`, `${JSON.stringify({ title: spec.title || '', output, resolutions }, null, 2)}\n`);
  });
}

async function main() {
  const args = process.argv.slice(2);
  const specIndex = args.indexOf('--spec');
  const specPath = specIndex >= 0 ? args[specIndex + 1] : null;
  const output = args.find((item, index) => index !== specIndex && index !== specIndex + 1 && !item.startsWith('-'));
  if (!specPath || !output) {
    console.error('Usage: node create_drawio_pptx.cjs --spec diagram.json output.pptx');
    process.exit(2);
  }
  await generate(readJson(path.resolve(specPath)), path.resolve(output));
  console.log(`created ${output}`);
}

main().catch((error) => { console.error(error.stack || error); process.exit(1); });
