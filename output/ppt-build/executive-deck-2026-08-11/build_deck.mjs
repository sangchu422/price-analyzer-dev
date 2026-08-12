import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const ROOT = "C:/Users/sangwoo/Documents/Price-analyzer/.worktrees/local-data-foundation";
const ASSET = path.join(ROOT, "output/playwright/executive-deck-2026-08-11");
const OUT = path.join(ROOT, "output/ppt-build/executive-deck-2026-08-11/rendered");
const FINAL = path.join(ROOT, "output/Price_Analyzer_임원보고_2026-08-11.pptx");
const USER_PHOTO = path.join(ROOT, "output/ppt-build/executive-deck-2026-08-11/assets/cpi-source-photo.jpg");

const W = 1280;
const H = 720;
const C = {
  navy: "#00287A",
  navy2: "#001B55",
  blue: "#174EA6",
  cyan: "#00A9CE",
  mint: "#6EE7C8",
  ink: "#15213A",
  text: "#26334D",
  muted: "#66748D",
  line: "#D8E0EC",
  pale: "#EAF0FA",
  pale2: "#F4F7FB",
  white: "#FFFFFF",
  green: "#0A7A62",
  amber: "#B66A00",
  red: "#C53A42",
  dark: "#091013",
};
const FONT = "Malgun Gothic";

async function bytes(file) {
  const b = await fs.readFile(file);
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
}

function box(slide, x, y, w, h, fill = C.white, radius = 12, line = C.line, shadow = "shadow-none") {
  return slide.shapes.add({
    geometry: "roundRect",
    position: { left: x, top: y, width: w, height: h },
    fill,
    line: { style: "solid", fill: line, width: line === "none" ? 0 : 1 },
    borderRadius: radius,
    shadow,
  });
}

function textBox(slide, text, x, y, w, h, opts = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position: { left: x, top: y, width: w, height: h },
    fill: opts.fill ?? "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontFamily: FONT,
    fontSize: opts.size ?? 20,
    bold: opts.bold ?? false,
    color: opts.color ?? C.text,
    alignment: opts.align ?? "left",
    verticalAlignment: opts.valign ?? "middle",
  };
  return shape;
}

function pill(slide, label, x, y, w, fill = C.pale, color = C.navy) {
  box(slide, x, y, w, 30, fill, 15, "none");
  textBox(slide, label, x + 8, y + 1, w - 16, 27, { size: 13, bold: true, color, align: "center" });
}

function header(slide, section, title, subtitle = "") {
  textBox(slide, section.toUpperCase(), 54, 32, 300, 20, { size: 12, bold: true, color: C.navy });
  textBox(slide, title, 54, 56, 1140, 54, { size: 34, bold: true, color: C.ink });
  if (subtitle) textBox(slide, subtitle, 56, 110, 1110, 28, { size: 15, color: C.muted });
  slide.shapes.add({ geometry: "line", position: { left: 54, top: 145, width: 1172, height: 0 }, fill: "none", line: { style: "solid", fill: C.line, width: 1 } });
}

function footer(slide, page) {
  slide.shapes.add({ geometry: "line", position: { left: 54, top: 682, width: 1172, height: 0 }, fill: "none", line: { style: "solid", fill: C.line, width: 1 } });
  textBox(slide, "PRICE ANALYZER · 임원 보고", 54, 688, 260, 18, { size: 10, color: C.muted });
  textBox(slide, String(page).padStart(2, "0"), 1174, 687, 52, 18, { size: 10, bold: true, color: C.navy, align: "right" });
}

function notes(slide, body, sources) {
  const safeSources = sources.map((source) => {
    const value = String(source).replaceAll("\\", "/");
    if (value.startsWith(USER_PHOTO.replaceAll("\\", "/"))) {
      return "사용자 제공 KOSIS 화면 사진 (2026-08-11)";
    }
    if (value.startsWith(`${ASSET.replaceAll("\\", "/")}/`)) {
      return `프로젝트 실제 화면 캡처 · ${path.basename(value)} (2026-08-11)`;
    }
    if (value.startsWith(`${ROOT.replaceAll("\\", "/")}/`)) {
      return value.slice(ROOT.length + 1);
    }
    return value;
  });
  slide.speakerNotes.textFrame.setText(`${body}\n\n[Sources]\n${safeSources.map((s) => `- ${s}`).join("\n")}\n[/Sources]`);
  slide.speakerNotes.setVisible(true);
}

