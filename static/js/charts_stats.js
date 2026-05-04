function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function themeColors() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  return {
    text: cssVar("--chart-text", dark ? "#e8ecf4" : "#141a24"),
    muted: cssVar("--chart-muted", dark ? "#9aa3b5" : "#5c6578"),
    ok: cssVar("--chart-ok", dark ? "#81c784" : "#2e7d32"),
    bad: cssVar("--chart-bad", dark ? "#ff8a80" : "#c62828"),
    warn: cssVar("--chart-warn", dark ? "#ffb74d" : "#ef6c00"),
    grid: cssVar("--chart-grid", dark ? "rgba(255,255,255,0.08)" : "rgba(20,26,36,0.08)"),
    accent: cssVar("--chart-accent", dark ? "#7c9cff" : "#3d5afe"),
  };
}

function barColor(pct) {
  const c = themeColors();
  if (pct < 40) return c.bad;
  if (pct < 70) return c.warn;
  return c.ok;
}

function initCharts() {
  const el = document.getElementById("stats-chart-data");
  if (!el || typeof Chart === "undefined") return;
  let data;
  try {
    data = JSON.parse(el.textContent);
  } catch {
    return;
  }
  const c = themeColors();
  const commonOpts = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: c.text } },
    },
  };

  const pieEl = document.getElementById("chartPie");
  if (pieEl) {
    const total = (data.pie_correct || 0) + (data.pie_wrong || 0);
    if (!total) {
      pieEl.parentElement.innerHTML =
        '<p class="muted" style="text-align:center;padding:2rem 0">Нет ответов для диаграммы</p>';
    } else {
      new Chart(pieEl, {
      type: "doughnut",
      data: {
        labels: ["Верно", "Неверно"],
        datasets: [
          {
            data: [data.pie_correct || 0, data.pie_wrong || 0],
            backgroundColor: [c.ok, c.bad],
            borderWidth: 0,
          },
        ],
      },
      options: {
        ...commonOpts,
        cutout: "62%",
        plugins: {
          legend: { position: "bottom", labels: { color: c.text, padding: 16 } },
          title: {
            display: true,
            text: total ? `${data.pie_center}% верных` : "Нет данных",
            color: c.text,
            font: { size: 18, weight: "600" },
          },
        },
      },
    });
    }
  }

  const barEl = document.getElementById("chartBar");
  if (barEl && data.bar_labels && data.bar_labels.length) {
    const colors = (data.bar_pcts || []).map((p) => barColor(p));
    new Chart(barEl, {
      type: "bar",
      data: {
        labels: data.bar_labels,
        datasets: [
          {
            label: "% верных",
            data: data.bar_pcts || [],
            backgroundColor: colors,
            borderRadius: 8,
          },
        ],
      },
      options: {
        ...commonOpts,
        scales: {
          x: { ticks: { color: c.muted }, grid: { color: c.grid } },
          y: {
            min: 0,
            max: 100,
            ticks: { color: c.muted },
            grid: { color: c.grid },
          },
        },
        plugins: { legend: { display: false } },
      },
    });
  }

  const histEl = document.getElementById("chartHist");
  if (histEl && data.hist_labels && data.hist_labels.length) {
    new Chart(histEl, {
      type: "bar",
      data: {
        labels: data.hist_labels,
        datasets: [
          {
            label: "Участников",
            data: data.hist_counts || [],
            backgroundColor: c.accent,
            borderRadius: 6,
          },
        ],
      },
      options: {
        ...commonOpts,
        scales: {
          x: { ticks: { color: c.muted, maxRotation: 45 }, grid: { display: false } },
          y: { ticks: { color: c.muted }, grid: { color: c.grid } },
        },
        plugins: { legend: { display: false } },
      },
    });
  }
}

document.addEventListener("DOMContentLoaded", initCharts);
