const THEME_KEY = "theme";
const MAX_OPTS = 6;

function createEditOptRow(index, qtype) {
  const div = document.createElement("div");
  div.className = "opt-editor";
  const inp = document.createElement("input");
  inp.type = "text";
  inp.name = "option_texts";
  inp.className = "input";
  inp.placeholder = `Вариант ${index}`;
  div.appendChild(inp);
  if (qtype === "single_choice") {
    const lab = document.createElement("label");
    lab.className = "radio-inline";
    const r = document.createElement("input");
    r.type = "radio";
    r.name = "correct_single";
    r.value = String(index);
    lab.appendChild(r);
    lab.appendChild(document.createTextNode(" верный"));
    div.appendChild(lab);
  } else if (qtype === "multiple_choice") {
    const lab = document.createElement("label");
    lab.className = "radio-inline";
    const c = document.createElement("input");
    c.type = "checkbox";
    c.name = "correct_multi";
    c.value = String(index);
    lab.appendChild(c);
    lab.appendChild(document.createTextNode(" верный"));
    div.appendChild(lab);
  }
  return div;
}

function initAddQuestionOptionRows() {
  const form = document.getElementById("addQuestionForm");
  const host = document.getElementById("addOptRows");
  const btn = document.getElementById("addOptRowBtn");
  if (!form || !host || !btn) return;
  const singleInput = document.getElementById("addCorrectSingleInput");
  const multiHost = document.getElementById("addMultiChecks");
  const typeSel = document.getElementById("addQType");

  function getCount() {
    return host.querySelectorAll('input[name="option_texts"]').length;
  }

  function syncAddControls() {
    const n = getCount();
    if (singleInput) {
      singleInput.max = String(Math.max(1, n));
      let v = parseInt(singleInput.value, 10);
      if (Number.isNaN(v) || v < 1) v = 1;
      if (v > n) v = n;
      singleInput.value = String(v);
    }
    if (multiHost) {
      multiHost.innerHTML = "";
      for (let i = 1; i <= n; i++) {
        const lab = document.createElement("label");
        const c = document.createElement("input");
        c.type = "checkbox";
        c.name = "correct_multi";
        c.value = String(i);
        lab.appendChild(c);
        lab.appendChild(document.createTextNode(` ${i}`));
        multiHost.appendChild(lab);
      }
    }
    btn.disabled = n >= MAX_OPTS;
    const hint = document.getElementById("addOptRowHint");
    if (hint) {
      hint.textContent =
        n >= MAX_OPTS ? "Максимум 6 вариантов" : `Минимум 2 варианта, максимум 6 (сейчас: ${n})`;
    }
  }

  function addRow(placeholder, required) {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.name = "option_texts";
    inp.className = "input";
    inp.placeholder = placeholder;
    if (required) inp.required = true;
    host.appendChild(inp);
    syncAddControls();
  }

  host.innerHTML = "";
  addRow("Вариант 1", true);
  addRow("Вариант 2", true);

  btn.addEventListener("click", () => {
    if (getCount() >= MAX_OPTS) return;
    addRow(`Вариант ${getCount() + 1}`, false);
  });

  if (typeSel) {
    typeSel.addEventListener("change", () => {
      if (typeSel.value === "multiple_choice") syncAddControls();
    });
  }
}

function syncEditAddButton(choiceBlock) {
  const rows = choiceBlock.querySelector(".js-opt-edit-rows");
  const btn = choiceBlock.querySelector(".js-edit-add-opt");
  const hint = choiceBlock.querySelector(".js-opt-limit-hint");
  if (!rows || !btn) return;
  const n = rows.querySelectorAll(".opt-editor").length;
  btn.disabled = n >= MAX_OPTS;
  if (hint) hint.classList.toggle("hidden", n < MAX_OPTS);
}

function initEditOptionButtons(list) {
  list.querySelectorAll(".js-edit-add-opt").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      const choiceBlock = btn.closest(".js-opts-choice");
      const form = btn.closest("form");
      const rows = choiceBlock && choiceBlock.querySelector(".js-opt-edit-rows");
      const qtypeSel = form && form.querySelector(".js-qtype");
      if (!choiceBlock || !rows || !qtypeSel) return;
      const qtype = qtypeSel.value;
      if (qtype === "text_input") return;
      const n = rows.querySelectorAll(".opt-editor").length + 1;
      if (n > MAX_OPTS) return;
      rows.appendChild(createEditOptRow(n, qtype));
      syncEditAddButton(choiceBlock);
    });
  });
  list.querySelectorAll(".js-opts-choice").forEach((block) => syncEditAddButton(block));
}

function getPreferredTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
  return "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

function initTheme() {
  applyTheme(getPreferredTheme());
  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });
}

function initConfirmForms() {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      const msg = form.getAttribute("data-confirm") || "Подтвердите действие";
      if (!window.confirm(msg)) e.preventDefault();
    });
  });
}

function initCopyLinks() {
  document.querySelectorAll(".copy-link").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const link = btn.getAttribute("data-link");
      if (!link) return;
      try {
        await navigator.clipboard.writeText(link);
        const prev = btn.textContent;
        btn.textContent = "Скопировано";
        setTimeout(() => {
          btn.textContent = prev;
        }, 2000);
      } catch {
        window.prompt("Скопируйте ссылку", link);
      }
    });
  });
}

function initFormValidation() {
  document.querySelectorAll("form[data-validate]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      const kind = form.getAttribute("data-validate");
      if (kind === "register") {
        const p = form.querySelector('input[name="password"]');
        const p2 = form.querySelector('input[name="password2"]');
        if (p && p2 && p.value !== p2.value) {
          e.preventDefault();
          alert("Пароли должны совпадать");
          return;
        }
        if (p && p.value.length < 8) {
          e.preventDefault();
          alert("Пароль не короче 8 символов");
        }
      }
      if (kind === "play-answer" || kind === "live-answer") {
        const radios = form.querySelectorAll('input[name="option_id"]');
        const checks = form.querySelectorAll('input[type="checkbox"].sr-only');
        const text = form.querySelector('input[name="text_answer"]');
        if (radios.length) {
          const ok = Array.from(radios).some((r) => r.checked);
          if (!ok) {
            e.preventDefault();
            alert("Выберите вариант ответа");
          }
        } else if (checks.length) {
          const ok = Array.from(checks).some((c) => c.checked);
          if (!ok) {
            e.preventDefault();
            alert("Отметьте хотя бы один вариант");
          }
        } else if (text && text.hasAttribute("required") && !text.value.trim()) {
          e.preventDefault();
          alert("Введите ответ");
        }
      }
      if (kind === "question-add") {
        const typeSel = form.querySelector("#addQType");
        const t = typeSel ? typeSel.value : "";
        if (t === "text_input") {
          const ct = form.querySelector('input[name="correct_text"]');
          if (ct && !ct.value.trim()) {
            e.preventDefault();
            alert("Укажите правильный ответ для текстового вопроса");
          }
        } else {
          const opts = Array.from(form.querySelectorAll('input[name="option_texts"]'))
            .map((i) => i.value.trim())
            .filter(Boolean);
          if (opts.length < 2) {
            e.preventDefault();
            alert("Нужно минимум два непустых варианта ответа");
          }
        }
      }
    });
  });
}