async function addImage(slide, file, x, y, w, h, opts = {}) {
  if (opts.frame !== false) box(slide, x - 5, y - 5, w + 10, h + 10, C.white, 14, C.line, "shadow-sm");
  return slide.images.add({
    blob: await bytes(file),
    contentType: file.toLowerCase().endsWith(".jpg") || file.toLowerCase().endsWith(".jpeg") ? "image/jpeg" : "image/png",
    alt: opts.alt ?? "Price Analyzer 실제 화면",
    fit: opts.fit ?? "cover",
    crop: opts.crop,
    position: { left: x, top: y, width: w, height: h },
    geometry: "roundRect",
    borderRadius: 10,
  });
}

function styleTable(table, { headerFill = C.navy, headerColor = C.white, bodyFill = C.white, fontSize = 15, firstColumnBold = true } = {}) {
  table.borders.assign({ style: "solid", fill: C.line, width: 1 });
  table.cells.block({ row: 0, column: 0, rowCount: 1, columnCount: table.columns.length }).assign({
    fill: headerFill,
    textStyle: { fontFamily: FONT, fontSize, bold: true, color: headerColor },
    margins: { left: 12, right: 12, top: 8, bottom: 8 },
    anchor: "middle",
  });
  if (table.rows.length > 1) {
    table.cells.block({ row: 1, column: 0, rowCount: table.rows.length - 1, columnCount: table.columns.length }).assign({
      fill: bodyFill,
      textStyle: { fontFamily: FONT, fontSize, color: C.text },
      margins: { left: 12, right: 12, top: 7, bottom: 7 },
      anchor: "middle",
    });
  }
  if (firstColumnBold && table.rows.length > 1) {
    table.cells.block({ row: 1, column: 0, rowCount: table.rows.length - 1, columnCount: 1 }).assign({
      textStyle: { fontFamily: FONT, fontSize, bold: true, color: C.ink },
    });
  }
}

const p = Presentation.create({ slideSize: { width: W, height: H } });

// 1. Title
{
  const s = p.slides.add();
  s.background.fill = C.navy;
  s.shapes.add({ geometry: "rect", position: { left: 0, top: 0, width: 16, height: H }, fill: C.mint, line: { style: "solid", fill: "none", width: 0 } });
  s.shapes.add({
    geometry: "custom",
    position: { left: 760, top: 0, width: 520, height: 720 },
    fill: C.navy2,
    line: { style: "solid", fill: "none", width: 0 },
    customPaths: [{
      width: 520,
      height: 720,
      commands: [
        { moveTo: { x: 120, y: 0 } },
        { lineTo: { x: 520, y: 0 } },
        { lineTo: { x: 520, y: 720 } },
        { lineTo: { x: 0, y: 720 } },
        { close: {} },
      ],
    }],
  });
  textBox(s, "PRICE ANALYZER", 70, 64, 270, 28, { size: 14, bold: true, color: C.mint });
  textBox(s, "과거 견적을\n구매 의사결정 자산으로", 70, 168, 820, 180, { size: 52, bold: true, color: C.white });
  textBox(s, "견적 적정성 분석 · 구매 목표가 제안 시스템", 74, 368, 650, 38, { size: 23, color: "#D7E4FF" });
  box(s, 74, 478, 590, 2, C.mint, 0, "none");
  textBox(s, "임원 보고  |  2026. 08. 11  |  로컬 시범운영 단계", 74, 504, 650, 28, { size: 15, color: "#D7E4FF" });
  textBox(s, "핵심 질문", 900, 182, 180, 22, { size: 13, bold: true, color: C.mint });
  textBox(s, "비싼가?", 898, 218, 250, 58, { size: 34, bold: true, color: C.white });
  textBox(s, "얼마에 사야 하는가?", 898, 288, 300, 80, { size: 27, bold: true, color: C.white });
  notes(s, "발표의 출발점은 기술이 아니라 구매 의사결정입니다. 이 시스템은 과거 견적을 다시 쓸 수 있는 가격 근거로 바꾸고, 신규 견적의 적정성과 구매 목표가를 한 흐름에서 제시합니다.", [
    `${ROOT}/docs/HANDOFF_2026-08-11.md`,
    `${ROOT}/docs/DECISIONS.md`,
  ]);
}

