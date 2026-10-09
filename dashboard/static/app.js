const swarm = document.getElementById("swarm");
const chart = document.getElementById("chart");
const clock = document.getElementById("clock");
const cards = {};
const X_MAX = 90;

function card(node) {
  if (cards[node]) return cards[node];
  const div = document.createElement("div");
  div.className = "card " + (node.startsWith("10.0.1.") ? "lan-a" : "lan-b");
  div.innerHTML = "<b>" + node + "</b><canvas width='128' height='128'></canvas><span></span>";
  swarm.appendChild(div);
  cards[node] = { ctx: div.querySelector("canvas").getContext("2d"), label: div.querySelector("span") };
  return cards[node];
}

function drawChart(rate) {
  const ctx = chart.getContext("2d");
  ctx.clearRect(0, 0, chart.width, chart.height);
  const ts = Object.keys(rate).map(Number).sort((a, b) => a - b);
  if (!ts.length) return;
  const max = Math.max(...ts.map(t => rate[t]));
  ctx.strokeStyle = "#4caf50";
  ctx.beginPath();
  ts.forEach((t, i) => {
    const x = t / X_MAX * chart.width;
    const y = chart.height - rate[t] / max * (chart.height - 20) - 5;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#9aa4b2";
  ctx.fillText((max / 1e6).toFixed(1) + " Mbit/s peak", 8, 14);
}

function render(data) {
  const owned = {};
  const rate = {};
  data.events.forEach(e => {
    if (e.type === "join") owned[e.node] = new Set();
    if (e.type === "have") owned[e.node].add(e.piece);
    if (e.type === "rate") {
      const s = Math.floor(e.t);
      rate[s] = (rate[s] || 0) + e.down_bps;
    }
  });
  Object.keys(owned).sort().forEach(node => {
    const c = card(node);
    c.ctx.clearRect(0, 0, 128, 128);
    c.ctx.fillStyle = "#4caf50";
    owned[node].forEach(p => c.ctx.fillRect((p % 16) * 8, Math.floor(p / 16) * 8, 7, 7));
    c.label.textContent = Math.round(owned[node].size / 256 * 100) + "% of 256 pieces";
  });
  drawChart(rate);
  clock.textContent = data.now.toFixed(0) + " s";
}

function poll() {
  fetch("/api/events").then(r => r.json()).then(render).catch(() => {});
}
setInterval(poll, 1000);
poll();