function initEditorPage() {
  initAddQuestionOptionRows();
  const list = document.getElementById("questionList");
  if (list) initEditOptionButtons(list);
  if (!list) return;
  const quizId = list.getAttribute("data-quiz-id");
  list.querySelectorAll(".js-toggle-edit").forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = btn.closest(".question-item");
      const panel = item && item.querySelector(".question-edit-panel");
      if (!panel) return;
      panel.classList.toggle("hidden");
    });
  });
  list.querySelectorAll(".js-qtype").forEach((sel) => {
    sel.addEventListener("change", () => {
      const form = sel.closest("form");
      if (!form) return;
      const v = sel.value;
      const choice = form.querySelector(".js-opts-choice");
      const text = form.querySelector(".js-opts-text");
      if (choice && text) {
        choice.style.display = v === "text_input" ? "none" : "block";
        text.style.display = v === "text_input" ? "block" : "none";
      }
      if (v !== "text_input" && choice) {
        const rows = choice.querySelector(".js-opt-edit-rows");
        if (rows) {
          const pack = [];
          rows.querySelectorAll(".opt-editor").forEach((row) => {
            const inp = row.querySelector('input[name="option_texts"]');
            const mark = row.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked');
            pack.push({ text: inp ? inp.value : "", correct: !!mark });
          });
          let singleIdx = pack.findIndex((p) => p.correct);
          if (singleIdx < 0) singleIdx = 0;
          rows.innerHTML = "";
          pack.forEach((d, i) => {
            const row = createEditOptRow(i + 1, v);
            const ti = row.querySelector('input[name="option_texts"]');
            if (ti) ti.value = d.text;
            const m = row.querySelector('input[type="radio"], input[type="checkbox"]');
            if (m) {
              if (v === "single_choice") m.checked = i === singleIdx;
              else m.checked = d.correct;
            }
            rows.appendChild(row);
          });
          syncEditAddButton(choice);
        }
      }
    });
  });
  const addType = document.getElementById("addQType");
  const addSingle = document.querySelector(".js-add-single");
  const addMulti = document.querySelector(".js-add-multi");
  const addChoice = document.querySelector(".js-add-choice");
  const addText = document.querySelector(".js-add-text");
  if (addType && addSingle && addMulti && addChoice && addText) {
    addType.addEventListener("change", () => {
      const v = addType.value;
      addChoice.classList.toggle("hidden", v === "text_input");
      addText.classList.toggle("hidden", v !== "text_input");
      addSingle.classList.toggle("hidden", v !== "single_choice");
      addMulti.classList.toggle("hidden", v !== "multiple_choice");
      if (v === "multiple_choice") {
        const multiHost = document.getElementById("addMultiChecks");
        const host = document.getElementById("addOptRows");
        if (multiHost && host) {
          const n = host.querySelectorAll('input[name="option_texts"]').length;
          multiHost.innerHTML = "";
          for (let i = 1; i <= n; i++) {
            const lab = document.createElement("label");
            const c = document.createElement("input");
            c.type = "checkbox";
            c.name = "correct_multi";
            c.value = String(i);
            lab.appendChild(c);
            lab.appendChild(document.createTextNode(` ${i}`));
            multiHost.appendChild(lab);
          }
        }
      }
    });
  }
  async function postReorder() {
    const ids = Array.from(list.querySelectorAll(".question-item")).map((li) => li.getAttribute("data-qid"));
    await fetch(`/quiz/${quizId}/questions/reorder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order: ids }),
    });
    window.location.reload();
  }
  list.querySelectorAll(".js-move-up").forEach((btn) => {
    btn.addEventListener("click", () => {
      const li = btn.closest(".question-item");
      const prev = li && li.previousElementSibling;
      if (li && prev) {
        list.insertBefore(li, prev);
        postReorder();
      }
    });
  });
  list.querySelectorAll(".js-move-down").forEach((btn) => {
    btn.addEventListener("click", () => {
      const li = btn.closest(".question-item");
      const next = li && li.nextElementSibling;
      if (li && next) {
        list.insertBefore(next, li);
        postReorder();
      }
    });
  });
}

function initStatsTable() {
  const table = document.getElementById("attemptsTable");
  if (!table) return;
  const tbody = table.querySelector("tbody");
  const ths = table.querySelectorAll("thead th");
  ths.forEach((th, idx) => {
    th.addEventListener("click", () => {
      const type = th.getAttribute("data-sort");
      const rows = Array.from(tbody.querySelectorAll("tr")).filter((r) => r.querySelectorAll("td").length > 1);
      rows.sort((a, b) => {
        const ac = a.children[idx].textContent.trim();
        const bc = b.children[idx].textContent.trim();
        if (type === "num") return parseFloat(bc) - parseFloat(ac);
        if (type === "date") {
          const ad = a.getAttribute("data-date") || "";
          const bd = b.getAttribute("data-date") || "";
          return bd.localeCompare(ad);
        }
        return ac.localeCompare(bc, "ru");
      });
      rows.forEach((r) => tbody.appendChild(r));
    });
  });
}

function initQuestionTimer() {
  const root = document.querySelector(".question-layout[data-play-code]");
  if (!root) return;
  const code = root.getAttribute("data-play-code");
  const index = parseInt(root.getAttribute("data-q-index") || "1", 10);
  let remaining = parseFloat(root.getAttribute("data-initial-remaining") || "0");
  let total = parseFloat(root.getAttribute("data-timer-total") || "30");
  total = Math.max(total, remaining, 1);
  const arc = document.getElementById("timerArc");
  const digits = document.getElementById("timerDigits");
  const timerRoot = document.getElementById("timerRoot");
  const form = document.getElementById("playForm");
  const circumference = 2 * Math.PI * 52;
  if (arc) {
    arc.style.strokeDasharray = String(circumference);
  }
  let expiredSent = false;

  function paint() {
    const sec = Math.max(0, Math.ceil(remaining - 1e-6));
    if (digits) digits.textContent = String(sec);
    const frac = Math.min(1, Math.max(0, remaining / total));
    if (arc) {
      arc.style.strokeDashoffset = String(circumference * (1 - frac));
    }
    if (timerRoot) {
      if (remaining <= 5 && remaining > 0) timerRoot.classList.add("timer-warn");
      else timerRoot.classList.remove("timer-warn");
    }
  }

  async function sync() {
    try {
      const res = await fetch(`/play/${code}/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ n: index }),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (typeof data.remaining === "number") {
        remaining = data.remaining;
        total = Math.max(total, remaining);
      }
      if (data.warn && timerRoot) timerRoot.classList.add("timer-warn");
      if (data.expired && !expiredSent) submitEmpty();
    } catch {}
  }

  function submitEmpty() {
    if (expiredSent || !form) return;
    expiredSent = true;
    if (form.querySelectorAll('input[name="option_id"]').length) {
      form.querySelectorAll('input[name="option_id"]').forEach((r) => {
        r.checked = false;
      });
    }
    if (form.querySelectorAll("input[type=checkbox].sr-only").length) {
      form.querySelectorAll("input[type=checkbox].sr-only").forEach((c) => {
        c.checked = false;
      });
    }
    const ta = form.querySelector('input[name="text_answer"]');
    if (ta) ta.value = "";
    form.submit();
  }

  const tickMs = 250;
  let acc = 0;
  const iv = setInterval(() => {
    acc += tickMs;
    remaining -= tickMs / 1000;
    if (remaining <= 0) {
      remaining = 0;
      paint();
      clearInterval(iv);
      submitEmpty();
      return;
    }
    paint();
    if (acc >= 3000) {
      acc = 0;
      sync();
    }
  }, tickMs);

  paint();
  sync();

  form &&
    form.addEventListener("submit", () => {
      clearInterval(iv);
    });
}