// 2. Problem
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "01 · 문제와 목적", "견적 검토의 문제는 ‘가격’보다 ‘근거의 단절’입니다");
  const items = [
    ["01", "과거 견적이 파일마다 흩어져", "같은 품목을 다시 찾아 비교하기 어렵습니다."],
    ["02", "담당자마다 비교 기준이 달라", "판정 결과와 설명 방식이 일관되지 않습니다."],
    ["03", "오래된 가격은 현재 가치와 달라", "2016년 단가를 2026년에 그대로 쓸 수 없습니다."],
  ];
  items.forEach((it, i) => {
    const y = 178 + i * 120;
    textBox(s, it[0], 58, y, 40, 24, { size: 13, bold: true, color: C.cyan });
    textBox(s, it[1], 104, y - 4, 430, 32, { size: 21, bold: true, color: C.ink });
    textBox(s, it[2], 104, y + 35, 420, 42, { size: 15, color: C.muted });
  });
  await addImage(s, path.join(ASSET, "cleansing-original-preview.png"), 604, 190, 566, 250, { fit: "contain", alt: "원본 견적서 셀 위치 미리보기" });
  box(s, 600, 474, 574, 122, C.pale, 12, "none");
  textBox(s, "따라서 필요한 것은", 626, 492, 210, 24, { size: 14, bold: true, color: C.navy });
  textBox(s, "가격 숫자 + 원본 위치 + 판단 기준을\n함께 남기는 구매 의사결정 체계", 626, 522, 510, 58, { size: 23, bold: true, color: C.ink });
  footer(s, 2);
  notes(s, "원본 견적의 특정 행을 바로 확인할 수 있는 실제 화면입니다. 문제의 핵심은 파일이 없다는 것이 아니라, 가격과 근거가 연결되지 않았다는 점입니다.", [
    `${ASSET}/cleansing-original-preview.png`,
    `${ROOT}/docs/DATA_QUALITY_AUDIT_2026-08-11.md`,
  ]);
}

// 3. Solution
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "02 · 프로젝트 목적", "견적서를 올리면 세 가지 답을 한 번에 제공합니다");
  const nodes = [
    { x: 72, t: "과거 가격 근거", d: "표준 DB 매칭" },
    { x: 440, t: "비싼지 판단", d: "적정 · 주의 · 고가 · 저가" },
    { x: 808, t: "살 가격 제안", d: "물가 반영 구매 목표가" },
  ];
  [392, 760].forEach((x) => s.shapes.add({ geometry: "rightArrow", position: { left: x, top: 198, width: 34, height: 26 }, fill: C.cyan, line: { style: "solid", fill: "none", width: 0 } }));
  nodes.forEach((n, i) => {
    box(s, n.x, 172, 304, 86, i === 1 ? C.navy : C.pale, 14, "none");
    textBox(s, `0${i + 1}`, n.x + 18, 188, 38, 22, { size: 12, bold: true, color: i === 1 ? C.mint : C.cyan });
    textBox(s, n.t, n.x + 60, 182, 220, 28, { size: 20, bold: true, color: i === 1 ? C.white : C.ink });
    textBox(s, n.d, n.x + 60, 216, 225, 24, { size: 13, color: i === 1 ? "#D7E4FF" : C.muted });
  });
  await addImage(s, path.join(ASSET, "analysis-intake.png"), 104, 300, 1072, 328, { crop: { left: 0, top: 0, right: 0, bottom: 0.29 }, alt: "신규 견적 접수와 판정 기준 설정 화면" });
  pill(s, "실제 화면", 92, 286, 92, C.mint, C.navy2);
  footer(s, 3);
  notes(s, "사용자는 견적서 한 건과 담당자, 판정 기준만 입력합니다. 시스템은 표준 DB 비교와 목표가 산정을 동일한 접수 흐름에서 수행합니다.", [
    `${ASSET}/analysis-intake.png`,
    `${ROOT}/docs/HANDOFF_2026-08-11.md`,
  ]);
}

