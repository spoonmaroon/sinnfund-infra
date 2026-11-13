// Heatmap page: one tile per holding, grouped by sector; area = gross value, colour = latest price move.
// Uses app.js globals ($, esc, money, num, safe, holdings).
const HM_FULL = 3; // a ±3% move gets full colour

// Same symbol across accounts becomes one tile; net value decides long/short, gross value decides size.
function hmSectors() {
  const tiles = new Map();
  for (const x of holdings) {
    const t = tiles.get(x.symbol) || { symbol: x.symbol, description: x.description, sector: x.sector, value: 0, size: 0, day: 0, pct: x._hasDay ? x._priceChangePct : null, accounts: new Set() };
    t.value += safe(x.market_value);
    t.size += Math.abs(safe(x.market_value));
    t.day += safe(x._dayChange);
    t.accounts.add(x.account_id);
    tiles.set(x.symbol, t);
  }
  const sectors = new Map();
  for (const t of tiles.values()) {
    if (!t.size) continue;
    const s = sectors.get(t.sector) || { name: t.sector, size: 0, day: 0, tiles: [] };
    s.size += t.size;
    s.day += t.day;
    s.tiles.push(t);
    sectors.set(t.sector, s);
  }
  const bySize = (a, b) => b.size - a.size;
  return [...sectors.values()].sort(bySize).map((s) => ({ ...s, tiles: s.tiles.sort(bySize) }));
}

// Squarified treemap (Bruls et al.): fill rows along the short side while tiles stay close to square.
// items must be sorted largest first; returns [{item, x, y, w, h}].
function hmSquarify(items, x, y, w, h) {
  const total = items.reduce((s, i) => s + i.size, 0);
  if (!total || w <= 0 || h <= 0) return [];
  const scale = (w * h) / total, out = [];
  const worst = (row, side) => {
    const areas = row.map((i) => i.size * scale), sum = areas.reduce((a, b) => a + b, 0);
    return Math.max((side * side * Math.max(...areas)) / (sum * sum), (sum * sum) / (side * side * Math.min(...areas)));
  };
  let rest = items;
  while (rest.length) {
    const side = Math.min(w, h);
    let row = [rest[0]];
    while (row.length < rest.length && worst([...row, rest[row.length]], side) <= worst(row, side)) row = [...row, rest[row.length]];
    const thick = row.reduce((s, i) => s + i.size * scale, 0) / side;
    let offset = 0;
    for (const item of row) {
      const len = (item.size * scale) / thick;
      out.push(w >= h ? { item, x, y: y + offset, w: thick, h: len } : { item, x: x + offset, y, w: len, h: thick });
      offset += len;
    }
    if (w >= h) { x += thick; w -= thick; } else { y += thick; h -= thick; }
    rest = rest.slice(row.length);
  }
  return out;
}

function hmColor(pct) {
  if (pct == null) return "#b8bec9";
  const t = Math.max(-1, Math.min(1, pct / HM_FULL)), [r, g, b] = t >= 0 ? [3, 152, 85] : [217, 45, 32];
  const mix = (c, base) => Math.round(base + (c - base) * Math.abs(t));
  return `rgb(${mix(r, 72)},${mix(g, 78)},${mix(b, 92)})`; // grey at 0, full green or red at ±3%
}

function hmTile({ item: t, x, y, w, h }) {
  const pct = t.pct == null ? "no price move" : `${t.pct >= 0 ? "+" : ""}${num.format(t.pct)}%`;
  const title = `${t.symbol}${t.value < 0 ? " (short)" : ""} · ${t.description}\n${t.sector}\n${money.format(t.value)} · ${pct} · ${money.format(t.day)} today\n${t.accounts.size} account${t.accounts.size === 1 ? "" : "s"}`;
  const font = Math.max(9, Math.min(22, w / 5, h / 3));
  const label = w > 34 && h > 20 ? `<strong style="font-size:${font.toFixed(0)}px">${esc(t.symbol)}${t.value < 0 ? " ·S" : ""}</strong>${h > 40 && w > 44 ? `<span>${esc(pct)}</span>` : ""}` : "";
  return `<div class="hm-tile" title="${esc(title)}" style="left:${x.toFixed(1)}px;top:${y.toFixed(1)}px;width:${w.toFixed(1)}px;height:${h.toFixed(1)}px;background:${hmColor(t.pct)}">${label}</div>`;
}

function renderHeatmap() {
  const box = $("heatmap"), sectors = hmSectors();
  if (!sectors.length) { box.innerHTML = '<div class="chart-empty">Waiting for holdings.</div>'; return; }
  const W = Number(box.clientWidth) || 900, H = Math.round(Math.max(420, Math.min(760, W * 0.6))), HEAD = 16;
  box.style.height = `${H}px`;
  box.innerHTML = hmSquarify(sectors, 0, 0, W, H).map(({ item: s, x, y, w, h }) => {
    const head = h > 60 && w > 60 ? HEAD : 0;
    const share = `${num.format((s.size / sectors.reduce((a, b) => a + b.size, 0)) * 100)}%`;
    return `<div class="hm-sector" style="left:${x.toFixed(1)}px;top:${y.toFixed(1)}px;width:${w.toFixed(1)}px;height:${h.toFixed(1)}px">${head ? `<div class="hm-head" title="${esc(s.name)} · ${share} of gross · ${money.format(s.day)} today">${esc(s.name)} <span>${share}</span></div>` : ""}${hmSquarify(s.tiles, 0, head, w, h - head).map(hmTile).join("")}</div>`;
  }).join("");
  const count = sectors.reduce((n, s) => n + s.tiles.length, 0);
  $("hmNote").textContent = `${count} holdings in ${sectors.length} sectors`;
}

new ResizeObserver(() => { if (!$("heatmapPage").hidden) renderHeatmap(); }).observe($("heatmap"));
