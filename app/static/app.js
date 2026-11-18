const COLORS = ["#8aa4ab", "#0f6f78", "#c9842a", "#5c6b73"];
const LINE_COLORS = ["#0f6f78", "#c9842a", "#3d5a80", "#8d4e2a", "#2f7d4a"];
const state = { question: "", view: "auto", period: "2026-10", main: "", history: [] };

const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function labelMonth(ym) {
  const [year, month] = ym.split("-");
  return `${year}年${Number(month)}月`;
}

function push(role, text) {
  const node = document.createElement("div");
  node.className = `bubble ${role}`;
  node.textContent = text;
  $("#messages").appendChild(node);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return node;
}

function setChatHidden(hidden) {
  $(".shell").classList.toggle("chat-hidden", hidden);
  $("#chat-open").setAttribute("aria-pressed", String(!hidden));
  $("#chat-open").title = hidden ? "展开对话栏" : "隐藏对话栏";
  $("#chat-show").hidden = !hidden;
  if (!hidden) {
    $("#messages").scrollTop = $("#messages").scrollHeight;
    $("#chat").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function formatY(value, fmt) {
  if (value == null || Number.isNaN(value)) return "";
  if (fmt === "pct") return `${Number(value).toFixed(1)}%`;
  if (fmt === "pp") {
    const num = Number(value);
    return `${num > 0 ? "+" : ""}${num.toFixed(1)}pp`;
  }
  if (fmt === "money") return `${(Number(value) / 10000).toFixed(1)}万`;
  if (fmt === "days") return `${Number(value).toFixed(0)}天`;
  return String(value);
}

function sourceButton(provenance) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "src";
  button.textContent = "数字从哪来";
  button.addEventListener("click", () => openProvenance(provenance));
  return button;
}

function openProvenance(provenance) {
  if (!provenance) return;
  const body = $("#modal-body");
  const rows = provenance.rows || [];
  const keys = rows.length ? Object.keys(rows[0]) : [];
  const head = keys.map((key) => `<th>${esc(key)}</th>`).join("");
  const bodyRows = rows.map((row) => `<tr>${keys.map((key) => `<td>${esc(row[key])}</td>`).join("")}</tr>`).join("");
  body.innerHTML = `
    <p class="formula">${esc(provenance.formula || "")}</p>
    <ul>${(provenance.filters || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
    <ul>${(provenance.notes || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
    ${rows.length ? `<table><thead><tr>${head}</tr></thead><tbody>${bodyRows}</tbody></table>` : ""}
  `;
  $("#modal").hidden = false;
}

function axis(min, max, fmt, x, plot, innerH) {
  const ticks = 4;
  let marks = "";
  for (let i = 0; i <= ticks; i += 1) {
    const value = min + ((max - min) * i) / ticks;
    const y = plot.t + ((max - value) / (max - min)) * innerH;
    marks += `<line x1="${plot.l}" x2="${x}" y1="${y}" y2="${y}" stroke="#e6ecee" />`;
    marks += `<text x="${plot.l - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="#5c6f76">${esc(formatY(value, fmt))}</text>`;
  }
  return marks;
}

function scale(values, tight) {
  const nums = values.filter((value) => value != null && !Number.isNaN(value));
  if (!nums.length) return { min: 0, max: 1 };
  let min = Math.min(...nums);
  let max = Math.max(...nums);
  if (tight && min >= 0) {
    const span = max - min || Math.abs(max) || 1;
    min = Math.max(0, min - span * 0.45);
    max += span * 0.2;
  } else {
    min = Math.min(0, min);
    max = Math.max(0, max);
  }
  if (!Number.isFinite(min) || !Number.isFinite(max) || max === min) {
    min = 0;
    max = 1;
  }
  const span = max - min || 1;
  if (min < 0) min -= span * 0.08;
  if (!tight) max += span * 0.16;
  return { min, max };
}

function textWidth(name, fontSize) {
  let width = 0;
  for (const ch of String(name)) width += ch.charCodeAt(0) > 255 ? fontSize : fontSize * 0.55;
  return width;
}

function categoryLabels(names, xOf, axisY) {
  const fontSize = 11;
  const gap = names.length > 1 ? Math.abs(xOf(1) - xOf(0)) : 999;
  const widest = Math.max(1, ...names.map((name) => textWidth(name, fontSize)));
  const slant = names.length > 4 && widest > gap * 0.82;
  const angle = 42;
  const depth = slant ? Math.ceil(widest * Math.sin((angle * Math.PI) / 180) + 16) : 28;
  const marks = names.map((name, index) => {
    const x = xOf(index).toFixed(1);
    if (!slant) {
      return `<text x="${x}" y="${axisY + 16}" text-anchor="middle" font-size="${fontSize}" fill="#14262c" data-drill="${esc(name)}">${esc(name)}</text>`;
    }
    const y = axisY + 10;
    return `<text x="${x}" y="${y}" text-anchor="start" font-size="${fontSize}" fill="#14262c" data-drill="${esc(name)}" transform="rotate(${angle} ${x} ${y})">${esc(name)}</text>`;
  }).join("");
  return { marks, depth, slant, widest };
}

function groupedBar(chart) {
  const width = 640;
  const plot = { l: 54, r: 16, t: 12 };
  const innerH = 200;
  const cats = chart.categories || [];
  const series = chart.series || [];
  const widest = Math.max(1, ...cats.map((name) => textWidth(name, 11)), 1);
  const trialGap = (width - plot.l - plot.r) / Math.max(cats.length, 1);
  if (cats.length > 4 && widest > trialGap * 0.82) {
    plot.r += Math.ceil(widest * Math.cos((42 * Math.PI) / 180) - trialGap / 2 + 8);
  }
  const innerW = width - plot.l - plot.r;
  const band = innerW / Math.max(cats.length, 1);
  const xOf = (index) => plot.l + band * index + band / 2;
  const labels = categoryLabels(cats, xOf, plot.t + innerH);
  const height = plot.t + innerH + labels.depth;
  const { min, max } = scale(series.flatMap((item) => item.values || []));
  const yOf = (value) => plot.t + ((max - value) / (max - min)) * innerH;
  const group = band * 0.72;
  const barW = group / Math.max(series.length, 1);
  let shapes = axis(min, max, chart.y_format, width - plot.r, plot, innerH);
  const zero = yOf(0);
  shapes += `<line x1="${plot.l}" x2="${width - plot.r}" y1="${zero}" y2="${zero}" stroke="#9aadb3" />`;
  cats.forEach((name, index) => {
    const cx = plot.l + band * index + band / 2;
    if ((chart.highlight || []).includes(name)) {
      shapes += `<rect x="${plot.l + band * index + 2}" y="${plot.t}" width="${band - 4}" height="${innerH}" fill="rgba(180,35,24,0.08)" />`;
    }
    series.forEach((item, seriesIndex) => {
      const value = item.values[index];
      if (value == null) return;
      const x = cx - group / 2 + barW * seriesIndex;
      const y = yOf(Math.max(value, 0));
      const h = Math.abs(yOf(value) - zero);
      const muted = (chart.muted || []).includes(name);
      shapes += `<rect x="${x}" y="${Math.min(y, zero)}" width="${Math.max(barW - 2, 1)}" height="${Math.max(h, 0)}" fill="${COLORS[seriesIndex % COLORS.length]}" opacity="${muted ? 0.35 : 1}" data-drill="${esc(name)}" />`;
    });
  });
  shapes += labels.marks;
  return `<svg viewBox="0 0 ${width} ${height}">${shapes}</svg>`;
}

function lineChart(chart) {
  const width = 640;
  const plot = { l: 54, r: 16, t: 12 };
  const innerH = 200;
  const cats = chart.categories || [];
  const series = chart.series || [];
  const innerW = width - plot.l - plot.r;
  const inset = 30;
  const xOf = (index) => plot.l + (cats.length <= 1 ? innerW / 2 : inset + ((innerW - inset * 2) * index) / (cats.length - 1));
  const labels = categoryLabels(cats, xOf, plot.t + innerH);
  const height = plot.t + innerH + labels.depth;
  const { min, max } = scale(series.flatMap((item) => item.values || []), chart.y_format === "pct");
  const yOf = (value) => plot.t + ((max - value) / (max - min)) * innerH;
  let shapes = axis(min, max, chart.y_format, width - plot.r, plot, innerH);
  series.forEach((item, seriesIndex) => {
    const color = LINE_COLORS[seriesIndex % LINE_COLORS.length];
    const pts = item.values.map((value, index) => (value == null ? null : `${xOf(index)},${yOf(value)}`)).filter(Boolean);
    shapes += `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts.join(" ")}" />`;
    item.values.forEach((value, index) => {
      if (value == null) return;
      shapes += `<circle cx="${xOf(index)}" cy="${yOf(value)}" r="3" fill="${color}" />`;
    });
  });
  shapes += labels.marks;
  return `<svg viewBox="0 0 ${width} ${height}">${shapes}</svg>`;
}

function dualChart(chart) {
  const width = 640;
  const plot = { l: 54, r: 52, t: 22 };
  const innerH = 200;
  const cats = chart.categories || [];
  const innerW = width - plot.l - plot.r;
  const band = innerW / Math.max(cats.length, 1);
  const xOf = (index) => plot.l + band * index + band / 2;
  const labels = categoryLabels(cats, xOf, plot.t + innerH);
  const height = plot.t + innerH + labels.depth;
  const bars = chart.bars || [];
  const line = chart.line || { values: [] };
  const left = scale(bars.flatMap((item) => item.values || []));
  const right = scale(line.values || [], (line.format || "pct") === "pct");
  const yLeft = (value) => plot.t + ((left.max - value) / (left.max - left.min)) * innerH;
  const yRight = (value) => plot.t + ((right.max - value) / (right.max - right.min)) * innerH;
  const group = band * 0.62;
  const barW = group / Math.max(bars.length, 1);
  let shapes = axis(left.min, left.max, "money", width - plot.r, plot, innerH);
  for (let i = 0; i <= 4; i += 1) {
    const value = right.min + ((right.max - right.min) * i) / 4;
    const y = plot.t + ((right.max - value) / (right.max - right.min)) * innerH;
    shapes += `<text x="${width - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="#b42318">${esc(formatY(value, line.format || "pct"))}</text>`;
  }
  cats.forEach((name, index) => {
    const cx = plot.l + band * index + band / 2;
    bars.forEach((item, seriesIndex) => {
      const value = item.values[index];
      if (value == null) return;
      const x = cx - group / 2 + barW * seriesIndex;
      const zero = yLeft(0);
      const y = yLeft(value);
      shapes += `<rect x="${x}" y="${Math.min(y, zero)}" width="${Math.max(barW - 2, 1)}" height="${Math.abs(y - zero)}" fill="${COLORS[seriesIndex % COLORS.length]}" />`;
    });
  });
  shapes += labels.marks;
  const pts = (line.values || []).map((value, index) => (value == null ? null : `${plot.l + band * index + band / 2},${yRight(value)}`)).filter(Boolean);
  shapes += `<polyline fill="none" stroke="#b42318" stroke-width="2" points="${pts.join(" ")}" />`;
  shapes += `<text x="${width - 6}" y="12" text-anchor="end" font-size="10" fill="#b42318">${esc(line.name || "")}</text>`;
  return `<svg viewBox="0 0 ${width} ${height}">${shapes}</svg>`;
}

function stackedChart(chart) {
  const width = 640;
  const height = 300;
  const plot = { l: 54, r: 12, t: 12, b: 64 };
  const innerW = width - plot.l - plot.r;
  const innerH = height - plot.t - plot.b;
  const cats = chart.categories || [];
  const rows = chart.rows || [];
  const segments = chart.segments || [];
  const palette = ["#8fb9c4", "#e6c35c", "#e09f3e", "#d46a3a", "#8d2f2f"];
  const totals = rows.map((row) => (row.values || []).reduce((sum, value) => sum + (value || 0), 0));
  const max = Math.max(1, ...totals) * 1.1;
  const yOf = (value) => plot.t + ((max - value) / max) * innerH;
  const band = innerW / Math.max(cats.length, 1);
  let shapes = axis(0, max, "money", width - plot.r, plot, innerH);
  rows.forEach((row, index) => {
    let acc = 0;
    const x = plot.l + band * index + band * 0.2;
    (row.values || []).forEach((value, seg) => {
      const h = ((value || 0) / max) * innerH;
      const y = yOf(acc + (value || 0));
      shapes += `<rect x="${x}" y="${y}" width="${band * 0.6}" height="${Math.max(h, 0)}" fill="${palette[seg % palette.length]}" />`;
      acc += value || 0;
    });
    shapes += `<text x="${x + band * 0.3}" y="${height - 28}" text-anchor="middle" font-size="10" fill="#14262c" transform="rotate(-25 ${x + band * 0.3} ${height - 28})">${esc(row.name)}</text>`;
  });
  const legend = segments.map((name, index) => `<span><i class="swatch" style="background:${palette[index % palette.length]}"></i>${esc(name)}</span>`).join("");
  return `<svg viewBox="0 0 ${width} ${height}">${shapes}</svg><div class="legend">${legend}</div>`;
}

function renderChart(chart) {
  const card = document.createElement("article");
  card.className = "card";
  const head = document.createElement("h3");
  head.textContent = chart.title || "";
  if (chart.provenance) head.appendChild(sourceButton(chart.provenance));
  card.appendChild(head);
  let graphic = "";
  if (chart.type === "grouped_bar") graphic = groupedBar(chart);
  else if (chart.type === "line") graphic = lineChart(chart);
  else if (chart.type === "dual") graphic = dualChart(chart);
  else if (chart.type === "stacked") graphic = stackedChart(chart);
  else if (chart.type === "hbar") graphic = "";
  else if (chart.type === "table") graphic = "";
  if (graphic) {
    const wrap = document.createElement("div");
    wrap.innerHTML = graphic;
    wrap.querySelectorAll("[data-drill]").forEach((node) => {
      node.style.cursor = chart.drill ? "pointer" : "default";
      if (!chart.drill) return;
      node.addEventListener("click", () => {
        const name = node.getAttribute("data-drill");
        if (chart.drill === "trend") ask(`近6个月${name}销售回款率怎么走？`);
        else ask(`只看${name}`);
      });
    });
    card.appendChild(wrap);
  }
  if (chart.type === "hbar") card.appendChild(hbar(chart));
  if (chart.type === "table") card.appendChild(tableEl(chart.columns, chart.rows));
  const series = chart.series || [];
  if (series.length && chart.type !== "stacked") {
    const legend = document.createElement("div");
    legend.className = "legend";
    const palette = chart.type === "line" ? LINE_COLORS : COLORS;
    series.forEach((item, index) => {
      const span = document.createElement("span");
      span.innerHTML = `<i class="swatch" style="background:${palette[index % palette.length]}"></i>${esc(item.name)}`;
      legend.appendChild(span);
    });
    card.appendChild(legend);
  }
  if (chart.footnote) {
    const note = document.createElement("p");
    note.className = "footnote";
    note.textContent = chart.footnote;
    card.appendChild(note);
  }
  return card;
}

function hbar(chart) {
  const wrap = document.createElement("div");
  const rows = chart.rows || [];
  const max = Math.max(1, ...rows.map((row) => Math.abs(row.value || 0)));
  rows.forEach((row) => {
    const line = document.createElement("div");
    line.className = "hbar";
    const width = `${(Math.abs(row.value || 0) / max) * 100}%`;
    const tone = (row.value || 0) < 0 ? "bad" : "good";
    line.innerHTML = `<span>${esc(row.name)}</span><span class="track"><span class="fill ${tone}" style="width:${width}"></span></span><span>${esc(row.text || "")}</span>`;
    wrap.appendChild(line);
  });
  return wrap;
}

function sortNumber(text) {
  const raw = String(text ?? "").trim();
  if (!raw || raw === "—") return null;
  const part = raw.includes("→") ? raw.split("→").pop() : raw;
  const cleaned = part.replace(/,/g, "").trim().replace(/(元|%|pp|天)$/, "");
  if (!/^[+-]?\d+(?:\.\d+)?$/.test(cleaned)) return null;
  return Number(cleaned);
}

function sortTableRows(rows, key, direction) {
  const compare = (left, right) => {
    if (left == null && right == null) return 0;
    if (left == null) return 1;
    if (right == null) return -1;
    return (left - right) * direction;
  };
  const hasTotal = rows.some((row) => row["月份"] === "区间合计" && row["范围"]);
  if (!hasTotal) return rows.slice().sort((a, b) => compare(sortNumber(a[key]), sortNumber(b[key])));
  const groups = [];
  const index = new Map();
  rows.forEach((row) => {
    const name = row["范围"] ?? "";
    if (!index.has(name)) {
      index.set(name, groups.length);
      groups.push([]);
    }
    groups[index.get(name)].push(row);
  });
  groups.sort((a, b) => {
    const totalOf = (group) => group.find((row) => row["月份"] === "区间合计") || group[group.length - 1];
    return compare(sortNumber(totalOf(a)[key]), sortNumber(totalOf(b)[key]));
  });
  return groups.flat();
}

function tableEl(columns, rows) {
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  const body = document.createElement("tbody");
  const source = rows || [];
  const sortable = new Set((columns || []).filter((col) => source.some((row) => sortNumber(row[col.key]) != null)).map((col) => col.key));
  const state = { key: null, direction: -1 };

  function paint() {
    headRow.replaceChildren();
    (columns || []).forEach((col) => {
      const cell = document.createElement("th");
      if (sortable.has(col.key)) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "sort";
        const mark = state.key === col.key ? (state.direction < 0 ? " ↓" : " ↑") : " ↕";
        button.textContent = `${col.label}${mark}`;
        button.setAttribute("aria-sort", state.key === col.key ? (state.direction < 0 ? "descending" : "ascending") : "none");
        button.addEventListener("click", () => {
          state.direction = state.key === col.key ? -state.direction : -1;
          state.key = col.key;
          paint();
        });
        cell.appendChild(button);
      } else {
        cell.textContent = col.label;
      }
      headRow.appendChild(cell);
    });
    const ordered = state.key ? sortTableRows(source, state.key, state.direction) : source;
    body.replaceChildren();
    ordered.forEach((row) => {
      const tr = document.createElement("tr");
      (columns || []).forEach((col) => {
        const td = document.createElement("td");
        if (col.drill === "customer" && row[col.key]) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "drill";
          button.textContent = row[col.key];
          button.addEventListener("click", () => {
            const name = row._客户 || String(row[col.key]).replace(/（[^）]*）$/, "");
            const monthLabel = row["月份"] && row["月份"] !== "区间合计" ? row["月份"] : (row["期末"] || "");
            const month = monthLabel ? `在${monthLabel}` : "";
            ask(`按SKU展示${name}${month}的销售和回款`);
          });
          td.appendChild(button);
        } else {
          td.textContent = row[col.key] ?? "";
        }
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  head.appendChild(headRow);
  table.append(head, body);
  paint();
  return table;
}

function renderReport(data) {
  const root = $("#report");
  root.innerHTML = "";
  root.scrollTop = 0;
  const scope = document.createElement("div");
  scope.className = "scope-row";
  const label = document.createElement("span");
  label.textContent = `范围：${data.filters?.label || "公司合计"} · ${data.period_label || ""}${data.compare_label ? ` vs ${data.compare_label}` : ""}`;
  scope.appendChild(label);
  if (data.question_id !== "p0_main") {
    const back = document.createElement("button");
    back.type = "button";
    back.className = "linkish";
    back.textContent = "返回主问";
    back.addEventListener("click", () => ask(state.main, { view: "auto" }));
    scope.appendChild(back);
  }
  root.appendChild(scope);

  const sentences = data.conclusion?.sentences || [];
  const conclusion = document.createElement("section");
  conclusion.className = "conclusion";
  const copy = document.createElement("div");
  sentences.forEach((sentence) => {
    const p = document.createElement("p");
    p.textContent = sentence.text;
    if (sentence.provenance) p.appendChild(sourceButton(sentence.provenance));
    copy.appendChild(p);
  });
  if (sentences.length) conclusion.appendChild(copy);
  if (data.view_switch) {
    const sw = document.createElement("div");
    sw.className = "switch";
    data.view_switch.options.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = option.label;
      button.setAttribute("aria-pressed", String(option.id === data.view_switch.current));
      button.addEventListener("click", () => ask(state.question || state.main, { view: option.id, quiet: true }));
      sw.appendChild(button);
    });
    conclusion.appendChild(sw);
  }
  if (sentences.length || data.view_switch) root.appendChild(conclusion);

  const kpis = document.createElement("div");
  kpis.className = "kpis";
  (data.kpis || []).forEach((kpi) => {
    const card = document.createElement("article");
    card.className = `kpi${kpi.auxiliary ? " aux" : ""}`;
    const labelRow = document.createElement("div");
    labelRow.className = "label";
    labelRow.innerHTML = `<span>${esc(kpi.label)}${kpi.auxiliary ? " · 辅助" : ""}</span>`;
    if (kpi.provenance) labelRow.appendChild(sourceButton(kpi.provenance));
    const value = document.createElement("div");
    value.className = "value";
    value.textContent = kpi.value_text;
    const mom = document.createElement("div");
    mom.className = `mom ${kpi.tone || "flat"}`;
    mom.textContent = `环比 ${kpi.mom_text || "—"}`;
    card.append(labelRow, value, mom);
    if (kpi.hint) {
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = kpi.hint;
      card.appendChild(hint);
    }
    kpis.appendChild(card);
  });
  root.appendChild(kpis);

  const charts = document.createElement("div");
  charts.className = "charts";
  (data.charts || []).forEach((chart) => charts.appendChild(renderChart(chart)));
  root.appendChild(charts);
  if (data.attribution) root.appendChild(renderAttribution(data.attribution));
}

function renderSheet(data) {
  const root = $("#report");
  root.innerHTML = "";
  root.scrollTop = 0;
  const head = document.createElement("div");
  head.className = "sheet-head";
  const title = document.createElement("h2");
  title.textContent = data.page_title || "名单";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "linkish";
  back.textContent = "返回主问";
  back.addEventListener("click", () => ask(state.main, { view: "auto" }));
  head.append(title, back);
  root.appendChild(head);
  const text = (data.conclusion?.sentences || []).map((sentence) => sentence.text).filter(Boolean).join("");
  if (text) {
    const paragraph = document.createElement("p");
    paragraph.className = "sheet-text";
    paragraph.textContent = text;
    root.appendChild(paragraph);
  }
  (data.charts || []).filter((chart) => chart.type === "table").forEach((chart) => {
    root.appendChild(tableEl(chart.columns, chart.rows));
  });
}

function renderAttribution(block) {
  const section = document.createElement("section");
  section.className = "block";
  const head = document.createElement("h3");
  head.textContent = block.title || "归因";
  if (block.provenance) head.appendChild(sourceButton(block.provenance));
  section.appendChild(head);
  const split = document.createElement("div");
  split.className = "split";
  const dec = block.decomposition || {};
  split.innerHTML = `
    <div class="metric-pill"><span>回款额变化贡献</span><b class="${String(dec.collection_text || "").startsWith("-") ? "bad" : "good"}">${esc(dec.collection_text)}</b></div>
    <div class="metric-pill"><span>净销售额变化贡献</span><b class="${String(dec.sales_text || "").startsWith("-") ? "bad" : "good"}">${esc(dec.sales_text)}</b></div>
  `;
  section.appendChild(split);
  const detail = document.createElement("p");
  detail.className = "detail";
  detail.textContent = dec.detail || "";
  section.appendChild(detail);

  const cross = document.createElement("div");
  cross.className = "block";
  cross.innerHTML = `<h3>${esc(block.cross_title || "交叉贡献")}</h3>`;
  cross.appendChild(hbar({
    rows: (block.cross_rows || []).map((row) => ({ name: row.name, value: row.pp_num, text: `${row.pp_text} · ${row.amount_text}` })),
  }));
  section.appendChild(cross);

  const agents = document.createElement("div");
  agents.innerHTML = "<h3>拖累代理商</h3>";
  agents.appendChild(tableEl(
    [
      { key: "name", label: "代理商", drill: "customer" },
      { key: "tier", label: "等级" },
      { key: "region", label: "大区" },
      { key: "pp_text", label: "贡献" },
      { key: "net_text", label: "净销售" },
      { key: "collection_text", label: "回款额" },
    ],
    block.agents || [],
  ));
  section.appendChild(agents);

  const aging = document.createElement("div");
  aging.innerHTML = "<h3>账龄迁移</h3>";
  aging.appendChild(tableEl(
    [
      { key: "bucket", label: "账龄" },
      { key: "prev", label: "上期金额" },
      { key: "curr", label: "本期金额" },
      { key: "share_prev", label: "上期占比" },
      { key: "share_curr", label: "本期占比" },
      { key: "share_delta", label: "占比变动" },
    ],
    block.aging || [],
  ));
  section.appendChild(aging);

  if ((block.large_invoices || []).length) {
    const invoices = document.createElement("div");
    invoices.innerHTML = "<h3>本月到期金额靠前的发票</h3>";
    invoices.appendChild(tableEl(
      [
        { key: "customer", label: "代理商" },
        { key: "tier", label: "等级" },
        { key: "line", label: "产品线" },
        { key: "due", label: "到期日" },
        { key: "original", label: "原净额" },
        { key: "outstanding", label: "月末未收回" },
      ],
      block.large_invoices,
    ));
    section.appendChild(invoices);
  }

  const aux = document.createElement("div");
  aux.innerHTML = `<h3>辅助对照</h3>${(block.auxiliary_notes || []).map((note) => `<p class="detail">${esc(note)}</p>`).join("")}`;
  section.appendChild(aux);

  const narrative = document.createElement("div");
  narrative.className = "narrative block";
  const nHead = document.createElement("h3");
  nHead.textContent = "原因说明";
  if (block.provenance) nHead.appendChild(sourceButton(block.provenance));
  narrative.appendChild(nHead);
  if ((block.signals || []).length) {
    const tags = document.createElement("div");
    tags.className = "tags";
    block.signals.forEach((tag) => {
      const span = document.createElement("span");
      span.className = "tag";
      span.textContent = tag;
      tags.appendChild(span);
    });
    const cap = document.createElement("div");
    cap.className = "hint";
    cap.textContent = "计算信号（由阈值从上面的数字得出）";
    narrative.appendChild(cap);
    narrative.appendChild(tags);
  }
  const body = document.createElement("p");
  body.className = "detail";
  const told = block.narrative || {};
  body.textContent = told.available ? told.text : (told.message || "未生成原因说明。");
  narrative.appendChild(body);
  section.appendChild(narrative);
  return section;
}

async function ask(question, opts = {}) {
  const text = (question || "").trim();
  if (!text) return;
  const view = opts.view || "auto";
  state.question = text;
  state.view = view;
  const history = state.history.slice(-6);
  if (!opts.quiet) push("user", text);
  const pending = opts.quiet ? null : push("assistant pending", "正在分析…");
  let data;
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, period: state.period, view, history }),
    });
    data = await response.json();
  } catch (error) {
    pending?.remove();
    push("assistant", "请求失败，请确认本地服务仍在运行。");
    return;
  }
  pending?.remove();
  if (!opts.quiet) {
    const reply = data.chat_reply || "暂不支持。";
    push("assistant", reply);
    state.history.push({ role: "user", text }, { role: "assistant", text: reply });
  }
  else if (opts.note) push("assistant", `已按${labelMonth(state.period)}重算当前问题。`);
  const show = !data.keep_report && (data.supported !== false || data.conclusion);
  if (show) (data.page === "sheet" ? renderSheet : renderReport)(data);
}