// 4. Development process
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "03 · 개발 과정", "자동화보다 ‘신뢰 가능한 근거’를 먼저 만들었습니다");
  const steps = ["원본 수집", "품목 추출", "검토·정제", "표준 DB", "신규 견적 분석"];
  for (let i = 0; i < 4; i++) s.shapes.add({ geometry: "rightArrow", position: { left: 245 + i * 225, top: 193, width: 34, height: 24 }, fill: C.cyan, line: { style: "solid", fill: "none", width: 0 } });
  steps.forEach((label, i) => {
    box(s, 60 + i * 225, 172, 184, 68, i === 4 ? C.navy : C.pale, 12, "none");
    textBox(s, `0${i + 1}`, 76 + i * 225, 187, 28, 18, { size: 11, bold: true, color: i === 4 ? C.mint : C.cyan });
    textBox(s, label, 108 + i * 225, 182, 120, 35, { size: 18, bold: true, color: i === 4 ? C.white : C.ink });
  });
  await addImage(s, path.join(ASSET, "cleansing-review.png"), 56, 278, 760, 356, { crop: { left: 0, top: 0.09, right: 0, bottom: 0.06 }, alt: "정제 검토 실제 화면" });
  box(s, 852, 286, 346, 348, C.pale2, 14, C.line);
  textBox(s, "개발 원칙", 880, 312, 250, 28, { size: 18, bold: true, color: C.navy });
  const rules = [
    "원본 파일은 그대로 보존",
    "확실한 값만 자동 반영",
    "애매한 값은 검토 대기",
    "모든 가격은 원본으로 역추적",
  ];
  rules.forEach((r, i) => {
    box(s, 880, 360 + i * 58, 22, 22, i < 3 ? C.navy : C.cyan, 11, "none");
    textBox(s, "✓", 881, 358 + i * 58, 20, 23, { size: 13, bold: true, color: C.white, align: "center" });
    textBox(s, r, 916, 353 + i * 58, 250, 32, { size: 16, color: C.text });
  });
  footer(s, 4);
  notes(s, "개발 과정은 무조건 많이 읽는 자동화가 아니라, 가격 기준으로 써도 되는 행과 사람이 확인해야 하는 행을 분리하는 과정이었습니다.", [
    `${ASSET}/cleansing-review.png`,
    `${ROOT}/docs/DATA_QUALITY_AUDIT_2026-08-11.md`,
  ]);
}

// 5. Scale
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "04 · 구현 규모", "과거 견적이 검색 가능한 가격 기준으로 전환됐습니다");
  const values = [
    ["구분", "현재 규모", "의미"],
    ["원본 품목행", "55,866개", "견적서에서 읽어낸 품목 기록"],
    ["표준 품목", "7,092개", "품명·규격·단위별 가격 기준"],
    ["가격 근거", "20,277건", "표준단가를 만든 원본 관측값"],
    ["날짜 있는 근거", "15,632건", "물가 보정에 활용 가능한 후보"],
    ["검토 대기", "7,755건", "자동 반영하지 않은 안전 구간"],
  ];
  const t = s.tables.add({ rows: values.length, columns: 3, left: 56, top: 180, width: 574, height: 360, columnWidths: [154, 120, 300], values });
  styleTable(t, { fontSize: 14 });
  t.rows[0].height = 46;
  for (let i = 1; i < values.length; i++) t.rows[i].height = 62;
  t.cells.block({ row: 1, column: 1, rowCount: 5, columnCount: 1 }).assign({ textStyle: { fontFamily: FONT, fontSize: 18, bold: true, color: C.navy } });
  await addImage(s, path.join(ASSET, "standard-db.png"), 668, 182, 542, 360, { crop: { left: 0.03, top: 0.22, right: 0.02, bottom: 0.09 }, alt: "표준 DB 목록 화면" });
  box(s, 668, 568, 542, 62, C.pale, 10, "none");
  textBox(s, "핵심", 686, 580, 50, 20, { size: 12, bold: true, color: C.navy });
  textBox(s, "초창기 엑셀을 복사한 것이 아니라 원본 견적에서 다시 구축", 748, 574, 438, 34, { size: 16, bold: true, color: C.ink });
  footer(s, 5);
  notes(s, "수치는 현재 로컬 운영 DB와 2026-08-11 인수인계 문서를 기준으로 정리했습니다. 표는 PowerPoint에서 직접 수정할 수 있습니다.", [
    `${ROOT}/docs/HANDOFF_2026-08-11.md`,
    `${ROOT}/docs/DATA_QUALITY_AUDIT_2026-08-11.md`,
    `${ASSET}/standard-db.png`,
  ]);
}