function initLiveHostPage() {
  const root = document.querySelector(".live-page[data-quiz-id]");
  if (!root) return;
  const pin = root.getAttribute("data-live-pin") || "";
  if (!pin) return;
  const quizId = root.getAttribute("data-quiz-id");
  const total = parseInt(root.getAttribute("data-total") || "0", 10);
  const lobby = document.getElementById("hostLobby");
  const stage = document.getElementById("hostStage");
  const connectedEl = document.getElementById("liveConnected");
  const connectedStage = document.getElementById("liveConnectedStage");
  const answeredEl = document.getElementById("liveAnswered");
  const indexEl = document.getElementById("liveIndex");
  const barsEl = document.getElementById("liveBars");
  const timerLine = document.getElementById("liveTimerLine");
  const timerSec = document.getElementById("liveTimerSec");
  const startBtn = document.getElementById("liveStartBtn");
  const nextBtn = document.getElementById("liveNextBtn");
  const finishLastBtn = document.getElementById("liveFinishLastBtn");
  const finishEarlyBtn = document.getElementById("liveFinishEarlyBtn");
  const finishedMsg = document.getElementById("hostFinishedMsg");
  const wsProto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${wsProto}://${window.location.host}/ws/live/host/${pin}`);
  ws.onmessage = () => refresh();
  let timerIv = null;
  function stopTimer() {
    if (timerIv) {
      clearInterval(timerIv);
      timerIv = null;
    }
  }
  async function doFinish() {
    await fetch(`/quiz/${quizId}/live/finish`, { method: "POST" });
    await refresh();
  }
  startBtn &&
    startBtn.addEventListener("click", async () => {
      await fetch(`/quiz/${quizId}/live/start`, { method: "POST" });
      await refresh();
    });
  nextBtn &&
    nextBtn.addEventListener("click", async () => {
      await fetch(`/quiz/${quizId}/live/next`, { method: "POST" });
      await refresh();
    });
  finishLastBtn && finishLastBtn.addEventListener("click", doFinish);
  finishEarlyBtn && finishEarlyBtn.addEventListener("click", doFinish);
  async function refresh() {
    const res = await fetch(`/quiz/${quizId}/live/state`);
    if (!res.ok) return;
    const data = await res.json();
    const conn = String(data.connected || 0);
    if (connectedEl) connectedEl.textContent = conn;
    if (connectedStage) connectedStage.textContent = conn;
    if (answeredEl) answeredEl.textContent = String(data.answered || 0);
    if (indexEl) indexEl.textContent = String(data.index || 0);
    if (data.state === "waiting") {
      lobby && lobby.classList.remove("hidden");
      stage && stage.classList.add("hidden");
      finishedMsg && finishedMsg.classList.add("hidden");
      stopTimer();
      if (timerLine) timerLine.classList.add("hidden");
    } else if (data.state === "running") {
      lobby && lobby.classList.add("hidden");
      stage && stage.classList.remove("hidden");
      finishedMsg && finishedMsg.classList.add("hidden");
      const qt = document.getElementById("liveQuestionText");
      if (qt && data.question_text) qt.textContent = data.question_text;
      if (barsEl && Array.isArray(data.options)) {
        barsEl.innerHTML = "";
        data.options.forEach((o) => {
          const row = document.createElement("div");
          row.className = "live-bar-row" + (o.is_correct ? " live-bar-correct" : "");
          const label = document.createElement("div");
          label.className = "live-bar-label";
          label.textContent = `${o.text} — ${o.count}`;
          const track = document.createElement("div");
          track.className = "live-bar-track";
          const fill = document.createElement("div");
          fill.className = "live-bar-fill";
          fill.style.width = `${Math.round(100 * (o.ratio || 0))}%`;
          track.appendChild(fill);
          row.appendChild(label);
          row.appendChild(track);
          barsEl.appendChild(row);
        });
      }
      const idx = data.index || 0;
      const tot = data.total || total;
      if (nextBtn && finishLastBtn) {
        if (idx >= tot) {
          nextBtn.classList.add("hidden");
          finishLastBtn.classList.remove("hidden");
        } else {
          nextBtn.classList.remove("hidden");
          finishLastBtn.classList.add("hidden");
        }
      }
      stopTimer();
      if (data.deadline_at && timerLine && timerSec) {
        timerLine.classList.remove("hidden");
        const end = Date.parse(data.deadline_at);
        const tick = () => {
          const left = Math.max(0, Math.ceil((end - Date.now()) / 1000));
          timerSec.textContent = String(left);
          if (left <= 0) stopTimer();
        };
        tick();
        timerIv = setInterval(tick, 500);
      } else if (timerLine) {
        timerLine.classList.add("hidden");
      }
    } else if (data.state === "finished") {
      lobby && lobby.classList.add("hidden");
      stage && stage.classList.remove("hidden");
      stopTimer();
      if (timerLine) timerLine.classList.add("hidden");
      if (barsEl) barsEl.innerHTML = "";
      if (finishedMsg) finishedMsg.classList.remove("hidden");
      if (nextBtn) nextBtn.classList.add("hidden");
      if (finishLastBtn) finishLastBtn.classList.add("hidden");
    }
  }
  refresh();
}

