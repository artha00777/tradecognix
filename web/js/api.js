/*
 * TradeCognix API wiring for the static site.
 *
 * When window.TC_API_BASE is set, this file takes over Strategy Studio ("Generate rules",
 * "Run backtest", paper / live / kill switch) and the Brokers screen, calling the FastAPI backend.
 * When it is empty, nothing here runs and the page keeps its built-in demo behaviour (app.js).
 * If an API call fails, the click is handed back to the demo so the page never dead-ends.
 *
 * Rendering goes through window.TCUI (defined in app.js, loaded before this file), so demo and
 * API mode draw the same UI. Clicks are intercepted in the capture phase on document, so they run
 * before app.js's own handlers and can stop them. No build step, no dependencies.
 */
(function () {
  "use strict";
  var BASE = (window.TC_API_BASE || "").replace(/\/+$/, "");
  if (!BASE) return; // demo mode

  var doc = document;
  var $ = function (s, c) { return (c || doc).querySelector(s); };
  var $$ = function (s, c) { return [].slice.call((c || doc).querySelectorAll(s)); };
  var UI = function () { return window.TCUI; };
  var rupee = function (v) { return UI().fmt.rupee(v); };

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
          if (r.status === 401 && token()) { localStorage.removeItem("tc_token"); UI().renderAccount(""); }
          if (!r.ok) {
            var d = data && data.detail;
            var msg = typeof d === "string" ? d : (d && d.message) || ("HTTP " + r.status);
            var err = new Error(msg); err.status = r.status; throw err;
          }
          return data;
        });
      });
  }

  /* ---------------- helpers ---------------- */
  function toast(msg) { UI().toast(msg); }

  // Hand a click back to the demo implementation (used when the API is unreachable).
  // Errors the user can act on (bad input, plan limits, rate limits) are shown, not swapped for the demo.
  function userError(x) { return x.status === 422 || x.status === 402 || x.status === 403 || x.status === 429; }
  function limitHint(x) { return x.status === 429 || x.status === 402 ? " Create a free account or upgrade to keep going." : ""; }

  function fallback(el, why) {
    console.warn("[TradeCognix] API unavailable, using demo:", why);
    toast("API unreachable, showing demo instead");
    state.bypass = true; el.click(); state.bypass = false;
  }

  /* ---------------- auth (modal lives in index.html: #tc-auth-modal) ---------------- */
  function onAuthSubmit(e) {
    var f = e.target, err = $("#tc-auth-err");
    var mode = (e.submitter && e.submitter.value) || "register";
    err.textContent = "";
    if (!UI().validateAuth(f)) return;
    var btn = e.submitter; if (btn) btn.classList.add("is-loading");
    api("/api/auth/" + mode, { method: "POST", body: { email: f.email.value.trim(), password: f.password.value } })
      .then(function (res) {
        if (btn) btn.classList.remove("is-loading");
        localStorage.setItem("tc_token", res.access_token);
        toast("Signed in as " + res.user.email);
        loadBrokers();
        var next = state.pendingAfterAuth; state.pendingAfterAuth = null;
        UI().state.afterAuth = null;
        UI().authDone(res.user.email);
        if (next) next();
      })
      .catch(function (x) { if (btn) btn.classList.remove("is-loading"); err.textContent = x.message === "Failed to fetch" ? "Can't reach the server. Check your connection." : x.message; });
  }
  function requireAuth(next) {
    if (token()) return next();
    state.pendingAfterAuth = next;
    var f = $("#tc-auth-form"); f.reset(); $("#tc-auth-err").textContent = "";
    $("#tc-auth-sub").textContent = "Backtests are free without an account. Paper trading needs one, so your sessions and journal are saved.";
    UI().openModal($("#tc-auth-modal"));
  }

  /* ---------------- Strategy Studio ---------------- */
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
    if (!UI().validatePrompt()) { $("#x-input").focus(); return; }
    var text = $("#x-input").value.trim();
    var done = UI().progress(btn, $("#x-gen-prog"), $("#x-gen-log"), ["Reading instrument and side…", "Finding indicators and timeframe…", "Extracting entry, exit and time window…", "Applying risk guardrails…"]);
    api("/api/strategies/parse", { method: "POST", body: { text: text, use_llm: true } })
      .then(function (res) {
        done();
        state.spec = res.strategy; state.rules = res.rules; state.prompt = text; state.strategyId = null;
        UI().renderRules(res.rules, res.explanation, res.warnings || []);
        UI().setLog($("#x-gen-log"), "Parsed " + Object.keys(res.rules).length + " rules by the TradeCognix engine.", "ok");
      })
      .catch(function (x) {
        done();
        if (userError(x)) { UI().setLog($("#x-gen-log"), x.message + limitHint(x), "error"); toast(x.status === 422 ? "Couldn't read that strategy" : "Limit reached"); return; }
        fallback(btn, x.message);
      });
  }

  // Convert an API backtest response into the shared results model (see app.js renderResults).
  function toModel(res) {
    var m = res.metrics, cb = m.cost_breakdown || {}, per = m.period || {}, F = UI().fmt;
    var flags = [];
    if (res.flags.indexOf("synthetic_underlying_data") >= 0) flags.push("synthetic data");
    if (res.flags.indexOf("premium_proxy_black_scholes") >= 0) flags.push("premiums estimated (Black-Scholes)");
    var labels = res.equity.map(function (p) { return p.t; });
    var fmt = function (l, long) { var d = new Date(String(l)); if (isNaN(d)) return String(l || ""); return long ? F.istDate(d) + " · " + String(l).slice(11, 16) : String(l).slice(8, 10) + " " + d.toLocaleString("en-IN", { month: "short" }); };
    return {
      instrument: res.strategy.underlying,
      illustrative: flags.length > 0,
      badge: flags.length ? "Synthetic / estimated" : "Broker data",
      scope: res.strategy.underlying + " · " + res.strategy.timeframe + " · " + (per.from || "").slice(0, 10) + " to " + (per.to || "").slice(0, 10) + (flags.length ? " · " + flags.join(", ") : " · broker data"),
      metrics: { trades: m.trades, winRate: m.win_rate, gross: m.gross_pnl, net: m.net_pnl, costs: m.total_costs, maxDD: Math.abs(m.max_drawdown), pf: m.profit_factor, expectancy: m.expectancy },
      equity: { labels: labels, values: res.equity.map(function (p) { return Math.round(p.equity); }), fmt: fmt },
      costs: { Brokerage: cb.brokerage || 0, STT: cb.stt || 0, "Exchange & SEBI": (cb.exchange || 0) + (cb.sebi || 0), "Stamp duty": cb.stamp || 0, GST: cb.gst || 0, Slippage: cb.slippage || 0 },
      trades: res.trades.slice(-8).reverse().map(function (t) {
        return { when: F.istDate(new Date(t.entry_time)) + " · " + t.entry_time.slice(11, 16), side: t.side === "sell" ? "Sell" : "Buy", instrument: t.instrument, entry: t.entry_price, exit: t.exit_price, net: t.net_pnl };
      }),
      totalTrades: m.trades
    };
  }

  function onRun(e) {
    var btn = e.target.closest("#x-run");
    if (!state.spec) { fallback(btn, "no parsed strategy"); return; }
    var spec = applyEdits(state.spec);
    var done = UI().progress(btn, $("#x-run-prog"), $("#x-run-log"), ["Loading candles…", "Simulating fills with slippage…", "Applying brokerage, STT, exchange, GST, stamp duty…", "Computing metrics and whipsaws…"]);
    api("/api/backtests/run", { method: "POST", body: { strategy: spec, days: 120 } })
      .then(function (res) {
        done(); state.spec = res.strategy;
        var w = res.metrics.whipsaw || { count: 0, pct_of_trades: 0, net_loss: 0 };
        UI().setLog($("#x-run-log"), "Backtest complete. " + w.count + " whipsaw trade" + (w.count === 1 ? "" : "s") + " (" + w.pct_of_trades + "%) cost " + rupee(w.net_loss) + "." +
          (res.data_source === "synthetic" ? " Synthetic data until a broker is connected." : ""), "ok");
        var model = toModel(res);
        UI().recordBacktest(model, (state.rules && state.rules.Entry) || res.strategy.underlying);
        UI().renderResults(model);
      })
      .catch(function (x) {
        done();
        if (userError(x)) { UI().setLog($("#x-run-log"), x.message + limitHint(x), "error"); toast(x.status === 422 ? "Check the edited rules" : "Limit reached"); return; }
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

  function sessionName() { var r = state.rules || {}; return (r.Entry || "Strategy") + " · " + ((state.spec && state.spec.underlying) || ""); }

  function onPaper() {
    if (!state.spec) { toast("Generate a strategy first"); return; }
    requireAuth(function () {
      saveStrategy()
        .then(function (id) { return api("/api/engine/start", { method: "POST", body: { strategy_id: id, mode: "paper" } }); })
        .then(function (r) {
          UI().addSession({ id: r.session_id, name: sessionName(), mode: "paper", broker: r.broker || "paper" });
          toast("Paper trading started (session " + r.session_id + "). Runs on candle close in market hours."); watch();
        })
        .catch(function (x) { toast(x.message); });
    });
  }

  function onLiveConfirm() {
    UI().closeModal($("#x-live-modal"));
    if (!state.spec) { toast("Generate a strategy first"); return; }
    requireAuth(function () {
      var connected = state.accounts.filter(function (a) { return a.status === "connected"; });
      if (!connected.length) { toast("Connect and test a broker first (Brokers tab)"); UI().nav("brokers"); return; }
      saveStrategy()
        .then(function (id) {
          return api("/api/strategies/" + id + "/confirm-live", { method: "POST", body: { confirm_text: "LIVE", acknowledge_risk: true } })
            .then(function () { return api("/api/engine/start", { method: "POST", body: { strategy_id: id, mode: "live", broker_account_id: connected[0].id, confirm_text: "LIVE" } }); });
        })
        .then(function (r) {
          UI().addSession({ id: r.session_id, name: sessionName(), mode: "live", broker: r.broker });
          toast("LIVE session " + r.session_id + " started on " + r.broker + ". Kill switch is in the top bar."); watch();
        })
        .catch(function (x) { toast(x.message); });
    });
  }

  function onKill() {
    UI().closeModal($("#x-kill-modal"));
    requireAuth(function () {
      api("/api/engine/kill", { method: "POST" })
        .then(function (r) { UI().killAll(); toast("Kill switch: " + r.sessions_killed + " session(s) halted, positions squared off"); })
        .catch(function (x) { toast("Kill switch failed: " + x.message + ". Square off in your broker app now."); });
    });
  }

  function onStop(sid) {
    api("/api/engine/" + encodeURIComponent(sid) + "/stop", { method: "POST" })
      .then(function () { UI().stopSession(sid); toast("Session #" + sid + " stopped"); })
      .catch(function (x) { toast(x.message); });
  }

  /* ---------------- Brokers ---------------- */
  function renderBrokers() {
    if (!state.brokers.length) return;
    UI().renderBrokers(state.brokers.map(function (b) {
      var acc = state.accounts.filter(function (a) { return a.broker === b.key; })[0];
      return { key: b.key, name: b.name, api: b.api, status: b.status === "live" ? "live" : b.status === "beta" ? "beta" : "rolling", connected: !!(acc && acc.status === "connected"), saved: !!acc };
    }));
    UI().state.connected = state.accounts.filter(function (a) { return a.status === "connected"; }).map(function (a) { return a.broker; });
    UI().renderChecklist();
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
    $("#x-conn-intro").textContent = b.status === "skeleton" ? b.name + " support is rolling out; you can save credentials now." : "Credentials are encrypted on the server and only used to place your orders.";
    $("#x-conn-fields").innerHTML = b.fields.map(function (f) { return UI().fieldHTML(f.key, f.label, f.secret, f.required, f.help); }).join("");
    UI().openModal($("#x-conn-modal"));
  }
  function onConnectSubmit(e) {
    var form = e.target, key = form.dataset.broker, creds = {};
    if (!UI().validateFields(form)) return;
    $$("input", form).forEach(function (i) { if (i.name && i.value) creds[i.name] = i.value; });
    requireAuth(function () {
      api("/api/brokers/accounts", { method: "POST", body: { broker: key, credentials: creds, test: true } })
        .then(function (r) {
          UI().closeModal($("#x-conn-modal"));
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
    var stop = function () { e.stopPropagation(); e.preventDefault(); };
    if (t.closest("#x-gen")) { stop(); onGenerate(e); }
    else if (t.closest("#x-run")) { stop(); onRun(e); }
    else if (t.closest("#x-paper")) { stop(); onPaper(); }
    else if (t.closest("#x-live-confirm")) { stop(); onLiveConfirm(); }
    else if (t.closest("#x-kill-confirm")) { stop(); onKill(); }
    else if (t.closest("[data-stop]")) { stop(); onStop(t.closest("[data-stop]").dataset.stop); }
    else if (t.closest("[data-connect]") && state.brokers.length) { stop(); openConnect(t.closest("[data-connect]").dataset.connect); }
  }, true);
  doc.addEventListener("submit", function (e) {
    if (!e.target) return;
    if (e.target.id === "x-conn-form" && e.target.dataset.broker) { e.stopPropagation(); e.preventDefault(); onConnectSubmit(e); }
    else if (e.target.id === "tc-auth-form") { e.stopPropagation(); e.preventDefault(); onAuthSubmit(e); }
  }, true);

  function boot() {
    var note = $("#x-engine-note");
    if (note) note.textContent = "Parsed by the TradeCognix engine";
    api("/api/meta").then(function (m) {
      document.documentElement.setAttribute("data-tc-api", "on");
      window.TC_META = m;
      loadBrokers();
      if (token()) api("/api/auth/me").then(function (u) { UI().renderAccount(u.email); }).catch(function () {});
    }).catch(function () { console.warn("[TradeCognix] API not reachable at " + BASE + "; demo handlers stay active on failure."); });
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot); else boot();

  window.TradeCognixAPI = { api: api, state: state, signOut: function () { localStorage.removeItem("tc_token"); toast("Signed out"); } };
})();