// 6. Standard DB evidence
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "05 · 구현 화면", "표준 DB는 숫자만 저장하지 않고 원본까지 역추적합니다");
  await addImage(s, path.join(ASSET, "standard-db-detail.png"), 56, 176, 760, 430, { fit: "contain", alt: "표준 품목 가격 근거와 변경 이력" });
  const callouts = [
    ["가격 범위", "최저 · 중앙값 · 평균 · 최고", C.navy],
    ["원본 근거", "견적일 · 파일 · 시트 · 행", C.cyan],
    ["감사 이력", "표준단가가 바뀐 시점과 근거 수", C.green],
  ];
  callouts.forEach((c, i) => {
    box(s, 858, 184 + i * 132, 340, 106, i === 0 ? C.pale : C.pale2, 12, C.line);
    pill(s, c[0], 878, 202 + i * 132, 104, i === 0 ? C.navy : "#DFF6F1", i === 0 ? C.white : c[2]);
    textBox(s, c[1], 878, 242 + i * 132, 292, 36, { size: 18, bold: true, color: C.ink });
  });
  textBox(s, "결과: 담당자가 ‘왜 이 가격인가’를 바로 설명할 수 있습니다.", 858, 598, 348, 46, { size: 16, bold: true, color: C.navy });
  footer(s, 6);
  notes(s, "표준 품목을 선택하면 가격 범위, 원본 위치, 공급사·제조사 확인 여부, 변경 이력이 함께 보입니다. 임원이 볼 핵심은 가격의 설명 가능성입니다.", [
    `${ASSET}/standard-db-detail.png`,
    `${ROOT}/docs/HANDOFF_2026-08-11.md`,
  ]);
}

// 7. Human review
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "06 · 통제와 신뢰", "애매한 값은 자동 가격 기준에서 제외하고 사람이 확인합니다");
  await addImage(s, path.join(ASSET, "cleansing-detail.png"), 56, 174, 690, 430, { crop: { left: 0, top: 0, right: 0, bottom: 0.11 }, alt: "원본 견적과 정제 판단 화면" });
  const values = [
    ["데이터 상태", "시스템 처리", "운영 의미"],
    ["확실함", "자동 반영", "표준 가격 근거로 사용"],
    ["애매함", "검토 대기", "담당자가 원본 확인"],
    ["근거 없음", "판정 제외", "가격을 임의로 만들지 않음"],
  ];
  const t = s.tables.add({ rows: 4, columns: 3, left: 784, top: 190, width: 424, height: 242, columnWidths: [108, 108, 208], values });
  styleTable(t, { fontSize: 13 });
  t.rows[0].height = 44; for (let i = 1; i < 4; i++) t.rows[i].height = 66;
  t.getCell(1, 0).fill = "#E4F6F0"; t.getCell(2, 0).fill = "#FFF3D9"; t.getCell(3, 0).fill = "#FCE7E9";
  t.getCell(1, 1).text.style = { fontFamily: FONT, fontSize: 14, bold: true, color: C.green };
  t.getCell(2, 1).text.style = { fontFamily: FONT, fontSize: 14, bold: true, color: C.amber };
  t.getCell(3, 1).text.style = { fontFamily: FONT, fontSize: 14, bold: true, color: C.red };
  box(s, 784, 468, 424, 136, C.navy, 12, "none");
  textBox(s, "안전 장치", 808, 486, 100, 22, { size: 13, bold: true, color: C.mint });
  textBox(s, "모르는 가격을 추정해 채우지 않고\n‘판정 대기’로 명확히 남깁니다.", 808, 520, 362, 58, { size: 20, bold: true, color: C.white });
  footer(s, 7);
  notes(s, "정제 화면에서 원본 셀을 직접 보고 포함 또는 제외를 결정합니다. 자동화의 범위를 제한하는 것이 데이터 신뢰성의 핵심입니다.", [
    `${ASSET}/cleansing-detail.png`,
    `${ROOT}/docs/DECISIONS.md`,
  ]);
}