function renderChips(questions) {
  const box = $("#chips");
  box.innerHTML = "";
  questions.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = item.priority === "P0" ? "p0" : "";
    button.textContent = `${item.priority} ${item.text}`;
    button.addEventListener("click", () => ask(item.text, { view: "auto" }));
    box.appendChild(button);
  });
}

async function boot() {
  const meta = await (await fetch("/api/meta")).json();
  state.main = meta.main_question;
  state.period = meta.default_period;
  const select = $("#period");
  meta.periods.forEach((period) => {
    const option = document.createElement("option");
    option.value = period;
    option.textContent = labelMonth(period);
    if (period === meta.default_period) option.selected = true;
    select.appendChild(option);
  });
  select.addEventListener("change", () => {
    state.period = select.value;
    if (state.question) ask(state.question, { view: state.view, quiet: true, note: true });
  });
  renderChips(meta.questions);
  $("#ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#question");
    ask(input.value, { view: "auto" });
    input.value = "";
  });
  $("#chat-hide").addEventListener("click", () => setChatHidden(true));
  $("#chat-show").addEventListener("click", () => setChatHidden(false));
  $("#chat-open").addEventListener("click", () => {
    setChatHidden(!$(".shell").classList.contains("chat-hidden"));
  });
  $("#modal-close").addEventListener("click", () => { $("#modal").hidden = true; });
  $("#modal").addEventListener("click", (event) => {
    if (event.target.id === "modal") $("#modal").hidden = true;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") $("#modal").hidden = true;
  });
  await ask(meta.main_question, { view: "auto" });
}

boot();
