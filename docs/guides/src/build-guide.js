// Builds the CommuniQ tenant guide (.docx) from content.json, in the v1.0 guide's visual style:
// Sylfaen 11 pt body, navy (#07263C) bold headings, the "CommuniQ CQ" title page with the red rule,
// navy "note" and red "warning" callout boxes, light-blue table headers. A4, 1" margins.
//
//   npm install --no-save docx@9          # once, in this folder (not a project dependency)
//   node build-guide.js tenant-guide-ka.json ../CommuniQ-Tenant-Guide-KA.docx
//   cp ../CommuniQ-Tenant-Guide-KA.docx ../../../frontend/public/guides/   # the served copy
//   (cd ../../../frontend/public && zip -X guides.zip guides/CommuniQ-Tenant-Guide-KA.docx)
//
// tenant-guide-ka.json is the guide's content (v2.0, written from the code in October 2026): edit it
// there and rebuild, rather than editing the .docx by hand, so the next revision has a source.
//
// content.json: { meta: {title, subtitle, tagline, version, contents_title},
//                 sections: [{ h1, blocks: [{type: p|h2|h3|steps|bullets|note|warn|table, ...}] }] }
// Inline **bold** is supported in every text field.
const fs = require('fs');
const {
  AlignmentType, BorderStyle, Document, Footer, HeadingLevel, LevelFormat, Packer, PageBreak,
  PageNumber, Paragraph, ShadingType, Table, TableCell, TableLayoutType, TableRow, TextRun, WidthType,
} = require('docx');

const NAVY = '07263C', RED = 'FA3B3C', GREY = '5A7184', TINT = 'EAF1F6', ROSE = 'FDECEC';
const FONT = 'Sylfaen', FULL = 9026;

const [, , src = 'content.json', out = 'out.docx'] = process.argv;
const doc = JSON.parse(fs.readFileSync(src, 'utf8'));

// "a **b** c" -> runs; `base` carries size/colour for the whole paragraph.
function runs(text, base = {}) {
  const parts = String(text ?? '').split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map(s => s.startsWith('**') && s.endsWith('**')
    ? new TextRun({ text: s.slice(2, -2), font: FONT, bold: true, ...base })
    : new TextRun({ text: s, font: FONT, ...base }));
}

const para = (text, opts = {}) => new Paragraph({ spacing: { after: 120 }, ...opts, children: runs(text, opts.run) });

let listInstance = 0;
const list = (items, ref) => {
  const instance = ++listInstance;               // each list restarts its own numbering
  return items.map(t => new Paragraph({
    numbering: { reference: ref, level: 0, instance },
    spacing: { after: 60 },
    children: runs(t),
  }));
};

const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };
const thin = { style: BorderStyle.SINGLE, size: 4, color: 'auto' };

function table(b) {
  const cols = (b.head || b.rows[0]).length;
  let widths = Array.isArray(b.widths) && b.widths.length === cols ? b.widths : null;
  if (!widths || widths.reduce((a, x) => a + x, 0) !== FULL) {
    widths = cols === 2 ? [3300, 5726] : Array.from({ length: cols }, (_, i) =>
      i < cols - 1 ? Math.floor(FULL / cols) : FULL - Math.floor(FULL / cols) * (cols - 1));
  }
  const row = (cells, head) => new TableRow({
    tableHeader: head,
    children: cells.map((c, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA },
      margins: cellMargins,
      shading: head ? { fill: TINT, type: ShadingType.CLEAR, color: 'auto' } : undefined,
      children: [new Paragraph({ children: runs(c, head ? { bold: true, color: NAVY, size: 21 } : {}) })],
    })),
  });
  return new Table({
    width: { size: FULL, type: WidthType.DXA },
    layout: TableLayoutType.FIXED,
    columnWidths: widths,
    borders: { top: thin, bottom: thin, left: thin, right: thin, insideHorizontal: thin, insideVertical: thin },
    rows: [...(b.head ? [row(b.head, true)] : []), ...b.rows.map(r => row(r, false))],
  });
}