// 8. Two analyses
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "07 · 신규 견적 분석", "신규 견적 한 건으로 두 가지 결과를 제공합니다");
  pill(s, "① 가격 적정성", 58, 170, 132, C.navy, C.white);
  pill(s, "② 구매 목표가", 650, 170, 132, C.cyan, C.white);
  await addImage(s, path.join(ASSET, "analysis-threshold.png"), 56, 212, 566, 274, { crop: { left: 0.01, top: 0.28, right: 0.01, bottom: 0.12 }, alt: "가격 적정성 분석 결과" });
  await addImage(s, path.join(ASSET, "analysis-target.png"), 648, 212, 566, 274, { crop: { left: 0.01, top: 0.28, right: 0.01, bottom: 0.12 }, alt: "구매 목표가 분석 결과" });
  const values = [
    ["업무 질문", "판단 기준", "결과"],
    ["이 견적은 비싼가?", "표준 중앙값 + 설정 임계값", "적정 · 주의 · 고가 · 저가"],
    ["얼마에 사야 하는가?", "과거 단가를 현재 물가로 보정", "항목별 목표가 · 목표 총액"],
  ];
  const t = s.tables.add({ rows: 3, columns: 3, left: 142, top: 520, width: 996, height: 116, columnWidths: [250, 366, 380], values });
  styleTable(t, { fontSize: 14 }); t.rows[0].height = 38; t.rows[1].height = 39; t.rows[2].height = 39;
  t.getCell(1, 0).fill = C.pale; t.getCell(2, 0).fill = "#E4F6F0";
  footer(s, 8);
  notes(s, "가격 적정성 탭은 사용자가 설정한 임계값으로 고가·적정·저가를 판정합니다. 구매 목표가 탭은 과거 단가를 현재 생산자물가로 보정합니다.", [
    `${ASSET}/analysis-threshold.png`,
    `${ASSET}/analysis-target.png`,
    `${ROOT}/docs/HANDOFF_2026-08-11.md`,
  ]);
}

// 9. Target price
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "08 · 구매 목표가", "2016년 가격을 2026년 가치로 환산해 구매 목표가를 제시합니다");
  box(s, 56, 178, 610, 212, C.pale, 16, "none");
  textBox(s, "과거 원본 단가", 82, 204, 160, 28, { size: 18, bold: true, color: C.ink, align: "center" });
  textBox(s, "×", 256, 201, 40, 32, { size: 26, bold: true, color: C.cyan, align: "center" });
  box(s, 306, 192, 310, 62, C.white, 10, C.line);
  textBox(s, "2026년 생산자물가지수", 322, 198, 280, 21, { size: 15, bold: true, color: C.navy, align: "center" });
  textBox(s, "2016년 생산자물가지수", 322, 224, 280, 21, { size: 15, color: C.muted, align: "center" });
  s.shapes.add({ geometry: "line", position: { left: 332, top: 222, width: 260, height: 0 }, fill: "none", line: { style: "solid", fill: C.navy, width: 1 } });
  textBox(s, "↓", 306, 270, 60, 38, { size: 28, bold: true, color: C.cyan, align: "center" });
  textBox(s, "보정 단가", 356, 274, 150, 28, { size: 22, bold: true, color: C.navy });
  textBox(s, "여러 과거 근거를 각각 보정한 뒤 중앙값을 구매 목표가로 사용", 82, 334, 540, 34, { size: 15, color: C.text });
  await addImage(s, USER_PHOTO, 704, 176, 510, 286, { fit: "cover", crop: { left: 0.03, top: 0.23, right: 0.02, bottom: 0.19 }, alt: "KOSIS 생산자물가지수 조회 화면 사진" });
  const values = [
    ["구분", "출처", "적용 방식"],
    ["물가 기준", "KOSIS 생산자물가지수", "기준월→현재월 지수 비율"],
    ["현재 기준", "2026년 6월 · 130.03", "2020년=100 공식 월별 지수"],
    ["대표값", "연결된 과거 견적 근거", "보정 단가의 중앙값"],
  ];
  const t = s.tables.add({ rows: 4, columns: 3, left: 104, top: 488, width: 1072, height: 150, columnWidths: [180, 360, 532], values });
  styleTable(t, { fontSize: 14 }); t.rows[0].height = 40; for (let i = 1; i < 4; i++) t.rows[i].height = 37;
  footer(s, 9);
  notes(s, "예시는 2016년 견적 단가에 2026년 지수와 2016년 지수의 비율을 곱하는 방식입니다. 실제 계산은 견적서에서 날짜가 직접 확인된 근거만 월별로 적용합니다.", [
    "https://kosis.kr/statHtml/statHtml.do?orgId=301&tblId=DT_404Y014",
    `${USER_PHOTO} (사용자 제공 이미지)`,
    `${ROOT}/docs/HANDOFF_2026-08-11.md`,
  ]);
}