function initLiveJoinPage() {
  const root = document.querySelector(".live-join-page[data-pin]");
  if (!root) return;
  const pin = root.getAttribute("data-pin");
  const hasAttempt = root.getAttribute("data-has-attempt") === "1";
  if (!hasAttempt) return;
  const statusEl = document.getElementById("joinStatus");
  async function refresh() {
    const res = await fetch(`/join/${pin}/state`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.state === "running") {
      window.location.href = `/join/${pin}/question`;
      return;
    }
    if (data.state === "finished") {
      window.location.href = `/join/${pin}/question`;
      return;
    }
    if (statusEl) statusEl.textContent = "Ожидайте начала викторины. Ведущий скоро начнёт.";
  }
  refresh();
  setInterval(refresh, 2000);
}

function initLiveQuestionPage() {
  const root = document.querySelector(".live-question-page[data-pin]");
  if (!root) return;
  const pin = root.getAttribute("data-pin");
  const attemptId = root.getAttribute("data-attempt-id") || "0";
  const deadlineMs = parseInt(root.getAttribute("data-deadline-ms") || "0", 10);
  const meta = document.querySelector('meta[name="live-q-index"]');
  const myIndex = parseInt(meta ? meta.getAttribute("content") || "0" : "0", 10);
  const form = document.getElementById("liveAnswerForm");
  const submitBtn = document.getElementById("liveSubmitBtn");
  const timerSec = document.getElementById("liveQTimerSec");
  const wsProto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${wsProto}://${window.location.host}/ws/live/player/${pin}/${attemptId}`);
  ws.onmessage = async () => {
    const res = await fetch(`/join/${pin}/state`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.state === "finished") {
      window.location.href = `/join/${pin}/question`;
      return;
    }
    if (data.state === "running" && data.current_index && data.current_index !== myIndex) {
      window.location.reload();
    }
  };
  if (deadlineMs > 0 && timerSec && form) {
    const tick = () => {
      const left = Math.max(0, Math.ceil((deadlineMs - Date.now()) / 1000));
      timerSec.textContent = String(left);
      if (left <= 0) {
        if (submitBtn) submitBtn.disabled = true;
        form.querySelectorAll("input, button").forEach((el) => {
          el.disabled = true;
        });
      }
    };
    tick();
    setInterval(tick, 500);
  }
}

initTheme();
initConfirmForms();
initCopyLinks();
initFormValidation();
initEditorPage();
initStatsTable();
initQuestionTimer();
initLiveHostPage();
initLiveJoinPage();
initLiveQuestionPage();