function callout(text, edge, fill) {
  const none = { style: BorderStyle.NONE, size: 0, color: 'auto' };
  return new Table({
    width: { size: FULL, type: WidthType.DXA },
    layout: TableLayoutType.FIXED,
    columnWidths: [FULL],
    borders: { top: none, bottom: none, right: none, insideHorizontal: none, insideVertical: none,
               left: { style: BorderStyle.SINGLE, size: 24, color: edge } },
    rows: [new TableRow({ children: [new TableCell({
      width: { size: FULL, type: WidthType.DXA },
      margins: { top: 100, bottom: 100, left: 160, right: 160 },
      shading: { fill, type: ShadingType.CLEAR, color: 'auto' },
      children: [new Paragraph({ children: runs(text) })],
    })] })],
  });
}
const gap = () => new Paragraph({ spacing: { after: 60 }, children: [] });

function block(b) {
  switch (b.type) {
    case 'p': return [para(b.text)];
    case 'h2': return [new Paragraph({ heading: HeadingLevel.HEADING_2, children: runs(b.text) })];
    case 'h3': return [new Paragraph({ heading: HeadingLevel.HEADING_3, children: runs(b.text) })];
    case 'steps': return list(b.items || [], 'steps');
    case 'bullets': return list(b.items || [], 'bullets');
    case 'note': return [gap(), callout(b.text, NAVY, TINT), gap()];
    case 'warn': return [gap(), callout(b.text, RED, ROSE), gap()];
    case 'table': return [gap(), table(b), gap()];
    default: throw new Error(`unknown block type ${b.type}`);
  }
}

const m = doc.meta;
const titlePage = [
  new Paragraph({ spacing: { before: 2800, after: 120 }, children: [
    new TextRun({ text: 'CommuniQ', font: 'Calibri', bold: true, size: 72, color: NAVY }),
    new TextRun({ text: ' CQ', font: 'Calibri', bold: true, size: 72, color: RED }),
  ] }),
  new Paragraph({ spacing: { after: 360 }, border: { bottom: { style: BorderStyle.SINGLE, size: 24, color: RED, space: 1 } }, children: [] }),
  new Paragraph({ spacing: { after: 160 }, children: runs(m.subtitle, { bold: true, size: 48, color: NAVY }) }),
  new Paragraph({ spacing: { after: 2000 }, children: runs(m.tagline, { size: 26, color: GREY }) }),
  new Paragraph({ children: runs(m.version, { size: 20, color: GREY }) }),
  new Paragraph({ children: [new PageBreak()] }),
];

const contents = [
  new Paragraph({ heading: HeadingLevel.HEADING_1, children: runs(m.contents_title || 'სარჩევი') }),
  ...doc.sections.map(s => new Paragraph({ spacing: { after: 60 }, indent: { left: 200 }, children: runs(s.h1) })),
  new Paragraph({ children: [new PageBreak()] }),
];

const body = doc.sections.flatMap(s => [
  new Paragraph({ heading: HeadingLevel.HEADING_1, children: runs(s.h1) }),
  ...s.blocks.flatMap(block),
]);

const heading = (id, name, size, before, after, color = NAVY) => ({
  id, name, basedOn: 'Normal', next: 'Normal', quickFormat: true,
  run: { font: FONT, bold: true, size, color },
  paragraph: { spacing: { before, after }, keepNext: true, keepLines: true },
});

const listLevel = (format, text) => ({
  level: 0, format, text, alignment: AlignmentType.LEFT,
  style: { paragraph: { indent: { left: 460, hanging: 320 } } },
});

const document = new Document({
  creator: 'CommuniQ',
  title: `${m.title} — ${m.subtitle}`,
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      heading('Heading1', 'Heading 1', 32, 360, 140),
      heading('Heading2', 'Heading 2', 26, 240, 100),
      heading('Heading3', 'Heading 3', 23, 200, 80, '1F4D78'),
    ],
  },
  numbering: { config: [
    { reference: 'steps', levels: [listLevel(LevelFormat.DECIMAL, '%1.')] },
    { reference: 'bullets', levels: [listLevel(LevelFormat.BULLET, '•')] },
  ] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [
      new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: GREY }),
    ] })] }) },
    children: [...titlePage, ...contents, ...body],
  }],
});

Packer.toBuffer(document).then(buf => { fs.writeFileSync(out, buf); console.log(`wrote ${out} (${buf.length} bytes)`); });