// 10. Utilization
{
  const s = p.slides.add(); s.background.fill = C.white; header(s, "09 · 활용성", "검토 속도보다 ‘협상 가능한 근거’를 만드는 시스템입니다");
  const outcomes = [
    ["검토시간 단축", "비교 자료를 찾는 시간을 줄임"],
    ["담당자 편차 축소", "동일 기준과 임계값으로 판정"],
    ["협상 근거 표준화", "목표가와 원본 근거를 함께 제시"],
    ["데이터의 지속 성장", "확정 견적이 다음 기준가격으로 축적"],
  ];
  outcomes.forEach((o, i) => {
    const x = 56 + (i % 2) * 306;
    const y = 176 + Math.floor(i / 2) * 134;
    box(s, x, y, 282, 110, i === 2 ? C.navy : C.pale, 14, "none");
    textBox(s, `0${i + 1}`, x + 18, y + 16, 36, 20, { size: 11, bold: true, color: i === 2 ? C.mint : C.cyan });
    textBox(s, o[0], x + 60, y + 13, 200, 28, { size: 18, bold: true, color: i === 2 ? C.white : C.ink });
    textBox(s, o[1], x + 22, y + 55, 238, 38, { size: 14, color: i === 2 ? "#D7E4FF" : C.muted });
  });
  textBox(s, "시범운영에서 측정할 KPI", 690, 176, 450, 28, { size: 21, bold: true, color: C.navy });
  const values = [
    ["지표", "측정 방법", "목적"],
    ["평균 검토시간", "업로드→1차 판정 시간", "업무시간 절감"],
    ["목표가 산출률", "목표가 표시 품목 ÷ 전체", "데이터 활용 범위"],
    ["협상 절감액", "최초 견적−최종 합의가", "직접 재무 효과"],
    ["판정 수정률", "담당자 수정 건 ÷ 자동 판정", "기준 신뢰도"],
  ];
  const t = s.tables.add({ rows: 5, columns: 3, left: 688, top: 218, width: 528, height: 284, columnWidths: [144, 216, 168], values });
  styleTable(t, { fontSize: 13 }); t.rows[0].height = 42; for (let i = 1; i < 5; i++) t.rows[i].height = 58;
  box(s, 688, 528, 528, 96, C.pale2, 12, C.line);
  textBox(s, "성과 수치는 아직 가정하지 않습니다.", 712, 545, 440, 26, { size: 17, bold: true, color: C.ink });
  textBox(s, "실제 견적 시범운영으로 기준선을 먼저 측정합니다.", 712, 577, 450, 24, { size: 14, color: C.muted });
  footer(s, 10);
  notes(s, "활용 가치는 단순히 빠른 판정이 아니라 협상 근거의 표준화입니다. 효과 수치는 과장하지 않고 실제 파일럿에서 측정합니다.", [
    `${ROOT}/docs/HANDOFF_2026-08-11.md`,
    `${ROOT}/docs/DECISIONS.md`,
  ]);
}

