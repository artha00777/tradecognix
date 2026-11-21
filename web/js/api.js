/*
 * TradeCognix API wiring for the static site.
 *
 * When window.TC_API_BASE is set, this file takes over Strategy Studio ("Generate strategy",
 * "Run backtest", paper / live buttons) and the Brokers screen, calling the FastAPI backend.
 * When it is empty, nothing here runs and the page keeps its built-in demo behaviour.
 * If an API call fails, the click is handed back to the demo so the page never dead-ends.
 *
 * It listens in the capture phase on document, so it runs before the page's own handlers and
 * can stop them. No build step, no dependencies: static-hostable on Vercel / Netlify / Pages.
 */
(function () {
  "use strict";
  var BASE = (window.TC_API_BASE || "").replace(/\/+$/, "");
  if (!BASE) return; // demo mode

  var doc = document;
  var $ = function (s, c) { return (c || doc).querySelector(s); };
  var $$ = function (s, c) { return [].slice.call((c || doc).querySelectorAll(s)); };
  var esc = function (v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (ch) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]; }); };
  var inr = function (v) { return Math.round(v).toLocaleString("en-IN"); };
  var rupee = function (v) { return (v < 0 ? "−₹" : "₹") + inr(Math.abs(v || 0)); };
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var state = { spec: null, rules: null, prompt: "", strategyId: null, brokers: [], accounts: [], pendingAfterAuth: null, ws: null, bypass: false };

  /* ---------------- http ---------------- */
  function token() { return localStorage.getItem("tc_token") || ""; }
  function api(path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    if (token()) headers.Authorization = "Bearer " + token();
    return fetch(BASE + path, { method: opts.method || "GET", headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined })
      .then(function (r) {
        return r.text().then(function (t) {
          var data = null;
          try { data = t ? JSON.parse(t) : null; } catch (e) { data = { detail: t }; }
          if (r.status === 401 && token()) { localStorage.removeItem("tc_token"); }
          if (!r.ok) {
            var d = data && data.detail;
            var msg = typeof d === "string" ? d : (d && d.message) || ("HTTP " + r.status);
            var err = new Error(msg); err.status = r.status; throw err;
          }
          return data;
        });
      });
  }

  /* ---------------- ui helpers ---------------- */
  var tt;
  function toast(msg) {
    var t = $("#x-toast"); if (!t) return;
    t.textContent = msg; t.classList.add("is-on"); clearTimeout(tt);
    tt = setTimeout(function () { t.classList.remove("is-on"); }, 3600);
  }
  function progress(prog, log, lines) {
    var btns = $$("#x-gen, #x-run"); btns.forEach(function (b) { b.disabled = true; });
    prog.classList.remove("is-run"); void prog.offsetWidth; prog.classList.add("is-run");
    var i = 0; log.textContent = lines[0];
    var iv = setInterval(function () { i = Math.min(lines.length - 1, i + 1); log.textContent = lines[i]; }, 450);
    return function done() { clearInterval(iv); btns.forEach(function (b) { b.disabled = false; }); };
  }
  function openModal(m) { m.hidden = false; var f = $("input", m); if (f) f.focus(); }
  function closeModal(m) { if (m) m.hidden = true; }

  // Hand a click back to the demo implementation (used when the API is unreachable).
  function fallback(el, why) {
    console.warn("[TradeCognix] API unavailable, using demo:", why);
    toast("API unreachable, showing demo instead");
    ["x-bt-chart", "x-cost-chart"].forEach(function (id) { var c = window.Chart && Chart.getChart(doc.getElementById(id)); if (c) c.destroy(); });
    state.bypass = true; el.click(); state.bypass = false;
  }

  /* ---------------- auth modal (injected, same design classes) ---------------- */
  function ensureAuthModal() {
    if ($("#tc-auth-modal")) return $("#tc-auth-modal");
    var m = doc.createElement("div");
    m.className = "x-modal"; m.id = "tc-auth-modal"; m.hidden = true;
    m.setAttribute("role", "dialog"); m.setAttribute("aria-modal", "true"); m.setAttribute("aria-labelledby", "tc-auth-title");
    m.innerHTML =
      '<form class="x-modal__box" id="tc-auth-form">' +
      '<h3 id="tc-auth-title">Sign in to TradeCognix</h3>' +
      '<p>Your strategies, brokers and journal live in your account.</p>' +
      '<label class="x-field"><span>Email</span><input name="email" type="email" autocomplete="email" required /></label>' +
      '<label class="x-field"><span>Password</span><input name="password" type="password" autocomplete="current-password" minlength="8" required /></label>' +
      '<p class="x-note" id="tc-auth-err" aria-live="polite"></p>' +
      '<div class="x-modal__actions">' +
      '<button class="x-btn x-btn--ghost" type="button" data-close>Cancel</button>' +
      '<button class="x-btn x-btn--ghost" type="submit" value="register">Create account</button>' +
      '<button class="x-btn x-btn--gold" type="submit" value="login">Sign in</button>' +
      "</div></form>";
    doc.body.appendChild(m);
    m.addEventListener("click", function (e) { if (e.target === m) closeModal(m); });
    $("#tc-auth-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var mode = (e.submitter && e.submitter.value) || "login";
      var f = e.target, err = $("#tc-auth-err");
      err.textContent = "";
      api("/api/auth/" + mode, { method: "POST", body: { email: f.email.value, password: f.password.value } })
        .then(function (res) {
          localStorage.setItem("tc_token", res.access_token);
          closeModal(m); toast("Signed in as " + res.user.email);
          loadBrokers();
          var next = state.pendingAfterAuth; state.pendingAfterAuth = null;
          if (next) next();
        })
        .catch(function (x) { err.textContent = x.message; });
    });
    return m;
  }
  function requireAuth(next) {
    if (token()) return next();
    state.pendingAfterAuth = next;
    openModal(ensureAuthModal());
  }

  /* ---------------- Strategy Studio ---------------- */
  function renderRules(rules) {
    $("#x-edit").innerHTML = Object.keys(rules).map(function (k) {
      return '<div class="x-rule"><b>' + esc(k) + '</b><span contenteditable="true" spellcheck="false" role="textbox" aria-label="' + esc(k) + '" data-k="' + esc(k) + '"></span></div>';
    }).join("");
    $$("#x-edit [data-k]").forEach(function (s) { s.textContent = rules[s.dataset.k]; });
  }

  // Apply the edits a user can make in the rule rows back onto the DSL.
  function applyEdits(spec) {
    var s = JSON.parse(JSON.stringify(spec));
    var val = function (k) { var el = $('#x-edit [data-k="' + k + '"]'); return el ? el.textContent.trim() : ""; };
    var tf = val("Timeframe").toLowerCase().replace(/\s+/g, "");
    var tfMap = { "1m": "1m", "3m": "3m", "5m": "5m", "10m": "10m", "15m": "15m", "30m": "30m", "1h": "1h", "60m": "1h", "1d": "1d" };
    if (tfMap[tf]) s.timeframe = tfMap[tf];
    var win = val("Window").match(/(\d{2}:\d{2})\D+(\d{2}:\d{2})/);
    if (win) { s.entry_window.start = win[1]; s.entry_window.end = win[2]; }
    var hx = val("Exit").match(/(\d{2}:\d{2})\s*hard exit/);
    if (hx) s.hard_exit = hx[1];
    var sl = val("Exit").match(/SL\s*(\d+(?:\.\d+)?)%/i);
    s.exit.stop_loss_pct = sl ? parseFloat(sl[1]) : s.exit.stop_loss_pct;
    var tg = val("Exit").match(/target\s*(\d+(?:\.\d+)?)%/i);
    s.exit.target_pct = tg ? parseFloat(tg[1]) : s.exit.target_pct;
    var risk = val("Risk");
    var mt = risk.match(/(\d+)\s*trades?\/day/); if (mt) s.risk.max_trades_per_day = parseInt(mt[1], 10);
    var lots = risk.match(/(\d+)\s*(lot|share)/); if (lots) s.risk.lots = parseInt(lots[1], 10);
    var cap = risk.match(/₹\s*([\d,]+)\s*daily loss cap/); if (cap) s.risk.daily_loss_cap = parseFloat(cap[1].replace(/,/g, ""));
    return s;
  }

  function onGenerate(e) {
    var btn = e.target.closest("#x-gen");
    var text = $("#x-input").value.trim();
    if (!text) { $("#x-input").focus(); toast("Describe a strategy first"); return; }
    $("#x-results").hidden = true;
    var done = progress($("#x-gen-prog"), $("#x-gen-log"), ["Reading instrument and side…", "Finding indicators and timeframe…", "Extracting entry, exit and time window…", "Applying risk guardrails…"]);
    api("/api/strategies/parse", { method: "POST", body: { text: text, use_llm: true } })
      .then(function (res) {
        done();
        state.spec = res.strategy; state.rules = res.rules; state.prompt = text; state.strategyId = null;
        var notes = (res.warnings || []).length ? " Note: " + res.warnings.join(" ") : "";
        $("#x-gen-log").textContent = res.explanation + notes;
        renderRules(res.rules);
        $("#x-rules-panel").hidden = false;
        $("#x-rules-panel").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
      })
      .catch(function (x) {
        done();
        if (x.status === 422) { $("#x-gen-log").textContent = x.message; toast("Couldn't read that strategy"); return; }
        fallback(btn, x.message);
      });
  }

  function chart(id, cfg) {
    var el = doc.getElementById(id);
    var old = window.Chart && Chart.getChart(el); if (old) old.destroy();
    return new Chart(el, cfg);
  }

  function renderResults(res) {
    var m = res.metrics;
    var pf = m.profit_factor == null ? "—" : m.profit_factor.toFixed(2);
    var cells = [
      ["Trades", String(m.trades)], ["Win rate", m.win_rate.toFixed(2) + "%"], ["Gross P&L", rupee(m.gross_pnl), m.gross_pnl >= 0],
      ["Net P&L", rupee(m.net_pnl), m.net_pnl >= 0], ["Max drawdown", rupee(m.max_drawdown), false], ["Profit factor", pf],
      ["Expectancy / trade", rupee(m.expectancy), m.expectancy >= 0]
    ];
    $("#x-mgrid").innerHTML = cells.map(function (x) {
      return '<div class="x-metric"><span>' + x[0] + "</span><strong" + (x.length > 2 ? ' class="' + (x[2] ? "x-up" : "x-down") + '"' : "") + ">" + esc(x[1]) + "</strong></div>";
    }).join("");
    var flags = [];
    if (res.flags.indexOf("synthetic_underlying_data") >= 0) flags.push("synthetic data");
    if (res.flags.indexOf("premium_proxy_black_scholes") >= 0) flags.push("premiums estimated (Black-Scholes)");
    var per = m.period || {};
    $("#x-res-scope").textContent = res.strategy.underlying + " · " + res.strategy.timeframe + " · " + (per.from || "").slice(0, 10) + " to " + (per.to || "").slice(0, 10) + (flags.length ? " · " + flags.join(", ") : " · broker data");
    $("#x-eq-cap").innerHTML = "Net P&amp;L " + (m.net_pnl >= 0 ? "ended at " : "fell to ") + rupee(m.net_pnl) + " over " + m.trades + " trades<small>Cumulative net P&amp;L, ₹ · after costs and slippage</small>";
    $("#x-cost-cap").innerHTML = "Costs took " + rupee(m.total_costs) + " of " + rupee(m.gross_pnl) + " gross<small>Total charges by type, ₹</small>";
    $("#x-results").hidden = false;

    var eq = res.equity.map(function (p) { return Math.round(p.equity); });
    var labels = res.equity.map(function (p) { return p.t; });
    var gold = "var(--x-gold)";
    chart("x-bt-chart", {
      type: "line",
      data: { labels: labels, datasets: [{ label: "Equity", data: eq, fill: true, borderColor: gold, backgroundColor: window.Hark && Hark.wash ? Hark.wash(gold, 0.28) : "rgba(201,168,106,.2)", borderWidth: 2, pointRadius: 0 }] },
      options: {
        animation: reduce ? false : { duration: 1200, easing: "easeOutQuart" }, interaction: { mode: "index", intersect: false },
        scales: { x: { ticks: { maxTicksLimit: 6, callback: function (v, i) { return (labels[i] || "").slice(5, 10); } } }, y: { ticks: { callback: function (v) { return Math.abs(v) >= 100000 ? "₹" + (v / 100000).toFixed(2) + "L" : "₹" + (v / 1000).toFixed(0) + "k"; } } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { title: function (c) { return (c[0].label || "").replace("T", " ").slice(0, 16); }, label: function (c) { return rupee(c.raw); } } } }
      }
    });
    var cb = m.cost_breakdown, parts = { Brokerage: cb.brokerage, STT: cb.stt, Exchange: (cb.exchange || 0) + (cb.sebi || 0) + (cb.stamp || 0), GST: cb.gst, Slippage: cb.slippage };
    chart("x-cost-chart", {
      type: "bar",
      data: { labels: ["Costs"], datasets: Object.keys(parts).map(function (k, i) { return { label: k, data: [Math.round(parts[k] || 0)], backgroundColor: "var(--chart-" + (i + 1) + ")", barThickness: 28 }; }) },
      options: { indexAxis: "y", animation: reduce ? false : undefined, scales: { x: { stacked: true, ticks: { callback: function (v) { return "₹" + (v / 1000).toFixed(0) + "k"; } } }, y: { stacked: true, display: false } }, plugins: { tooltip: { callbacks: { label: function (x) { return x.dataset.label + " " + rupee(x.raw); } } } } }
    });
    $("#x-trades").innerHTML = res.trades.slice(-8).reverse().map(function (t) {
      var d = new Date(t.entry_time);
      return "<tr><td>" + d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) + " " + t.entry_time.slice(11, 16) + "</td><td>" + esc(t.side === "buy" ? "Buy" : t.side === "sell" ? "Sell" : t.side) +
        "</td><td>" + esc(t.instrument) + '</td><td class="r">' + t.entry_price.toFixed(2) + '</td><td class="r">' + t.exit_price.toFixed(2) + '</td><td class="r ' + (t.net_pnl >= 0 ? "x-up" : "x-down") + '">' + (t.net_pnl >= 0 ? "+" : "") + rupee(t.net_pnl) + "</td></tr>";
    }).join("");
    $("#x-results").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }

  function onRun(e) {
    var btn = e.target.closest("#x-run");
    if (!state.spec) { fallback(btn, "no parsed strategy"); return; }
    var spec = applyEdits(state.spec);
    var done = progress($("#x-run-prog"), $("#x-run-log"), ["Loading candles…", "Simulating fills with slippage…", "Applying brokerage, STT, exchange, GST, stamp duty…", "Computing metrics and whipsaws…"]);
    api("/api/backtests/run", { method: "POST", body: { strategy: spec, days: 120 } })
      .then(function (res) {
        done(); state.spec = res.strategy;
        var w = res.metrics.whipsaw;
        $("#x-run-log").textContent = "Backtest complete. " + w.count + " whipsaw trade" + (w.count === 1 ? "" : "s") + " (" + w.pct_of_trades + "%) cost " + rupee(w.net_loss) + "." +
          (res.data_source === "synthetic" ? " Synthetic data until a broker is connected." : "");
        renderResults(res);
      })
      .catch(function (x) {
        done();
        if (x.status === 422) { $("#x-run-log").textContent = x.message; toast("Check the edited rules"); return; }
        fallback(btn, x.message);
      });
  }

  function saveStrategy() {
    if (state.strategyId) return Promise.resolve(state.strategyId);
    return api("/api/strategies", { method: "POST", body: { spec: applyEdits(state.spec), prompt: state.prompt } })
      .then(function (r) { state.strategyId = r.id; return r.id; });
  }

  function watch() {
    if (state.ws || !window.WebSocket) return;
    var url = BASE.replace(/^http/, "ws") + "/ws/live?token=" + encodeURIComponent(token());
    try {
      state.ws = new WebSocket(url);
      state.ws.onmessage = function (m) {
        var d = JSON.parse(m.data);
        if (d.type === "order") toast((d.mode === "live" ? "LIVE " : "Paper ") + d.side + " " + d.qty + " " + d.symbol + " @ " + d.price);
        if (d.type === "trade") toast("Trade closed: " + rupee(d.net_pnl) + " (" + d.exit_reason + ")");
      };
      state.ws.onclose = function () { state.ws = null; };
    } catch (x) { state.ws = null; }
  }

  function onPaper() {
    if (!state.spec) { toast("Generate a strategy first"); return; }
    requireAuth(function () {
      saveStrategy()
        .then(function (id) { return api("/api/engine/start", { method: "POST", body: { strategy_id: id, mode: "paper" } }); })
        .then(function (r) { toast("Paper trading started (session " + r.session_id + "). Runs on candle close in market hours."); watch(); })
        .catch(function (x) { toast(x.message); });
    });
  }

  function onLiveConfirm() {
    closeModal($("#x-live-modal"));
    if (!state.spec) { toast("Generate a strategy first"); return; }
    requireAuth(function () {
      var connected = state.accounts.filter(function (a) { return a.status === "connected"; });
      if (!connected.length) { toast("Connect and test a broker first (Brokers tab)"); return; }
      saveStrategy()
        .then(function (id) {
          return api("/api/strategies/" + id + "/confirm-live", { method: "POST", body: { confirm_text: "LIVE", acknowledge_risk: true } })
            .then(function () { return api("/api/engine/start", { method: "POST", body: { strategy_id: id, mode: "live", broker_account_id: connected[0].id, confirm_text: "LIVE" } }); });
        })
        .then(function (r) { toast("LIVE session " + r.session_id + " started on " + r.broker + ". Kill switch is in the top bar."); watch(); })
        .catch(function (x) { toast(x.message); });
    });
  }

  /* ---------------- Brokers ---------------- */
  function initials(name) { return name.replace(/\(.*\)/, "").trim().split(/\s+/).map(function (w) { return w[0]; }).join("").slice(0, 2).toUpperCase(); }
  function renderBrokers() {
    var grid = $("#x-bgrid"); if (!grid || !state.brokers.length) return;
    grid.innerHTML = state.brokers.map(function (b) {
      var acc = state.accounts.filter(function (a) { return a.broker === b.key; })[0];
      var label = b.status === "live" ? "live" : b.status === "beta" ? "beta" : "rolling out";
      var right = acc && acc.status === "connected"
        ? '<span class="x-connected"><span class="x-dotlive" aria-hidden="true"></span>Connected</span>'
        : '<button class="x-btn x-btn--ghost x-btn--sm" type="button" data-connect="' + esc(b.key) + '">' + (acc ? "Reconnect" : "Connect") + "</button>";
      return '<article class="x-bcard"><div class="x-bcard__row"><span class="x-bcard__mono" aria-hidden="true">' + esc(initials(b.name)) + "</span><div><h3>" + esc(b.name) + "</h3><p>" + esc(b.api) + " · " + label + "</p></div></div>" + right + "</article>";
    }).join("");
  }
  function loadBrokers() {
    api("/api/brokers").then(function (r) {
      state.brokers = r.brokers;
      return token() ? api("/api/brokers/accounts").then(function (a) { state.accounts = a.accounts; }).catch(function () {}) : null;
    }).then(renderBrokers).catch(function (x) { console.warn("[TradeCognix] brokers:", x.message); });
  }
  function openConnect(key) {
    var b = state.brokers.filter(function (x) { return x.key === key; })[0]; if (!b) return;
    var form = $("#x-conn-form");
    form.dataset.broker = key;
    $("#x-conn-title").textContent = "Connect " + b.name;
    $$(".x-field", form).forEach(function (f) { f.remove(); });
    var intro = $("p", form);
    intro.textContent = b.status === "skeleton" ? b.name + " support is rolling out; you can save credentials now." : "Credentials are encrypted on the server and only used to place your orders.";
    var html = b.fields.map(function (f) {
      return '<label class="x-field"><span>' + esc(f.label) + (f.required ? "" : " (optional)") + '</span><input name="' + esc(f.key) + '" ' + (f.secret ? 'type="password" ' : "") + 'autocomplete="off" ' + (f.required ? "required" : "") + ' placeholder="' + esc(f.help || "") + '" /></label>';
    }).join("");
    intro.insertAdjacentHTML("afterend", html);
    openModal($("#x-conn-modal"));
  }
  function onConnectSubmit(e) {
    var form = e.target, key = form.dataset.broker, creds = {};
    $$("input", form).forEach(function (i) { if (i.name && i.value) creds[i.name] = i.value; });
    requireAuth(function () {
      api("/api/brokers/accounts", { method: "POST", body: { broker: key, credentials: creds, test: true } })
        .then(function (r) {
          closeModal($("#x-conn-modal"));
          toast(r.test && r.test.ok ? "Connected to " + key + (r.test.profile && r.test.profile.name ? " as " + r.test.profile.name : "") : "Saved, but the test failed: " + ((r.test && r.test.error) || "unknown"));
          loadBrokers();
        })
        .catch(function (x) { toast(x.message); });
    });
  }

  /* ---------------- capture-phase interception ---------------- */
  doc.addEventListener("click", function (e) {
    if (state.bypass) return;
    var t = e.target;
    if (t.closest("#x-gen")) { e.stopPropagation(); e.preventDefault(); onGenerate(e); }
    else if (t.closest("#x-run")) { e.stopPropagation(); e.preventDefault(); onRun(e); }
    else if (t.closest("#x-paper")) { e.stopPropagation(); e.preventDefault(); onPaper(); }
    else if (t.closest("#x-live-confirm")) { e.stopPropagation(); e.preventDefault(); onLiveConfirm(); }
    else if (t.closest("[data-connect]") && state.brokers.length) { e.stopPropagation(); e.preventDefault(); openConnect(t.closest("[data-connect]").dataset.connect); }
  }, true);
  doc.addEventListener("submit", function (e) {
    if (e.target && e.target.id === "x-conn-form" && e.target.dataset.broker) { e.stopPropagation(); e.preventDefault(); onConnectSubmit(e); }
  }, true);

  doc.addEventListener("DOMContentLoaded", function () {
    var note = $(".x-studio__actions .x-note");
    if (note) note.textContent = "Parsed by the TradeCognix engine";
    api("/api/meta").then(function (m) {
      document.documentElement.setAttribute("data-tc-api", "on");
      window.TC_META = m;
      loadBrokers();
      if (token()) api("/api/auth/me").catch(function () {});
    }).catch(function () { console.warn("[TradeCognix] API not reachable at " + BASE + "; demo handlers stay active on failure."); });
  });

  window.TradeCognixAPI = { api: api, state: state, signOut: function () { localStorage.removeItem("tc_token"); toast("Signed out"); } };
})();
