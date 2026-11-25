/*
 * TradeCognix front end: site behaviour, app shell, and the in-browser demo.
 *
 * Exposes window.TCUI, the shared rendering layer. js/api.js (loaded after this file) uses it
 * when TC_API_BASE is set, so demo and API mode draw exactly the same UI.
 * No build step, no dependencies beyond chart.js.
 */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  var $ = function (s, c) { return (c || doc).querySelector(s); };
  var $$ = function (s, c) { return [].slice.call((c || doc).querySelectorAll(s)); };
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var API_ON = !!(window.TC_API_BASE || "");
  var IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

  /* ================= formatting (Indian) ================= */
  var esc = function (v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var inr = function (v) { return Math.round(v).toLocaleString("en-IN"); };
  var rupee = function (v) { v = v || 0; return (v < 0 ? "−₹" : "₹") + inr(Math.abs(v)); };
  var signed = function (v) { return (v > 0 ? "+" : "") + rupee(v); };
  function compact(v) {
    var a = Math.abs(v), s = v < 0 ? "−₹" : "₹";
    if (a >= 1e7) return s + (a / 1e7).toFixed(2).replace(/\.?0+$/, "") + " Cr";
    if (a >= 1e5) return s + (a / 1e5).toFixed(2).replace(/\.?0+$/, "") + " L";
    if (a >= 1e3) return s + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "k";
    return s + Math.round(a);
  }
  var IST = "Asia/Kolkata";
  function istParts(d) {
    var o = {};
    new Intl.DateTimeFormat("en-GB", { timeZone: IST, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(d || new Date()).forEach(function (p) { o[p.type] = p.value; });
    return { wd: o.weekday, h: +o.hour, m: +o.minute, hm: o.hour + ":" + o.minute };
  }
  function istTime(d) { return istParts(d).hm; }
  function istDate(d) { return new Intl.DateTimeFormat("en-IN", { timeZone: IST, day: "2-digit", month: "short", year: "numeric" }).format(d); }

  /* ================= theme ================= */
  function theme() { return root.getAttribute("data-theme") === "light" ? "light" : "dark"; }
  function setTheme(t) {
    root.setAttribute("data-theme", t);
    try { localStorage.setItem("tc-theme", t); } catch (e) { /* private mode */ }
    $$("[data-theme-set]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.themeSet === t)); });
    $$("[data-theme-toggle]").forEach(function (b) { b.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme"); });
    redrawCharts();
  }

  /* ================= toast & modals ================= */
  var tt;
  function toast(msg) {
    var t = $("#x-toast"); if (!t) return;
    t.textContent = msg; t.classList.add("is-on"); clearTimeout(tt);
    tt = setTimeout(function () { t.classList.remove("is-on"); }, 3600);
  }
  var lastFocus = null;
  function openModal(m) {
    if (!m) return;
    lastFocus = doc.activeElement; m.hidden = false;
    var f = $("input:not([type=hidden])", m) || $("button:not([data-close])", m) || $("button", m);
    if (f) setTimeout(function () { f.focus(); }, 20);
  }
  function closeModal(m) { if (!m || m.hidden) return; m.hidden = true; if (lastFocus && lastFocus.focus) lastFocus.focus(); }
  function openModalEl() { return $$(".modal").filter(function (m) { return !m.hidden; })[0]; }
  doc.addEventListener("keydown", function (e) {
    var m = openModalEl();
    if (!m) return;
    if (e.key === "Escape") { closeModal(m); return; }
    if (e.key === "Tab") { // focus trap
      var f = $$("button:not([disabled]), input, a[href], [tabindex]:not([tabindex='-1'])", m).filter(function (x) { return x.offsetParent !== null; });
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  $$(".modal").forEach(function (m) { m.addEventListener("mousedown", function (e) { if (e.target === m) closeModal(m); }); });

  /* ================= state ================= */
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } }
  };
  var S = {
    user: store.get("tc_demo_user", null),
    connected: store.get("tc_demo_brokers", []),
    sessions: [],
    backtests: [],
    parsed: false,
    rules: null,
    model: null,
    afterAuth: null,
    nextSid: 101
  };
  function signedIn() { return API_ON ? !!localStorage.getItem("tc_token") : !!S.user; }

  /* ================= site ================= */
  $$("[data-bill]").forEach(function (b) {
    b.addEventListener("click", function () {
      $$("[data-bill]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      var y = b.dataset.bill === "y";
      $("#x-pro-price").textContent = y ? "₹9,990" : "₹999";
      $("#x-pro-per").textContent = y ? "/year" : "/month";
      $("#x-pro-note").innerHTML = y ? "≈ ₹833/month · <b>2 months free</b>" : "or ₹9,990/year";
      $("#x-elite-note").textContent = y ? "Monthly billing only" : "Billed monthly";
    });
  });

  /* ================= app shell & routing ================= */
  var site = $("#x-site"), app = $("#x-app");
  var VIEWS = { dashboard: "Dashboard", studio: "Strategy Studio", backtests: "Backtests", paper: "Paper & live", journal: "Journal", brokers: "Brokers", settings: "Settings" };
  var REAL = { dashboard: 1, studio: 1, backtests: 1, paper: 1, journal: 1, brokers: 1, settings: 1 };
  var current = null;

  function show(v) {
    if (!VIEWS[v]) v = "dashboard";
    current = v;
    var real = REAL[v] ? v : "soon";
    $$(".view").forEach(function (s) { s.hidden = s.dataset.v !== real; });
    $$(".tab").forEach(function (t) {
      var on = t.dataset.view === v || (v === "settings" && t.classList.contains("tab--more"));
      if (on) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
    });
    $("#x-top-title").textContent = VIEWS[v];
    if (real === "soon") $("#x-soon-title").textContent = VIEWS[v];
    doc.title = VIEWS[v] + " · TradeCognix";
    if (v === "dashboard") { greet(); drawDash(); renderChecklist(); }
    if (v === "backtests") renderBacktests();
    if (v === "paper") renderSessions();
    if (v === "settings") renderSettings();
    if (v === "studio" && S.model && charts.bt == null) renderResults(S.model, true);
    if (v === "journal") renderJournalEmpty();
    window.scrollTo(0, 0);
    // js/api.js listens for this to load account data (saved runs, strategies, journal) from the server.
    doc.dispatchEvent(new CustomEvent("tc:view", { detail: v }));
  }
  function openApp(v) {
    site.hidden = true; app.hidden = false;
    show(v || "dashboard");
  }
  function closeApp() {
    app.hidden = true; site.hidden = false; doc.title = "TradeCognix · Plain-English algo trading for Indian traders";
  }
  function route() {
    var h = location.hash;
    if (h.indexOf("#/") === 0) openApp(h.slice(2));
    else if (!app.hidden) closeApp();
  }
  function nav(v) {
    if (location.hash === "#/" + v) show(v);
    else location.hash = "#/" + v;
  }
  function backToSite() {
    if (location.hash.indexOf("#/") === 0) history.pushState(null, "", location.pathname + location.search);
    closeApp(); window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", route);

  doc.addEventListener("click", function (e) {
    var mb = e.target.closest("[data-menu]");
    if (mb) { toggleMenu(); return; }
    if (e.target.closest("#nav-menu a, #nav-menu button")) toggleMenu(false);
    var t = e.target.closest("[data-open-app],[data-back],[data-view],[data-ex],[data-close],[data-theme-toggle],[data-theme-set],[data-auth-open],[data-kill-open],[data-open-bt]");
    if (!t) return;
    if (t.hasAttribute("data-open-app")) { e.preventDefault(); nav(t.dataset.openApp || "dashboard"); }
    else if (t.hasAttribute("data-back")) { e.preventDefault(); backToSite(); }
    else if (t.dataset.view) { closeModal(openModalEl()); nav(t.dataset.view); }
    else if (t.dataset.ex) {
      var i = $("#x-input"); i.value = t.dataset.ex; i.dispatchEvent(new Event("input"));
      $$(".tpl").forEach(function (b) { b.setAttribute("aria-pressed", String(b === t)); });
      // Only focus on devices with a fine pointer: on phones focusing pops the keyboard over the template.
      if (window.matchMedia && window.matchMedia("(pointer: fine)").matches) i.focus();
      else { var h = $("#x-input-hint"); if (h) h.textContent = "Template loaded. Edit it or tap Generate rules."; }
    }
    else if (t.hasAttribute("data-close")) closeModal(t.closest(".modal"));
    else if (t.hasAttribute("data-theme-toggle")) setTheme(theme() === "dark" ? "light" : "dark");
    else if (t.dataset.themeSet) setTheme(t.dataset.themeSet);
    else if (t.hasAttribute("data-auth-open")) { if (signedIn()) nav("settings"); else requireAuth(null); }
    else if (t.hasAttribute("data-kill-open")) openKill();
    else if (t.dataset.openBt) { var b = S.backtests[+t.dataset.openBt]; if (b) { S.model = b.model; nav("studio"); renderResults(b.model, true); } }
  });

  /* ================= market status (IST) ================= */
  var DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  function market() {
    var p = istParts(), mins = p.h * 60 + p.m, di = DAYS.indexOf(p.wd), weekday = di >= 0 && di < 5;
    var open = weekday && mins >= 555 && mins < 930;
    var text;
    if (open) text = "NSE open · closes 15:30";
    else if (weekday && mins < 555) text = "NSE closed · opens 09:15";
    else { var nd = di >= 4 ? "Mon" : DAYS[di + 1]; text = "NSE closed · opens " + nd + " 09:15"; }
    return { open: open, text: text, hm: p.hm, h: p.h };
  }
  function tickClock() {
    var m = market(), el = $("#x-mkt");
    if (!el) return;
    el.classList.toggle("is-open", m.open);
    $("#x-mkt-text").textContent = m.text + " ·";
    $("#x-clock").textContent = m.hm + " IST";
  }
  function greet() {
    var h = market().h, g = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    var name = S.user ? S.user.split("@")[0] : "";
    $("#x-greet").textContent = g + (name ? ", " + name : "");
  }

  /* ================= charts ================= */
  var charts = {};
  function cssVar(n) { return getComputedStyle(root).getPropertyValue(n).trim(); }
  function hexA(hex, a) {
    var m = hex.replace("#", ""); if (m.length === 3) m = m.split("").map(function (c) { return c + c; }).join("");
    var n = parseInt(m, 16); return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
  }
  function chartDefaults() {
    if (!window.Chart) return;
    var d = Chart.defaults;
    d.font.family = cssVar("--font-ui") || "system-ui"; d.font.size = 11.5;
    d.color = cssVar("--text-3"); d.borderColor = cssVar("--chart-grid");
    d.maintainAspectRatio = false; d.responsive = true;
    var tip = d.plugins.tooltip;
    tip.backgroundColor = cssVar("--surface-3"); tip.titleColor = cssVar("--text"); tip.bodyColor = cssVar("--text-2");
    tip.borderColor = cssVar("--border-strong"); tip.borderWidth = 1; tip.padding = 10; tip.cornerRadius = 8; tip.displayColors = false;
    tip.titleFont = { family: d.font.family, weight: "600", size: 12 }; tip.bodyFont = { family: cssVar("--font-mono"), size: 12 };
  }
  function equityChart(id, labels, values, fmtLabel, startAtZero) {
    if (!window.Chart) return null;
    chartDefaults();
    var el = doc.getElementById(id); if (!el) return null;
    var old = Chart.getChart(el); if (old) old.destroy();
    var last = values[values.length - 1] - (startAtZero ? 0 : values[0]);
    var col = cssVar(last >= 0 ? "--brand" : "--down");
    return new Chart(el, {
      type: "line",
      data: { labels: labels, datasets: [{ data: values, borderColor: col, borderWidth: 1.75, pointRadius: 0, pointHoverRadius: 3, pointHoverBackgroundColor: col, tension: 0, fill: startAtZero ? "origin" : "start",
        backgroundColor: function (c) {
          var a = c.chart.chartArea; if (!a) return hexA(col, 0.1);
          var g = c.chart.ctx.createLinearGradient(0, a.top, 0, a.bottom); g.addColorStop(0, hexA(col, 0.18)); g.addColorStop(1, hexA(col, 0)); return g;
        } }] },
      options: {
        animation: reduce ? false : { duration: 500 },
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 4, right: 4 } },
        scales: {
          x: { grid: { display: false }, border: { color: cssVar("--border") }, ticks: { maxTicksLimit: 6, maxRotation: 0, autoSkipPadding: 16, callback: function (v, i) { return fmtLabel(labels[i]); } } },
          y: { position: "right", border: { display: false }, grid: { color: cssVar("--chart-grid") }, ticks: { maxTicksLimit: 5, font: { family: cssVar("--font-mono"), size: 11 }, callback: function (v) { return compact(v); } } }
        },
        plugins: { legend: { display: false }, tooltip: { callbacks: { title: function (c) { return fmtLabel(c[0].label, true); }, label: function (c) { return rupee(c.raw); } } } }
      }
    });
  }
  function drawDash() {
    // js/api.js sets S.dash to the signed-in account's cumulative net P&L; otherwise draw the demo curve.
    if (S.dash) {
      if (!S.dash.values.length) { if (charts.dash) { charts.dash.destroy && charts.dash.destroy(); charts.dash = null; } return; }
      charts.dash = equityChart("x-dash-chart", S.dash.labels, S.dash.values, function (l) { return l; }, true);
      return;
    }
    var raw = JSON.parse($("#x-equity-data").textContent);
    var labels = raw.map(function (_, i) { return i + 1; });
    charts.dash = equityChart("x-dash-chart", labels, raw, function (l) { return "Day " + l; }, false);
  }
  function redrawCharts() {
    if (!app.hidden && current === "dashboard") drawDash();
    if (S.model && !$("#x-results").hidden) drawEquity(S.model);
  }

  /* ================= progress & logs ================= */
  function progress(btn, prog, log, lines) {
    var btns = $$("#x-gen, #x-run"); btns.forEach(function (b) { b.disabled = true; });
    if (btn) btn.classList.add("is-loading");
    log.classList.remove("is-error", "is-ok");
    prog.classList.remove("is-run"); void prog.offsetWidth; prog.classList.add("is-run");
    var i = 0; log.textContent = lines[0];
    var iv = setInterval(function () { i = Math.min(lines.length - 1, i + 1); log.textContent = lines[i]; }, reduce ? 80 : 400);
    return function done() { clearInterval(iv); btns.forEach(function (b) { b.disabled = false; }); if (btn) btn.classList.remove("is-loading"); prog.classList.remove("is-run"); };
  }
  function setLog(el, msg, kind) { el.textContent = msg; el.classList.toggle("is-error", kind === "error"); el.classList.toggle("is-ok", kind === "ok"); }

  /* ================= Studio: prompt validation ================= */
  var input = $("#x-input"), hint = $("#x-input-hint"), HINT0 = hint.textContent, triedOnce = false;
  function validatePrompt(strict) {
    var t = input.value.trim(), msg = HINT0, kind = "";
    if (!t) { msg = "Describe a strategy first, or pick an example below."; kind = "error"; }
    else if (t.length < 20) { msg = "Add a bit more detail: instrument, entry and exit."; kind = "error"; }
    else if (!/\b(buy|sell|short|long)\b/i.test(t)) { msg = "Tip: say whether to buy or sell, e.g. “Buy Nifty ATM call when…”"; kind = "warn"; }
    else if (!/(exit|stop\s*loss|\bsl\b|target|square|turns red|crosses above|close)/i.test(t)) { msg = "Tip: add an exit, e.g. a stop loss, target or exit time."; kind = "warn"; }
    var show = strict || triedOnce || kind !== "error";
    hint.textContent = show ? msg : HINT0;
    hint.classList.toggle("is-error", show && kind === "error");
    hint.classList.toggle("is-warn", show && kind === "warn");
    if (show && kind === "error") input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
    $("#x-count").textContent = input.value.length + "/600";
    return kind !== "error";
  }
  input.addEventListener("input", function () { validatePrompt(false); });
  $("#x-clear").addEventListener("click", function () { input.value = ""; triedOnce = false; validatePrompt(false); input.focus(); });
  $$(".kbd-mod").forEach(function (k) { k.textContent = IS_MAC ? "⌘↵" : "Ctrl ↵"; });
  validatePrompt(false);

  /* ================= Studio: flow steps ================= */
  function setStep() {
    var paper = S.sessions.some(function (s) { return s.mode === "paper"; }), live = S.sessions.some(function (s) { return s.mode === "live"; });
    var cur = !S.parsed ? 1 : !S.model ? 2 : !paper && !live ? 3 : !live ? 4 : 5;
    $$("#x-flow li").forEach(function (li) {
      var n = +li.dataset.step;
      li.classList.toggle("is-done", n < cur);
      li.classList.toggle("is-current", n === cur);
      if (n === cur) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    renderGuide(cur);
  }

  /* ================= Studio: first-run guide ================= */
  // A one-line coach above the Studio that follows the user's progress. Hidden for good once dismissed
  // or once they have started paper trading; "Show guide" brings it back.
  function renderGuide(cur) {
    var box = $("#x-guide"); if (!box) return;
    var off = localStorage.getItem("tc_guide_off") === "1";
    var finished = cur >= 4;
    box.hidden = off || finished;
    $("#x-guide-show").hidden = !off || finished;
    if (box.hidden) return;
    var act = $("#x-guide-act"), text, label = "", step = Math.min(cur, 3);
    if (cur === 1) { text = "New here? Tap a template, or describe your own idea in plain English, then press Generate rules."; label = "See templates"; }
    else if (cur === 2) text = "Check the rules. Click any value to change it, then press Run backtest. Brokerage, STT and slippage are included.";
    else if (signedIn()) text = "Happy with the result? Start paper trading. It runs on live prices and no money moves.";
    else { text = "Happy with the result? Create a free account to paper trade on live prices. No money moves."; label = "Create free account"; }
    $("#x-guide-step").textContent = step + "/3";
    $("#x-guide-text").textContent = text;
    act.hidden = !label; act.textContent = label;
    act.dataset.go = cur === 1 ? "tpl" : "auth";
  }
  $("#x-guide-act").addEventListener("click", function (e) {
    if (e.currentTarget.dataset.go === "tpl") {
      var g = $(".tpls"); g.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
      var first = $(".tpl"); if (first) first.focus({ preventScroll: true });
    } else requireAuth(null);
  });
  $("#x-guide-close").addEventListener("click", function () { localStorage.setItem("tc_guide_off", "1"); setStep(); });
  $("#x-guide-show").addEventListener("click", function () { localStorage.removeItem("tc_guide_off"); setStep(); });

  /* ================= Studio: rules ================= */
  function renderRules(rules, explanation, warnings) {
    S.rules = rules; S.parsed = true;
    $("#x-edit").innerHTML = Object.keys(rules).map(function (k) {
      return '<div class="rule x-rule" data-row="' + esc(k) + '"><b>' + esc(k) + '</b><span contenteditable="true" spellcheck="false" role="textbox" aria-label="' + esc(k) + '" data-k="' + esc(k) + '"></span><span class="rule__edit" aria-hidden="true"><svg><use href="#i-pencil" /></svg></span></div>';
    }).join("");
    $$("#x-edit [data-k]").forEach(function (s) { s.textContent = rules[s.dataset.k]; flagRule(s); });
    var ex = $("#x-explain");
    if (explanation) { ex.innerHTML = "<b>In plain words:</b> " + esc(explanation) + (warnings && warnings.length ? '<br><span class="hint is-warn">' + esc(warnings.join(" ")) + "</span>" : ""); ex.hidden = false; }
    else ex.hidden = true;
    $("#x-rules-panel").hidden = false;
    $("#x-run-log").textContent = "";
    setStep(); renderChecklist();
    if (window.matchMedia("(max-width: 1180px)").matches) $("#x-rules-panel").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }
  function flagRule(span) {
    var row = span.closest(".rule"), v = span.textContent;
    row.classList.toggle("is-warn", /\?|review|\(default\)/i.test(v) || !v.trim());
    row.title = row.classList.contains("is-warn") ? "Check this value: it was missing from your description or is a default." : "";
  }
  $("#x-edit").addEventListener("input", function (e) { if (e.target.dataset && e.target.dataset.k) flagRule(e.target); });
  $("#x-edit").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !(e.metaKey || e.ctrlKey)) { e.preventDefault(); e.target.blur(); }
  });
  function readRules() { var r = {}; $$("#x-edit [data-k]").forEach(function (s) { r[s.dataset.k] = s.textContent.trim(); }); return r; }

  /* ================= Landing: mobile menu ================= */
  function toggleMenu(force) {
    var menu = document.getElementById("nav-menu"), btn = document.querySelector("[data-menu]");
    if (!menu || !btn) return;
    var open = typeof force === "boolean" ? force : menu.hidden;
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    btn.closest(".nav").classList.toggle("is-open", open);
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") toggleMenu(false); });
  window.addEventListener("resize", function () { if (window.innerWidth > 980) toggleMenu(false); });

  /* ================= Studio: results (shared with api.js) ================= */
  /* model = { scope, illustrative, badge, metrics:{trades,winRate,gross,net,costs,maxDD,pf,expectancy},
               equity:{labels,values,fmt}, costs:{name:amount}, trades:[{when,side,instrument,entry,exit,net}], totalTrades } */
  function metricCell(label, value, cls, sub, hero) {
    return '<div class="metric x-metric' + (hero ? " metric--hero" : "") + '"><span>' + label + '</span><strong class="' + (cls || "") + '">' + esc(value) + "</strong>" + (sub ? "<small>" + esc(sub) + "</small>" : "") + "</div>";
  }
  function drawEquity(model) {
    charts.bt = equityChart("x-bt-chart", model.equity.labels, model.equity.values, model.equity.fmt, true);
  }
  function renderResults(model, quiet) {
    S.model = model;
    var m = model.metrics, upDown = function (v) { return v >= 0 ? "up" : "down"; };
    var costPct = m.gross > 0 ? Math.round(m.costs / m.gross * 100) + "% of gross" : "";
    $("#x-mgrid").innerHTML = [
      metricCell("Net P&amp;L", signed(m.net), upDown(m.net), "after all costs", true),
      metricCell("Gross P&amp;L", signed(m.gross), upDown(m.gross), "before costs"),
      metricCell("Costs", rupee(m.costs), "down", costPct),
      metricCell("Max drawdown", rupee(m.maxDD), "down", "peak to trough"),
      metricCell("Trades", String(m.trades), "", model.days ? model.days : ""),
      metricCell("Win rate", m.winRate.toFixed(2) + "%", ""),
      metricCell("Profit factor", m.pf == null ? "—" : m.pf.toFixed(2), m.pf == null ? "" : m.pf >= 1 ? "up" : "down"),
      metricCell("Expectancy", signed(m.expectancy), upDown(m.expectancy), "per trade")
    ].join("");
    $("#x-res-scope").textContent = model.scope;
    var badge = $("#x-res-badge");
    badge.textContent = model.badge || (model.illustrative ? "Illustrative" : "Broker data");
    badge.className = "badge " + (model.illustrative ? "badge--warn" : "badge--live");
    var note = $("#x-res-note");
    if (note) { note.hidden = !model.note; note.querySelector("p").textContent = model.note || ""; }
    $("#x-eq-cap").innerHTML = "Net P&amp;L " + (m.net >= 0 ? "ended at " : "fell to ") + esc(signed(m.net)) + " over " + m.trades + " trades<small>Cumulative net P&amp;L, ₹ · after costs and slippage</small>";
    $("#x-cost-cap").innerHTML = "Costs took " + esc(rupee(m.costs)) + (m.gross > 0 ? " of " + esc(rupee(m.gross)) + " gross" : "") + "<small>Charges by type, ₹</small>";
    var keys = Object.keys(model.costs), max = Math.max.apply(null, keys.map(function (k) { return model.costs[k] || 0; }).concat([1]));
    $("#x-costbars").innerHTML = keys.map(function (k) {
      var v = model.costs[k] || 0;
      return '<div class="costbar"><span>' + esc(k) + '</span><span class="track" aria-hidden="true"><i style="width:' + (v / max * 100).toFixed(1) + '%"></i></span><span class="num">' + rupee(v) + "<small>" + (m.costs ? Math.round(v / m.costs * 100) : 0) + "%</small></span></div>";
    }).join("");
    $("#x-costsum").innerHTML = '<div><span>Gross P&amp;L</span><span class="num">' + signed(m.gross) + '</span></div><div><span>Total costs</span><span class="num down">−' + rupee(m.costs) + '</span></div><div class="total"><span>Net P&amp;L</span><span class="num ' + upDown(m.net) + '">' + signed(m.net) + "</span></div>";
    $("#x-trades").innerHTML = model.trades.length ? model.trades.map(function (t) {
      return "<tr><td class=\"num\">" + esc(t.when) + "</td><td>" + (t.side === "Sell" ? '<span class="down">Sell</span>' : '<span class="up">Buy</span>') + "</td><td>" + esc(t.instrument) + '</td><td class="r">' + t.entry.toFixed(2) + '</td><td class="r">' + t.exit.toFixed(2) + '</td><td class="r ' + upDown(t.net) + '">' + signed(t.net) + "</td></tr>";
    }).join("") : '<tr><td colspan="6" class="faint" style="text-align:center;padding:28px">No trades in this period. Try loosening the entry condition or a longer window.</td></tr>';
    $("#x-trades-note").textContent = model.trades.length ? "Latest " + model.trades.length + " of " + model.totalTrades : "";
    $("#x-results").hidden = false;
    $("#x-res-banner").hidden = true;
    drawEquity(model);
    setStep(); renderChecklist();
    if (!quiet) {
      var r = $("#x-results");
      if (window.matchMedia("(max-width: 1180px)").matches) r.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
      r.focus({ preventScroll: true });
    }
  }
  function recordBacktest(model, name) {
    S.backtests.unshift({ name: name, model: model, at: istTime() });
    $("#x-bt-count").textContent = S.backtests.length || "";
  }
  function renderBacktests() {
    var has = S.backtests.length > 0;
    $("#x-bt-panel").hidden = !has; $("#x-bt-empty").hidden = has;
    $("#x-bt-body").innerHTML = S.backtests.map(function (b, i) {
      var m = b.model.metrics;
      return "<tr><td>" + esc(b.name) + "</td><td>" + esc(b.model.instrument || "") + '</td><td class="r">' + m.trades + '</td><td class="r">' + m.winRate.toFixed(1) + '%</td><td class="r ' + (m.net >= 0 ? "up" : "down") + '">' + signed(m.net) + '</td><td class="r">' + b.at + '</td><td><button class="btn btn--ghost btn--sm" type="button" data-open-bt="' + i + '">Open</button></td></tr>';
    }).join("");
  }

  /* ================= journal (demo / signed-out state) ================= */
  function renderJournalEmpty() {
    ["#x-j-panel", "#x-j-sum-panel", "#x-j-bar", "#x-j-export"].forEach(function (s) { $(s).hidden = true; });
    $("#x-j-empty").hidden = false;
    var api = !!window.TC_API_BASE, b = $("#x-j-empty-btn");
    if (api && !signedIn()) {
      $("#x-j-empty-h").textContent = "Sign in to see your journal";
      $("#x-j-empty-p").textContent = "The journal records every trade your paper and live sessions close, so it needs a free account.";
      b.textContent = "Create free account"; b.removeAttribute("data-view"); b.setAttribute("data-auth-open", "");
    } else {
      $("#x-j-empty-h").textContent = "No trades yet";
      $("#x-j-empty-p").textContent = api ? "Closed trades from your paper sessions land here, with charges and the reason each one exited."
        : "This is the demo, so no trades are recorded. With an account, every trade your paper sessions close lands here.";
      b.textContent = "Open Strategy Studio"; b.removeAttribute("data-auth-open"); b.setAttribute("data-view", "studio");
    }
  }

  /* ================= sessions & kill switch (shared) ================= */
  function addSession(s) {
    s.status = s.status || "running"; s.started = s.started || istTime();
    S.sessions.unshift(s); syncKill(); renderSessions(); setStep(); renderChecklist();
    var b = $("#x-res-banner");
    b.innerHTML = '<div class="callout callout--ok"><svg aria-hidden="true"><use href="#i-check" /></svg><span style="flex:1">' + (s.mode === "live" ? "<strong>Live session #" + esc(s.id) + "</strong> started on " + esc(s.broker) + "." : "<strong>Paper session #" + esc(s.id) + "</strong> is running.") + " Runs on candle close, 09:15–15:30 IST.</span><button class=\"btn btn--ghost btn--sm\" type=\"button\" data-view=\"paper\">View</button></div>";
    b.hidden = false;
  }
  // js/api.js replaces the list with the account's sessions from the server (survives reloads and restarts).
  function setSessions(list) { S.sessions = list; syncKill(); renderSessions(); setStep(); renderChecklist(); }
  function running() { return S.sessions.filter(function (s) { return s.status === "running"; }); }
  function syncKill() {
    var n = running().length, k = $("#x-kill");
    k.classList.toggle("is-armed", n > 0);
    k.setAttribute("aria-label", n ? "Kill switch: stop " + n + " running session" + (n > 1 ? "s" : "") : "Kill switch: nothing running");
    $("#x-sess-count").textContent = n || "";
  }
  function stopSession(id, reason) {
    S.sessions.forEach(function (s) { if (String(s.id) === String(id) && s.status === "running") { s.status = reason || "stopped"; } });
    syncKill(); renderSessions(); setStep();
  }
  function killAll() {
    var n = running().length;
    S.sessions.forEach(function (s) { if (s.status === "running") s.status = "killed"; });
    syncKill(); renderSessions(); setStep();
    return n;
  }
  function renderSessions() {
    var has = S.sessions.length > 0;
    $("#x-sess-panel").hidden = !has; $("#x-sess-empty").hidden = has;
    $("#x-sess-note").textContent = running().length + " running";
    $("#x-sess-body").innerHTML = S.sessions.map(function (s) {
      var state = s.status === "running" ? '<span class="badge badge--live">Running</span>' : s.status === "killed" ? '<span class="badge badge--down">Killed</span>' : s.status === "halted" ? '<span class="badge badge--warn" title="' + esc(s.reason || "") + '">Halted</span>' + (s.reason ? '<span class="sub">' + esc(s.reason) + "</span>" : "") : '<span class="badge">Stopped</span>';
      return '<tr><td class="num">#' + esc(s.id) + "</td><td>" + esc(s.name) + "</td><td>" + (s.mode === "live" ? '<span class="badge badge--down">Live</span>' : '<span class="badge badge--paper">Paper</span>') + "</td><td>" + esc(s.broker) + '</td><td class="num">' + esc(s.started) + "</td><td>" + state + "</td><td>" +
        (s.status === "running" ? '<button class="btn btn--ghost btn--sm" type="button" data-stop="' + esc(s.id) + '">Stop</button>' : "") + "</td></tr>";
    }).join("");
  }
  $("#x-sess-body").addEventListener("click", function (e) {
    var b = e.target.closest("[data-stop]"); if (!b) return;
    stopSession(b.dataset.stop); toast("Session #" + b.dataset.stop + " stopped");
  });
  function openKill() {
    var n = running().length;
    if (!n) { toast("Nothing is running. The kill switch arms when a paper or live session starts."); return; }
    $("#x-kill-text").textContent = "This halts " + n + " running session" + (n > 1 ? "s" : "") + " and squares off open positions immediately. You can start them again later.";
    openModal($("#x-kill-modal"));
  }
  $("#x-kill").addEventListener("click", openKill);
  $("#x-kill-confirm").addEventListener("click", function () {
    closeModal($("#x-kill-modal"));
    var n = killAll();
    toast("Kill switch: " + n + " session" + (n === 1 ? "" : "s") + " halted, positions squared off" + (API_ON ? "" : " (demo)"));
  });

  /* ================= auth (demo; api.js takes over submit in API mode) ================= */
  function requireAuth(next) {
    if (signedIn()) { if (next) next(); return; }
    S.afterAuth = next;
    var f = $("#tc-auth-form"); f.reset(); $("#tc-auth-err").textContent = "";
    $$(".field", f).forEach(function (x) { x.classList.remove("is-error"); $(".field__msg", x).textContent = ""; });
    $("#tc-auth-sub").textContent = next ? "Backtests are free without an account. Paper trading needs one, so your sessions and journal are saved." : "Backtests stay free without one. An account saves your strategies, paper sessions and journal.";
    openModal($("#tc-auth-modal"));
  }
  function validateAuth(f) {
    var ok = true;
    [["email", function (v) { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) ? "" : "Enter a valid email address."; }],
     ["password", function (v) { return v.length >= 8 ? "" : "Use at least 8 characters."; }]].forEach(function (p) {
      var inp = f[p[0]], msg = p[1](inp.value.trim()), wrap = inp.closest(".field");
      wrap.classList.toggle("is-error", !!msg); $(".field__msg", wrap).textContent = msg;
      if (msg) { inp.setAttribute("aria-invalid", "true"); if (ok) inp.focus(); ok = false; } else inp.removeAttribute("aria-invalid");
    });
    return ok;
  }
  function authDone(email) {
    closeModal($("#tc-auth-modal"));
    renderAccount(email); renderChecklist();
    var next = S.afterAuth; S.afterAuth = null;
    if (next) next();
  }
  $("#tc-auth-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var f = e.target;
    if (!validateAuth(f)) return;
    var mode = (e.submitter && e.submitter.value) || "register";
    S.user = f.email.value.trim(); store.set("tc_demo_user", S.user);
    toast(mode === "register" ? "Account created (demo). Welcome!" : "Signed in as " + S.user + " (demo)");
    authDone(S.user);
  });
  function renderAccount(email) {
    email = email || S.user || (API_ON && localStorage.getItem("tc_token") ? "Signed in" : "");
    var av = $("#x-avatar");
    av.textContent = email && email.indexOf("@") > 0 ? email.slice(0, 2).toUpperCase() : email ? "ME" : "?";
    av.classList.toggle("is-guest", !email);
    $("#x-acct-text").textContent = email ? "Signed in" + (email.indexOf("@") > 0 ? " as " + email : "") + (API_ON ? "." : " (demo).") : "Not signed in. Backtests work without an account.";
    var btn = $("#x-acct-btn");
    btn.textContent = email ? "Sign out" : "Create free account";
    if (email) btn.removeAttribute("data-auth-open"); else btn.setAttribute("data-auth-open", "");
  }
  $("#x-acct-btn").addEventListener("click", function () {
    if (!signedIn()) return;
    if (API_ON && window.TradeCognixAPI) window.TradeCognixAPI.signOut(); else { S.user = null; store.set("tc_demo_user", null); toast("Signed out"); }
    renderAccount(""); renderChecklist();
  });
  $("#x-reset").addEventListener("click", function () {
    ["tc_demo_user", "tc_demo_brokers"].forEach(function (k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } });
    S.user = null; S.connected = []; S.sessions = []; S.backtests = []; S.model = null; S.parsed = false;
    $("#x-results").hidden = true; $("#x-rules-panel").hidden = true; $("#x-gen-log").textContent = ""; $("#x-bt-count").textContent = "";
    syncKill(); renderAccount(""); renderBrokers(); setStep(); renderChecklist(); toast("Demo data cleared");
  });

  function renderSettings() {
    renderAccount();
    $$("[data-theme-set]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.themeSet === theme())); });
    $("#x-api-text").textContent = API_ON ? "Connected to " + window.TC_API_BASE + ". Open the site with ?api=off to return to demo mode." : "Demo mode: everything runs in your browser. Set TC_API_BASE in js/config.js, or open with ?api=https://your-api, to use the backend.";
    var b = $("#x-api-badge"); b.textContent = API_ON ? "API" : "Demo"; b.className = "badge " + (API_ON ? "badge--live" : "badge--warn");
  }

  /* ================= onboarding checklist ================= */
  function renderChecklist() {
    var done = { describe: S.parsed, backtest: !!S.model || S.backtests.length > 0, account: signedIn(), broker: S.connected.length > 0, paper: S.sessions.length > 0 };
    var n = 0;
    $$("#x-checklist li").forEach(function (li) { var d = !!done[li.dataset.ck]; li.classList.toggle("is-done", d); if (d) n++; });
    $("#x-ck-count").textContent = n + "/5";
    $("#x-ck-bar").style.width = (n / 5 * 100) + "%";
  }

  /* ================= brokers (shared) ================= */
  var DEMO_BROKERS = [
    { key: "Angel One", name: "Angel One", api: "SmartAPI", status: "live", fields: [["api_key", "API key"], ["client_code", "Client code"], ["pin", "PIN", 1], ["totp_secret", "TOTP secret", 1]] },
    { key: "Zerodha", name: "Zerodha", api: "Kite Connect" }, { key: "Upstox", name: "Upstox", api: "Upstox API" }, { key: "Dhan", name: "Dhan", api: "DhanHQ" },
    { key: "Fyers", name: "Fyers", api: "Fyers API" }, { key: "Groww", name: "Groww", api: "Trade API" }, { key: "ICICI Direct", name: "ICICI Direct", api: "Breeze" },
    { key: "Kotak Neo", name: "Kotak Neo", api: "Neo API" }, { key: "5paisa", name: "5paisa", api: "Xstream" }, { key: "Alice Blue", name: "Alice Blue", api: "ANT API" },
    { key: "Shoonya (Finvasia)", name: "Shoonya (Finvasia)", api: "Shoonya API" }, { key: "Motilal Oswal", name: "Motilal Oswal", api: "MO API" }
  ];
  var BROKER_LOGOS = { "Angel One": "angel-one.png", "Zerodha": "zerodha.svg", "Upstox": "upstox.svg", "Dhan": "dhan.svg", "Fyers": "fyers.svg", "Groww": "groww.png", "ICICI Direct": "icici-direct.svg", "Kotak Neo": "kotak-neo.svg", "5paisa": "5paisa.svg", "Alice Blue": "alice-blue.svg", "Shoonya (Finvasia)": "shoonya.svg", "Shoonya": "shoonya.svg", "Motilal Oswal": "motilal-oswal.png" };
  function brokerLogo(b) {
    var norm = function (x) { return String(x || "").replace(/\(.*\)/, "").toLowerCase().replace(/[^a-z0-9]/g, ""); };
    var f = null, want = [norm(b.key), norm(b.name)];
    Object.keys(BROKER_LOGOS).forEach(function (k) { if (!f && want.indexOf(norm(k)) >= 0) f = BROKER_LOGOS[k]; });
    return f ? '<span class="bcard__logo"><img src="brokers/' + f + '" alt="" loading="lazy" decoding="async" /></span>' : '<span class="bcard__mono" aria-hidden="true">' + esc(initials(b.name)) + "</span>";
  }
  function initials(name) { return name.replace(/\(.*\)/, "").trim().split(/\s+/).map(function (w) { return w[0]; }).join("").slice(0, 2).toUpperCase(); }
  /* list items: { key, name, api, status: live|beta|rolling, connected, saved } */
  function renderBrokers(list) {
    list = list || DEMO_BROKERS.map(function (b) { return { key: b.key, name: b.name, api: b.api, status: b.status || "rolling", connected: S.connected.indexOf(b.key) >= 0 }; });
    $("#x-bgrid").innerHTML = list.map(function (b) {
      var label = b.status === "live" ? '<span class="up">Live</span>' : b.status === "beta" ? "Beta" : "Rolling out";
      var right = b.connected
        ? '<span class="connected x-connected"><span class="dot-live" aria-hidden="true"></span>Connected</span>'
        : '<button class="btn btn--' + (b.status === "live" ? "primary" : "secondary") + ' btn--sm" type="button" data-connect="' + esc(b.key) + '">' + (b.saved ? "Reconnect" : "Connect") + "</button>";
      return '<article class="bcard x-bcard"><div class="bcard__row">' + brokerLogo(b) + "<div><h3>" + esc(b.name) + "</h3><p>" + esc(b.api) + " · " + label + "</p></div></div>" + right + "</article>";
    }).join("");
  }
  function fieldHTML(name, label, secret, required, placeholder) {
    return '<label class="field x-field"><span>' + esc(label) + (required ? "" : " (optional)") + '</span><input name="' + esc(name) + '" ' + (secret ? 'type="password" ' : "") + 'autocomplete="off" ' + (required ? "required " : "") + 'placeholder="' + esc(placeholder || "") + '" /><small class="field__msg"></small></label>';
  }
  function openConnect(key) {
    var b = DEMO_BROKERS.filter(function (x) { return x.key === key; })[0]; if (!b) return;
    var form = $("#x-conn-form"); form.dataset.broker = ""; form.dataset.demo = key;
    $("#x-conn-title").textContent = "Connect " + b.name;
    $("#x-conn-intro").textContent = b.status === "live" ? "Demo only: nothing you type here leaves your browser." : b.name + " support is rolling out. In the demo you can try the form; nothing is sent.";
    var fields = b.fields || [["api_key", "API key"], ["client_id", "Client ID"], ["api_secret", "API secret", 1]];
    $("#x-conn-fields").innerHTML = fields.map(function (f) { return fieldHTML(f[0], f[1], f[2], true); }).join("");
    openModal($("#x-conn-modal"));
  }
  function validateFields(form) {
    var ok = true;
    $$("input[required]", form).forEach(function (i) {
      var wrap = i.closest(".field"), bad = !i.value.trim();
      wrap.classList.toggle("is-error", bad); var m = $(".field__msg", wrap); if (m) m.textContent = bad ? "Required." : "";
      if (bad) { i.setAttribute("aria-invalid", "true"); if (ok) i.focus(); ok = false; } else i.removeAttribute("aria-invalid");
    });
    return ok;
  }
  $("#x-bgrid").addEventListener("click", function (e) { var b = e.target.closest("[data-connect]"); if (b) openConnect(b.dataset.connect); });
  $("#x-conn-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var form = e.target, key = form.dataset.demo;
    if (!validateFields(form)) return;
    if (S.connected.indexOf(key) < 0) S.connected.push(key);
    store.set("tc_demo_brokers", S.connected);
    closeModal($("#x-conn-modal")); renderBrokers(); renderChecklist();
    toast(key + " connected (demo). Nothing was sent.");
  });

  /* ================= demo parser ================= */
  function toTime(h, m, ap) {
    h = +h; m = +(m || 0);
    if (ap === "pm" && h < 12) h += 12;
    if (!ap && h >= 1 && h <= 3) h += 12; // market hours: 3:15 means 15:15
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }
  function parse(text) {
    var t = text.toLowerCase(), r = {};
    var idx = /nifty\s*50\s*stocks?/.test(t) ? "NIFTY 50 stocks" : /bank\s?nifty/.test(t) ? "BANKNIFTY" : /finnifty|fin nifty/.test(t) ? "FINNIFTY" : /midcp|midcap nifty/.test(t) ? "MIDCPNIFTY" : /sensex/.test(t) ? "SENSEX" : /nifty/.test(t) ? "NIFTY" : null;
    if (!idx) { var m = text.match(/\b(?:[Bb]uy|[Ss]ell|[Ss]hort)\s+([A-Z][A-Za-z&]+(?:\s[A-Z][a-z]+)?)/); idx = m ? m[1].toUpperCase() : "Instrument?"; }
    var fs = t.match(/\b(buy|sell|short)\b/), side = fs && fs[1] !== "buy" ? "Sell" : "Buy";
    var opt = /iron\s*condor/.test(t) ? "iron condor" : /straddle/.test(t) ? "ATM straddle" : /strangle/.test(t) ? "OTM strangle" : /\b(call|ce)\b/.test(t) ? "ATM call" : /\b(put|pe)\b/.test(t) ? "ATM put" : null;
    var fut = /futures?/.test(t);
    r.Instrument = idx + (opt ? " options · " + side + " " + opt : fut ? " futures · " + side : " · " + side);
    var tf = t.match(/(\d+)\s*-?\s*(?:min|minute|m\b)/);
    r.Timeframe = tf ? tf[1] + "m" : /hour/.test(t) ? "1h" : /daily|day candle/.test(t) ? "1D" : "5m (default)";
    var emas = [], re = /(\d+)\s*-?\s*(?:period\s*)?ema|ema\s*\(?\s*(\d+)/g, mm;
    while ((mm = re.exec(t))) emas.push(mm[1] || mm[2]);
    var dir = /below|under|crosses down/.test(t) && !/above/.test(t) ? "▼" : "▲";
    var gap = t.match(/gaps?\s*(up|down)\s*(?:by\s*)?(?:more than\s*|over\s*)?(\d+(?:\.\d+)?)\s*%/);
    var rsi = t.match(/rsi\s*\(?(\d+)?\)?[^.]*?(below|under|above|over)\s*(\d+)/);
    if (/opening range|\borb\b/.test(t)) r.Entry = "Break " + (/below|breakdown|low/.test(t) ? "below" : "above") + " 09:15–09:30 range";
    else if (/straddle|strangle|iron\s*condor/.test(t)) r.Entry = side + " " + (opt || "straddle") + " at fixed time";
    else if (gap) r.Entry = "Gap " + gap[1] + " > " + gap[2] + "% at open, fade it";
    else if (rsi) r.Entry = "RSI " + (rsi[1] || 14) + (/below|under/.test(rsi[2]) ? " < " : " > ") + rsi[3];
    else if (emas.length >= 2) r.Entry = "EMA " + emas[0] + " " + dir + " EMA " + emas[1];
    else if (/supertrend/.test(t)) { var st = t.match(/supertrend\s*(\d+)\s*,\s*(\d+)/); r.Entry = "Supertrend " + (st ? st[1] + "," + st[2] : "10,3") + " turns green"; }
    else if (/vwap/.test(t)) r.Entry = /(crosses|breaks|slips)\s+below\s+vwap/.test(t)
      ? "Cross below VWAP" + (emas.length ? ", below EMA " + emas[0] : "")
      : "Pullback to VWAP" + (emas.length ? ", above EMA " + emas[0] : "");
    else r.Entry = "Custom condition · review";
    var times = [], tre = /(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b|(\d{1,2})[:.](\d{2})/g, tm;
    while ((tm = tre.exec(t))) times.push({ v: tm[4] ? toTime(tm[4], tm[5]) : toTime(tm[1], tm[2], tm[3]), at: tm.index });
    var exitT = null, winT = [];
    times.forEach(function (x) { var before = t.slice(Math.max(0, x.at - 30), x.at); if (/exit|close|square|by\s*$|or\s*$/.test(before) || x.v >= "14:00") exitT = x.v; else winT.push({ v: x.v, before: before }); });
    if (/opening range|\borb\b/.test(t)) r.Window = "after 09:30";
    else if (winT.length) r.Window = (/after/.test(winT[0].before) ? "after " : /\bat\b/.test(winT[0].before) ? "at " : "from ") + winT[0].v;
    else r.Window = "09:15–15:30 (market hours)";
    if (/expiry\s*day/.test(t)) r.Window += " · expiry day only";
    var ex = [];
    if (/opposite cross/.test(t)) ex.push("opposite cross");
    else if (/turns red/.test(t)) ex.push("Supertrend flips red");
    else if (rsi) { var after = t.slice(t.indexOf(rsi[0]) + rsi[0].length).match(/above\s*(\d+)/); if (after) ex.push("RSI > " + after[1]); }
    var sl = t.match(/stop\s*loss\s*(\d+(?:\.\d+)?)\s*%/), tg = t.match(/target\s*(\d+(?:\.\d+)?)\s*%/);
    if (sl) ex.push("SL " + sl[1] + "%" + (/each leg|per leg/.test(t) ? " per leg" : /stop\s*loss\s*[\d.]+\s*%\s*of\s*(?:the\s*)?(?:combined\s*)?premium/.test(t) ? " of premium" : ""));
    if (tg) ex.push("target " + tg[1] + "%");
    else if (/target[^.]*previous (?:day'?s )?close|fill(?:s|ed)? the gap/.test(t)) ex.push("target prev close");
    if (r.Timeframe !== "1D" || exitT) ex.push((exitT || "15:15") + " hard exit");
    r.Exit = ex.join(" / ");
    var risk = [], mt = t.match(/max(?:imum)?\s*(\d+)\s*trades?/), dl = t.match(/(?:daily\s*)?loss\s*(?:limit|cap)\s*(?:of\s*)?₹?\s*(\d[\d,]*)/);
    risk.push(mt ? mt[1] + (mt[1] === "1" ? " trade/day" : " trades/day") : "3 trades/day (default)");
    if (dl) risk.push("₹" + inr(+dl[1].replace(/,/g, "")) + " daily loss cap");
    r.Risk = risk.join(" · ");
    return r;
  }
  function explain(r) {
    return r.Instrument.replace(" · ", ": ") + ". Enter on " + r.Entry + " (" + r.Timeframe.replace(" (default)", "") + " candles, " + r.Window + "). Exit on " + r.Exit + ". Risk: " + r.Risk + ".";
  }

  /* ================= demo backtest ================= */
  function rng(seed) { var s = seed >>> 0 || 1; return function () { s = (s + 0x6D2B79F5) >>> 0; var t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function hash(str) { var h = 2166136261; for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function backtest(rules) {
    var r = rng(hash(JSON.stringify(rules)));
    var n = 180 + Math.floor(r() * 220), wr = 0.46 + r() * 0.1, lot = /BANKNIFTY/.test(rules.Instrument) ? 30 : 75;
    var start = Date.UTC(2020, 0, 2, 4, 0), end = Date.now(), span = end - start;
    var nets = [], grossT = [], dates = [], cum = 0, peak = 0, dd = 0, eq = [0], wins = 0, gw = 0, gl = 0;
    var c = { Brokerage: 0, STT: 0, "Exchange & SEBI": 0, "Stamp duty": 0, GST: 0, Slippage: 0 };
    for (var i = 0; i < n; i++) {
      var d = new Date(start + span * (i + r() * 0.8) / n);
      while (d.getUTCDay() === 0 || d.getUTCDay() === 6) d = new Date(d.getTime() + 864e5);
      d.setUTCHours(4, 0 + 15 * Math.floor(r() * 20), 0, 0); // 09:30–14:15 IST
      dates.push(d);
      var win = r() < wr, g = win ? 900 + r() * 2600 : -(700 + r() * 1900);
      g += 220;
      var brok = 40, stt = 20 + r() * 40, exch = 22 + r() * 18, stamp = 3 + r() * 4, gst = (brok + exch) * 0.18, slip = 60 + r() * 90;
      c.Brokerage += brok; c.STT += stt; c["Exchange & SEBI"] += exch; c["Stamp duty"] += stamp; c.GST += gst; c.Slippage += slip;
      var net = g - brok - stt - exch - stamp - gst - slip;
      nets.push(net); grossT.push(g); cum += net; eq.push(cum);
      if (net > 0) { wins++; gw += net; } else gl -= net;
      peak = Math.max(peak, cum); dd = Math.max(dd, peak - cum);
    }
    var gross = grossT.reduce(function (a, b) { return a + b; }, 0), costs = Object.keys(c).reduce(function (a, k) { return a + c[k]; }, 0);
    var step = Math.max(1, Math.floor(eq.length / 140)), vals = [], labels = [];
    for (var j = 0; j < eq.length; j += step) { vals.push(Math.round(eq[j])); labels.push(j === 0 ? start : dates[j - 1].getTime()); }
    if (labels[labels.length - 1] !== dates[n - 1].getTime()) { vals.push(Math.round(eq[eq.length - 1])); labels.push(dates[n - 1].getTime()); }
    var instr = rules.Instrument.split(" ·")[0], base = /BANKNIFTY/.test(instr) ? 52200 : 24600, trades = [];
    var isOpt = /options/.test(rules.Instrument), sideBuy = !/Sell/.test(rules.Instrument);
    for (var k = n - 1; k >= Math.max(0, n - 8); k--) {
      var strike = Math.round((base + (r() - 0.5) * 600) / 50) * 50;
      var name = isOpt ? instr + " " + strike + (/put/.test(rules.Instrument) ? " PE" : /straddle|strangle/.test(rules.Instrument) ? " CE+PE" : " CE") : instr;
      var entry = isOpt ? 90 + r() * 140 : base * (0.98 + r() * 0.04) / (/NIFTY/.test(instr) ? 1 : 20);
      var exit = Math.max(0.05, entry + (sideBuy ? 1 : -1) * grossT[k] / lot);
      trades.push({ when: istDate(dates[k]) + " · " + istTime(dates[k]), side: sideBuy ? "Buy" : "Sell", instrument: name, entry: entry, exit: exit, net: nets[k] });
    }
    return {
      instrument: instr, illustrative: true, badge: "Simulated", note: "Demo mode: these numbers are simulated in your browser to show how results look. They are not a real backtest of your strategy.",
      scope: instr + " · " + rules.Timeframe.replace(" (default)", "") + " · Jan 2020 – " + MON[new Date().getMonth()] + " " + new Date().getFullYear() + " · synthetic data",
      metrics: { trades: n, winRate: wins / n * 100, gross: gross, net: cum, costs: costs, maxDD: dd, pf: gl ? gw / gl : null, expectancy: cum / n },
      equity: { labels: labels, values: vals, fmt: function (l, long) { var d = new Date(+l); return long ? istDate(d) : MON[d.getMonth()] + " ’" + String(d.getFullYear()).slice(2); } },
      costs: c, trades: trades, totalTrades: n
    };
  }

  /* ================= demo handlers (api.js intercepts in capture phase when API is on) ================= */
  $("#x-gen").addEventListener("click", function () {
    triedOnce = true;
    if (!validatePrompt(true)) { input.focus(); return; }
    var text = input.value.trim(), btn = this;
    var done = progress(btn, $("#x-gen-prog"), $("#x-gen-log"), ["Reading instrument and side…", "Finding indicators and timeframe…", "Extracting entry, exit and time window…", "Applying risk guardrails…"]);
    setTimeout(function () {
      done();
      var rules = parse(text);
      var unsure = Object.keys(rules).filter(function (k) { return /\?|review|\(default\)/.test(rules[k]); });
      if (/Instrument\?/.test(rules.Instrument) && /review/.test(rules.Entry)) {
        setLog($("#x-gen-log"), "Couldn't find an instrument or an entry condition. Try naming one, e.g. “Buy Nifty ATM call when 9 EMA crosses above 21 EMA”.", "error");
        return;
      }
      renderRules(rules, explain(rules));
      setLog($("#x-gen-log"), "Parsed " + Object.keys(rules).length + " rules" + (unsure.length ? " · check " + unsure.join(", ") : "") + ".", unsure.length ? "" : "ok");
    }, reduce ? 200 : 1300);
  });

  $("#x-run").addEventListener("click", function () {
    var rules = readRules(), btn = this;
    var empty = Object.keys(rules).filter(function (k) { return !rules[k]; });
    if (empty.length) { setLog($("#x-run-log"), "Fill in " + empty.join(", ") + " before running.", "error"); var el = $('#x-edit [data-k="' + empty[0] + '"]'); if (el) el.focus(); return; }
    var done = progress(btn, $("#x-run-prog"), $("#x-run-log"), ["Loading NSE candles, Jan 2020 to today…", "Simulating fills with slippage…", "Applying brokerage, STT, exchange, GST, stamp duty…", "Computing metrics…"]);
    setTimeout(function () {
      done();
      var model = backtest(rules);
      recordBacktest(model, rules.Entry);
      setLog($("#x-run-log"), "Backtest complete: " + model.metrics.trades + " trades, net " + signed(model.metrics.net) + ".", "ok");
      renderResults(model);
    }, reduce ? 200 : 1500);
  });

  function connectedBroker() { return S.connected[0] || null; }
  function startDemoSession(mode) {
    var r = S.rules || {};
    addSession({ id: S.nextSid++, name: (r.Entry || "Strategy") + " · " + (r.Instrument || "").split(" ·")[0], mode: mode, broker: mode === "live" ? connectedBroker() : (connectedBroker() || "Paper engine") });
    toast(mode === "live" ? "Live session started (demo: no real orders are placed)" : "Paper trading started. Runs on candle close in market hours.");
  }
  $("#x-paper").addEventListener("click", function () {
    if (!S.model) { toast("Run a backtest first"); return; }
    requireAuth(function () { startDemoSession("paper"); });
  });
  $("#x-golive").addEventListener("click", function () {
    if (!S.model) { toast("Run a backtest first"); return; }
    var open = function () {
      $("#x-live-input").value = ""; $("#x-live-confirm").disabled = true;
      $("#x-live-broker").textContent = API_ON ? "your connected broker" : connectedBroker();
      openModal($("#x-live-modal"));
    };
    if (API_ON) { open(); return; }
    requireAuth(function () {
      if (!connectedBroker()) { toast("Connect a broker before going live"); nav("brokers"); return; }
      open();
    });
  });
  $("#x-live-input").addEventListener("input", function (e) {
    var v = e.target.value.trim(), ok = v === "LIVE";
    $("#x-live-confirm").disabled = !ok;
    var h = $("#x-live-hint");
    h.textContent = !v ? "Case-sensitive." : ok ? "Confirmed." : v.toUpperCase() === "LIVE" ? "Type it in capitals: LIVE" : "Type LIVE exactly.";
    h.className = "hint" + (ok ? "" : v ? " is-warn" : "");
  });
  $("#x-live-confirm").addEventListener("click", function () { closeModal($("#x-live-modal")); startDemoSession("live"); });

  /* ================= keyboard ================= */
  doc.addEventListener("keydown", function (e) {
    if (app.hidden || current !== "studio" || openModalEl()) return;
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      var inPrompt = doc.activeElement === input, rulesOn = !$("#x-rules-panel").hidden;
      var b = !inPrompt && rulesOn ? $("#x-run") : $("#x-gen");
      if (!b.disabled) b.click();
    } else if (e.key === "/" && !/INPUT|TEXTAREA/.test(doc.activeElement.tagName) && !doc.activeElement.isContentEditable) {
      e.preventDefault(); input.focus();
    }
  });

  /* ================= public layer for api.js ================= */
  window.TCUI = {
    toast: toast, openModal: openModal, closeModal: closeModal, progress: progress, setLog: setLog,
    renderRules: renderRules, readRules: readRules, renderResults: renderResults, recordBacktest: recordBacktest,
    renderBrokers: renderBrokers, fieldHTML: fieldHTML, validateFields: validateFields, validateAuth: validateAuth, validatePrompt: function () { triedOnce = true; return validatePrompt(true); },
    addSession: addSession, stopSession: stopSession, renderJournalEmpty: renderJournalEmpty, esc: esc, drawDash: drawDash, setSessions: setSessions, killAll: killAll, running: running,
    authDone: authDone, renderAccount: renderAccount, renderChecklist: renderChecklist, setStep: setStep,
    nav: nav, fmt: { inr: inr, rupee: rupee, signed: signed, compact: compact, istDate: istDate, istTime: istTime }, state: S
  };

  /* ================= boot ================= */
  if (API_ON) {
    $("#x-mode-title").textContent = "Connected workspace";
    $("#x-mode-text").textContent = "Wired to the TradeCognix API.";
  }
  setTheme(theme());
  renderBrokers(); renderAccount(); renderChecklist(); setStep(); syncKill();
  tickClock(); setInterval(tickClock, 30000);
  route();
})();