// 11. Conclusion
{
  const s = p.slides.add(); s.background.fill = C.pale2; header(s, "10 · 결론과 요청", "다음 단계는 추가 개발보다 10~20건 실견적 시범운영입니다");
  textBox(s, "현재 구현도", 56, 174, 260, 28, { size: 20, bold: true, color: C.navy });
  const values = [
    ["영역", "상태", "비고"],
    ["표준 DB·원본 근거", "완료", "로컬 운영 가능"],
    ["신규 견적 적정성", "완료", "임계값 조정 가능"],
    ["물가연동 목표가", "완료", "KOSIS 월별 지수 적용"],
    ["시장가 조회", "보완", "DeviceMart·Mouser 성공률 개선"],
    ["보안 원본 53개", "사내 작업", "보안해제본 재수집"],
    ["서버·권한 체계", "다음 단계", "파일럿 KPI 후 결정"],
  ];
  const t = s.tables.add({ rows: 7, columns: 3, left: 56, top: 218, width: 640, height: 352, columnWidths: [224, 112, 304], values });
  styleTable(t, { fontSize: 13 }); t.rows[0].height = 42; for (let i = 1; i < 7; i++) t.rows[i].height = 50;
  [1,2,3].forEach((r) => { t.getCell(r,1).fill = "#E4F6F0"; t.getCell(r,1).text.style = { fontFamily: FONT, fontSize: 13, bold: true, color: C.green }; });
  t.getCell(4,1).fill = "#FFF3D9"; t.getCell(4,1).text.style = { fontFamily: FONT, fontSize: 13, bold: true, color: C.amber };
  t.getCell(5,1).fill = "#FFF3D9"; t.getCell(5,1).text.style = { fontFamily: FONT, fontSize: 13, bold: true, color: C.amber };
  t.getCell(6,1).fill = C.pale; t.getCell(6,1).text.style = { fontFamily: FONT, fontSize: 13, bold: true, color: C.navy };
  box(s, 742, 176, 476, 394, C.navy, 16, "none", "shadow-sm");
  textBox(s, "의사결정 요청", 774, 202, 220, 26, { size: 15, bold: true, color: C.mint });
  const asks = [
    ["1", "설비구매팀 실견적 10~20건 시범운영 승인"],
    ["2", "사내에서 보안해제 원본 53개 재수집 지원"],
    ["3", "검토시간·목표가 산출률·절감액 측정 후 서버화 판단"],
  ];
  asks.forEach((a, i) => {
    box(s, 774, 250 + i * 86, 42, 42, C.white, 21, "none");
    textBox(s, a[0], 774, 250 + i * 86, 42, 42, { size: 17, bold: true, color: C.navy, align: "center" });
    textBox(s, a[1], 834, 244 + i * 86, 344, 56, { size: 17, bold: true, color: C.white });
  });
  textBox(s, "결론", 56, 600, 60, 20, { size: 12, bold: true, color: C.cyan });
  textBox(s, "과거 견적을 ‘보관 파일’에서 ‘협상 가능한 가격 자산’으로 전환했습니다.", 128, 586, 1060, 48, { size: 22, bold: true, color: C.ink });
  footer(s, 11);
  notes(s, "현재는 로컬에서 시범운영 가능한 단계입니다. 다음 의사결정은 더 많은 기능이 아니라 실제 견적을 통한 KPI 검증입니다.", [
    `${ROOT}/docs/HANDOFF_2026-08-11.md`,
    `${ROOT}/docs/DATA_QUALITY_AUDIT_2026-08-11.md`,
    `${ROOT}/docs/DECISIONS.md`,
  ]);
}

await fs.mkdir(OUT, { recursive: true });
for (const [i, slide] of p.slides.items.entries()) {
  const stem = `slide-${String(i + 1).padStart(2, "0")}`;
  const png = await p.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(path.join(OUT, `${stem}.png`), new Uint8Array(await png.arrayBuffer()));
  const layout = await slide.export({ format: "layout" });
  await fs.writeFile(path.join(OUT, `${stem}.layout.json`), await layout.text());
}
const montage = await p.export({ format: "webp", montage: true, scale: 0.45 });
await fs.writeFile(path.join(OUT, "deck-montage.webp"), new Uint8Array(await montage.arrayBuffer()));
const pptx = await PresentationFile.exportPptx(p);
await pptx.save(FINAL);
console.log(FINAL);
