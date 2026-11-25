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

  var state = { spec: null, rules: null, prompt: "", strategyId: null, saved: [], brokers: [], accounts: [], pendingAfterAuth: null, ws: null, bypass: false };

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
            var msg = typeof d === "string" ? d : Array.isArray(d) ? validationMessage(d) : (d && d.message) || (r.status >= 500 ? "The server hit a problem. Please try again in a minute." : "Request failed (" + r.status + ").");
            var err = new Error(msg); err.status = r.status; throw err;
          }
          return data;
        });
      });
  }

  // FastAPI 422 bodies are [{loc:[...], msg:"..."}]; turn the first few into a readable sentence.
  function validationMessage(list) {
    return list.slice(0, 3).map(function (e) {
      var loc = (e.loc || []).filter(function (p) { return p !== "body" && p !== "query" && typeof p === "string"; });
      var field = loc.length ? loc[loc.length - 1].replace(/_/g, " ") : "";
      var m = String(e.msg || "is invalid").replace(/^Value error, /, "");
      return field ? field.charAt(0).toUpperCase() + field.slice(1) + ": " + m : m;
    }).join(". ") || "Please check what you entered.";
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
        loadSessions();
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
        var sb = $("#x-save"); if (sb) sb.textContent = "Save strategy";
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
    var notes = [];
    if (res.flags.indexOf("synthetic_underlying_data") >= 0) notes.push("These results use generated practice prices, not real NSE history, so they show how the rules and costs behave, not what the strategy would have earned. Connect a broker to backtest on real candles.");
    if (res.flags.indexOf("premium_proxy_black_scholes") >= 0) notes.push("Option premiums are estimated with Black-Scholes, not taken from traded prices.");
    if (res.flags.indexOf("futures_priced_at_spot") >= 0) notes.push("Futures are priced at the index level, so basis and rollover are ignored.");
    var labels = res.equity.map(function (p) { return p.t; });
    var fmt = function (l, long) { var d = new Date(String(l)); if (isNaN(d)) return String(l || ""); return long ? F.istDate(d) + " · " + String(l).slice(11, 16) : String(l).slice(8, 10) + " " + d.toLocaleString("en-IN", { month: "short" }); };
    return {
      instrument: res.strategy.underlying,
      illustrative: flags.length > 0,
      badge: res.flags.indexOf("synthetic_underlying_data") >= 0 ? "Practice data" : flags.length ? "Estimated" : "Broker data",
      note: notes.join(" "),
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
        showSweep();
      })
      .catch(function (x) {
        done();
        if (userError(x)) { UI().setLog($("#x-run-log"), x.message + limitHint(x), "error"); toast(x.status === 422 ? "Check the edited rules" : "Limit reached"); return; }
        fallback(btn, x.message);
      });
  }

  /* Parameter sweep: same rules over timeframes x entry starts (x option modes), ranked by net P&L. */
  var MODE_LABEL = { atm_buy: "Buy ATM", itm_buy: "Buy ITM", atm_sell: "Sell ATM" };
  function showSweep() {
    var box = $("#x-sweep"); if (!box) return;
    box.hidden = !state.spec; $("#x-sweep-wrap").hidden = true; $("#x-sweep-rows").innerHTML = "";
    var opt = state.spec && state.spec.instrument_type === "option";
    $("#x-sweep-note").textContent = "Same rules on 5, 15 and 30-minute candles with 9:20, 9:30 and 9:45 starts" + (opt ? ", buying ATM, buying ITM or selling ATM" : "") + ", ranked by net P&L after charges.";
  }
  function onSweep(e) {
    var btn = e.target.closest("#x-sweep-run");
    if (!token()) { toast("Create a free account to compare settings"); var o = $("[data-auth-open]"); if (o) o.click(); return; }
    var spec = applyEdits(state.spec), opt = spec.instrument_type === "option";
    btn.disabled = true; btn.classList.add("is-loading");
    $("#x-sweep-note").textContent = "Running " + (opt ? 27 : 9) + " backtests on practice prices…";
    api("/api/backtests/sweep", { method: "POST", body: { strategy: spec, days: 120, timeframes: ["5m", "15m", "30m"], entry_starts: ["09:20", "09:30", "09:45"], modes: opt ? ["atm_buy", "itm_buy", "atm_sell"] : [] } })
      .then(function (r) {
        var F = UI().fmt, rows = r.rows.filter(function (x) { return !x.error; }), bad = r.rows.length - rows.length;
        $("#x-sweep-modeh").hidden = !opt;
        state.sweepRows = rows;
        $("#x-sweep-rows").innerHTML = rows.map(function (x, i) {
          return '<tr' + (i === 0 ? ' class="is-best"' : "") + "><td>" + E(x.timeframe) + "</td><td>" + E(x.entry_start) + "</td>" + (opt ? "<td>" + E(MODE_LABEL[x.mode] || x.mode || "") + "</td>" : "") +
            '<td class="r">' + x.trades + '</td><td class="r">' + (x.win_rate == null ? "—" : (+x.win_rate).toFixed(1) + "%") + '</td><td class="r">' + F.rupee(Math.abs(x.max_drawdown)) + '</td><td class="r ' + (x.net_pnl >= 0 ? "up" : "down") + '">' + F.signed(Math.round(x.net_pnl)) + '</td><td class="r"><button class="btn btn--secondary btn--sm" type="button" data-sweep-use="' + i + '">Use</button></td></tr>';
        }).join("");
        $("#x-sweep-wrap").hidden = !rows.length;
        var best = rows[0];
        $("#x-sweep-note").textContent = rows.length ? "Best on net: " + best.timeframe + " candles from " + best.entry_start + (opt && best.mode ? ", " + (MODE_LABEL[best.mode] || best.mode) : "") + ". Practice prices, so treat the ranking as a guide, not a forecast." + (bad ? " " + bad + " combination" + (bad === 1 ? "" : "s") + " could not run." : "") : "None of the combinations could run with these rules.";
      })
      .catch(function (x) { $("#x-sweep-note").textContent = x.message + limitHint(x); })
      .then(function () { btn.disabled = false; btn.classList.remove("is-loading"); });
  }
  function onSweepUse(e) {
    var b = e.target.closest("[data-sweep-use]"); var x = state.sweepRows && state.sweepRows[+b.getAttribute("data-sweep-use")]; if (!x) return;
    var sp = JSON.parse(JSON.stringify(state.spec));
    sp.timeframe = x.timeframe; sp.entry_window = sp.entry_window || {}; sp.entry_window.start = x.entry_start;
    if (x.mode && sp.option) sp.option.mode = x.mode;
    state.spec = sp; state.strategyId = null;
    // the editable rule cells win over the spec in applyEdits, so update them to match
    var cell = function (k) { return $('#x-edit [data-k="' + k + '"]'); };
    if (cell("Timeframe")) cell("Timeframe").textContent = x.timeframe;
    if (cell("Window")) cell("Window").textContent = x.entry_start + "–" + (sp.entry_window.end || "");
    var sb = $("#x-save"); if (sb) sb.textContent = "Save strategy";
    toast("Using " + x.timeframe + " candles from " + x.entry_start + ". Running the backtest…");
    var run = $("#x-run"); if (run) run.click();
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

  /* Sessions from the server, so a reload (or a server restart, which re-attaches paper sessions) shows what is really running. */
  function loadSessions() {
    if (!token()) return;
    Promise.all([api("/api/engine/sessions"), api("/api/strategies").catch(function () { return { strategies: [] }; })]).then(function (rs) {
      var names = {}; (rs[1].strategies || []).forEach(function (s) { names[s.id] = s.name; });
      var F = UI().fmt;
      UI().setSessions((rs[0].sessions || []).slice(0, 20).map(function (s) {
        var d = new Date(s.started_at);
        return { id: s.id, name: names[s.strategy_id] || ("Strategy #" + s.strategy_id), mode: s.mode, broker: s.broker_account_id ? ((state.accounts.filter(function (a) { return a.id === s.broker_account_id; })[0] || {}).broker || "broker") : "paper",
          status: s.status, reason: s.halt_reason || "", started: F.istDate(d) + " · " + F.istTime(d) };
      }));
    }).catch(function (x) { console.warn("[TradeCognix] sessions:", x.message); });
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

  /* ---------------- account data: saved strategies, backtest history, journal ---------------- */
  var E = function (v) { return UI().esc(v); };
  var TF = { "1m": "1 min", "3m": "3 min", "5m": "5 min", "10m": "10 min", "15m": "15 min", "30m": "30 min", "1h": "1 hour", "1d": "Daily" };
  function istStamp(iso) { var d = new Date(iso); if (isNaN(d)) return ""; var F = UI().fmt; return F.istDate(d) + " · " + F.istTime(d); }
  function signedClass(v) { return v >= 0 ? "up" : "down"; }

  function onSave() {
    if (!state.spec) return;
    requireAuth(function () {
      var spec = applyEdits(state.spec), btn = $("#x-save");
      var name = (state.rules && state.rules.Entry ? state.rules.Entry : spec.underlying).slice(0, 80);
      var req = state.strategyId
        ? api("/api/strategies/" + state.strategyId, { method: "PUT", body: { spec: spec, prompt: state.prompt || undefined } })
        : api("/api/strategies", { method: "POST", body: { spec: spec, prompt: state.prompt || undefined, name: name } });
      btn.classList.add("is-loading");
      req.then(function (r) { state.strategyId = r.id; state.spec = r.spec; btn.textContent = "Saved"; toast("Strategy saved to your account"); setTimeout(function () { btn.textContent = "Save changes"; }, 1800); })
        .catch(function (x) { toast(x.message + limitHint(x)); })
        .then(function () { btn.classList.remove("is-loading"); });
    });
  }

  function loadBacktestsView() {
    if (!token()) return;
    $("#x-bt-sub").textContent = "Saved to your account. Open a run to see its full results.";
    api("/api/strategies").then(function (r) {
      var list = r.strategies || [];
      $("#x-strat-panel").hidden = !list.length;
      $("#x-strat-note").textContent = list.length ? list.length + (list.length === 1 ? " saved" : " saved") : "";
      $("#x-strat-body").innerHTML = list.map(function (s) {
        var sp = s.spec || {};
        return "<tr><td class=\"wrap\">" + E(s.name) + (s.spec_error ? '<span class="sub">Needs re-parsing</span>' : "") + "</td><td>" + E(sp.underlying || "") + "</td><td>" + E(TF[sp.timeframe] || sp.timeframe || "") +
          '</td><td class="r">' + istStamp(s.updated_at) + '</td><td><div class="acts"><button class="btn btn--ghost btn--sm" type="button" data-open-strat="' + s.id + '"' + (s.spec_error ? " disabled" : "") + '>Open</button>' +
          '<button class="btn btn--ghost btn--sm" type="button" data-del-strat="' + s.id + '" aria-label="Delete ' + E(s.name) + '">Delete</button></div></td></tr>';
      }).join("");
      state.saved = list;
    }).catch(function (x) { console.warn("[TradeCognix] strategies:", x.message); });
    api("/api/backtests?limit=50").then(function (r) {
      var runs = r.runs || [];
      if (!runs.length) return; // keep the session list / empty state
      $("#x-bt-panel").hidden = false; $("#x-bt-empty").hidden = true;
      $("#x-bt-body").innerHTML = runs.map(function (b) {
        var m = b.metrics || {};
        return "<tr><td class=\"wrap\">" + E(b.name || "Backtest #" + b.id) + (b.data_source === "synthetic" ? '<span class="sub">Practice data</span>' : "") + "</td><td>" + E(b.instrument || "") +
          '</td><td class="r">' + (m.trades || 0) + '</td><td class="r">' + (m.win_rate || 0).toFixed(1) + '%</td><td class="r ' + signedClass(m.net_pnl || 0) + '">' + UI().fmt.signed(m.net_pnl || 0) +
          '</td><td class="r">' + istStamp(b.created_at) + '</td><td><button class="btn btn--ghost btn--sm" type="button" data-open-run="' + b.id + '">Open</button></td></tr>';
      }).join("");
    }).catch(function (x) { console.warn("[TradeCognix] backtests:", x.message); });
  }

  function openRun(id) {
    Promise.all([api("/api/backtests/" + id), api("/api/backtests/" + id + "/trades?limit=2000")]).then(function (rs) {
      var run = rs[0], res = { strategy: run.strategy, metrics: run.metrics, equity: run.equity, flags: run.flags || [], data_source: run.data_source, trades: rs[1].trades || [] };
      var model = toModel(res);
      UI().state.model = model; UI().nav("studio");
      setTimeout(function () { UI().renderResults(model, true); var sw = $("#x-sweep"); if (sw) sw.hidden = true; }, 0);
    }).catch(function (x) { toast(x.message); });
  }

  function openStrategy(id) {
    var s = (state.saved || []).filter(function (x) { return x.id === +id; })[0];
    if (!s) return;
    state.spec = s.spec; state.rules = s.rules; state.prompt = s.prompt || ""; state.strategyId = s.id;
    UI().nav("studio");
    setTimeout(function () {
      var i = $("#x-input"); if (i) { i.value = s.prompt || ""; i.dispatchEvent(new Event("input")); }
      UI().renderRules(s.rules, s.explanation, []);
      UI().setLog($("#x-gen-log"), "Loaded \u201c" + s.name + "\u201d from your account.", "ok");
      var b = $("#x-save"); if (b) b.textContent = "Save changes";
    }, 0);
  }

  function deleteStrategy(id) {
    var s = (state.saved || []).filter(function (x) { return x.id === +id; })[0];
    if (!s || !window.confirm("Delete \u201c" + s.name + "\u201d? Its past backtests and journal entries are kept.")) return;
    api("/api/strategies/" + id, { method: "DELETE" }).then(function () {
      if (state.strategyId === +id) state.strategyId = null;
      toast("Strategy deleted"); loadBacktestsView();
    }).catch(function (x) { toast(x.message); });
  }

  var jmode = "";
  function loadJournal() {
    if (!token()) return; // app.js shows the signed-out state
    var q = jmode ? "?mode=" + jmode : "";
    Promise.all([api("/api/journal" + q), api("/api/journal/summary" + q)]).then(function (rs) {
      var rows = rs[0].entries || [], sum = rs[1].by_strategy || [], any = rows.length > 0, F = UI().fmt;
      $("#x-j-bar").hidden = false;
      $("#x-j-export").hidden = !any; $("#x-j-panel").hidden = !any; $("#x-j-sum-panel").hidden = !any; $("#x-j-empty").hidden = any;
      if (!any) {
        $("#x-j-empty-h").textContent = jmode ? "No " + jmode + " trades yet" : "No trades yet";
        $("#x-j-empty-p").textContent = "Closed trades from your paper sessions land here, with charges and the reason each one exited.";
        return;
      }
      $("#x-j-count").textContent = rows.length + (rows.length === 1 ? " trade" : " trades");
      $("#x-j-sum-body").innerHTML = sum.sort(function (a, b) { return b.net_pnl - a.net_pnl; }).map(function (s) {
        return "<tr><td>" + E(s.strategy_tag) + '</td><td class="r">' + s.trades + '</td><td class="r">' + Number(s.win_rate).toFixed(1) + '%</td><td class="r">' + F.rupee(s.costs) + '</td><td class="r ' + signedClass(s.net_pnl) + '">' + F.signed(s.net_pnl) + "</td></tr>";
      }).join("");
      $("#x-j-body").innerHTML = rows.slice(0, 300).map(function (t) {
        return "<tr><td>" + istStamp(t.exit_time) + "</td><td>" + E(t.strategy_tag || "") + (t.mode === "live" ? ' <span class="badge badge--live">Live</span>' : "") + "</td><td>" + E(t.symbol) + "</td><td>" + (t.side === "sell" ? "Sell" : "Buy") +
          '</td><td class="r">' + t.quantity + '</td><td class="r">' + F.inr(t.entry_price) + '</td><td class="r">' + F.inr(t.exit_price) + '</td><td class="r ' + signedClass(t.net_pnl) + '">' + F.signed(t.net_pnl) + "</td><td>" + E((t.exit_reason || "").replace(/_/g, " ")) + "</td></tr>";
      }).join("");
    }).catch(function (x) { toast("Couldn't load the journal: " + x.message); });
  }

  function exportJournal() {
    var btn = $("#x-j-export"); btn.classList.add("is-loading");
    fetch(BASE + "/api/journal/export.csv" + (jmode ? "?mode=" + jmode : ""), { headers: { Authorization: "Bearer " + token() } })
      .then(function (r) { if (!r.ok) throw new Error("Export failed (" + r.status + ")"); return r.blob(); })
      .then(function (b) {
        var u = URL.createObjectURL(b), a = doc.createElement("a");
        a.href = u; a.download = "tradecognix-journal-" + new Date().toISOString().slice(0, 10) + ".csv"; doc.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(u); }, 1000);
      })
      .catch(function (x) { toast(x.message); })
      .then(function () { btn.classList.remove("is-loading"); });
  }

  /* Dashboard: real account numbers when signed in (journal, saved strategies, engine sessions). */
  var istDay = function (iso) { return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(iso)); };
  function setKpi(n, label, value, sub, cls) {
    $("#x-k" + n + "l").textContent = label; var v = $("#x-k" + n); v.textContent = value; v.className = cls || "";
    var s = $("#x-k" + n + "s"); s.textContent = sub; s.className = "";
  }
  function loadDashboard() {
    if (!token()) {
      $("#x-dash-sub").textContent = "Sample numbers. Create a free account and these become your own.";
      UI().state.dash = null;
      return;
    }
    Promise.all([api("/api/journal"), api("/api/strategies"), api("/api/engine/sessions").catch(function () { return { sessions: [] }; })]).then(function (rs) {
      var trades = rs[0].entries || [], strats = rs[1].strategies || [], sess = rs[2].sessions || [], F = UI().fmt;
      var today = istDay(new Date().toISOString()), cutoff = Date.now() - 30 * 864e5;
      var running = sess.filter(function (s) { return s.status === "running"; });
      var tToday = trades.filter(function (t) { return istDay(t.exit_time) === today; });
      var pnlToday = tToday.reduce(function (s, t) { return s + t.net_pnl; }, 0);
      var t30 = trades.filter(function (t) { return new Date(t.exit_time).getTime() >= cutoff; });
      var wins = t30.filter(function (t) { return t.net_pnl > 0; }).length;
      $("#x-dash-sub").textContent = "Your account at a glance. Paper trading only during beta.";
      setKpi(1, "Net P&L today", tToday.length ? F.signed(Math.round(pnlToday)) : "₹0", tToday.length + (tToday.length === 1 ? " closed trade" : " closed trades") + " · after charges", tToday.length ? (pnlToday >= 0 ? "up" : "down") : "");
      setKpi(2, "Saved strategies", String(strats.length), strats.length ? "Open them from Backtests" : "Save one from the Studio");
      var paperN = running.filter(function (s) { return s.mode === "paper"; }).length;
      setKpi(3, "Running sessions", String(running.length), running.length ? paperN + " paper · " + (running.length - paperN) + " live" : "Start one from a backtest");
      setKpi(4, "Win rate, 30 days", t30.length ? (wins / t30.length * 100).toFixed(1) + "%" : "—", t30.length + (t30.length === 1 ? " trade" : " trades"));
      // cumulative net P&L by IST day, last 30 trading days that had a closed trade
      var byDay = {};
      trades.forEach(function (t) { var d = istDay(t.exit_time); byDay[d] = (byDay[d] || 0) + t.net_pnl; });
      var days = Object.keys(byDay).sort().slice(-30), run = 0;
      var labels = days.map(function (d) { var x = new Date(d + "T12:00:00+05:30"); return F.istDate(x).replace(/ \d{4}$/, ""); });
      var values = days.map(function (d) { run += byDay[d]; return Math.round(run); });
      $("#x-dash-h").textContent = "Cumulative net P&L";
      $("#x-dash-note").textContent = "Paper and live trades · after charges";
      var empty = !values.length;
      $("#x-dash-box").hidden = empty; $("#x-dash-cap").hidden = empty; $("#x-dash-empty").hidden = !empty;
      if (empty) $("#x-dash-empty").textContent = "This chart starts after your first closed paper trade. Backtest a strategy, then start paper trading from its results.";
      else $("#x-dash-cap").innerHTML = "Net " + E(F.signed(values[values.length - 1])) + " over " + days.length + (days.length === 1 ? " trading day" : " trading days") + "<small>Closed trades only</small>";
      UI().state.dash = { labels: labels, values: values };
      UI().drawDash();
      // strategies table
      var sid = {}; running.forEach(function (s) { sid[s.strategy_id] = s.mode; });
      var todayBy = {}; tToday.forEach(function (t) { var k = t.strategy_id; todayBy[k] = todayBy[k] || { n: 0, p: 0 }; todayBy[k].n++; todayBy[k].p += t.net_pnl; });
      $("#x-dash-strats").innerHTML = strats.length ? strats.slice(0, 6).map(function (s) {
        var sp = s.spec || {}, mode = sid[s.id], td = todayBy[s.id];
        var badge = mode === "live" ? '<span class="badge badge--live">Live</span>' : mode === "paper" ? '<span class="badge badge--paper">Paper</span>' : '<span class="badge">Saved</span>';
        return "<tr><td>" + E(s.name) + '<span class="sub">' + E((sp.underlying || "") + " · " + (sp.timeframe || "")) + "</span></td><td>" + badge + '</td><td class="r ' + (td ? (td.p >= 0 ? "up" : "down") : "faint") + '">' + (td ? F.signed(Math.round(td.p)) : "—") + '</td><td class="r">' + (td ? td.n : 0) + "</td></tr>";
      }).join("") : '<tr><td colspan="4" class="faint">No saved strategies yet. Generate rules in the Studio and press Save strategy.</td></tr>';
    }).catch(function (x) { console.warn("[TradeCognix] dashboard:", x.message); });
  }

  doc.addEventListener("tc:view", function (e) {
    if (e.detail === "dashboard") loadDashboard();
    if (e.detail === "paper") loadSessions();
    if (e.detail === "backtests") loadBacktestsView();
    else if (e.detail === "journal") loadJournal();
  });

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
    else if (t.closest("#x-save")) { stop(); onSave(); }
    else if (t.closest("#x-sweep-run")) { stop(); onSweep(e); }
    else if (t.closest("[data-sweep-use]")) { stop(); onSweepUse(e); }
    else if (t.closest("[data-open-run]")) { stop(); openRun(t.closest("[data-open-run]").dataset.openRun); }
    else if (t.closest("[data-open-strat]")) { stop(); openStrategy(t.closest("[data-open-strat]").dataset.openStrat); }
    else if (t.closest("[data-del-strat]")) { stop(); deleteStrategy(t.closest("[data-del-strat]").dataset.delStrat); }
    else if (t.closest("[data-jmode]")) {
      stop(); var jb = t.closest("[data-jmode]"); jmode = jb.dataset.jmode;
      $$("[data-jmode]").forEach(function (b) { b.setAttribute("aria-pressed", String(b === jb)); }); loadJournal();
    }
    else if (t.closest("#x-j-export")) { stop(); exportJournal(); }
  }, true);
  doc.addEventListener("submit", function (e) {
    if (!e.target) return;
    if (e.target.id === "x-conn-form" && e.target.dataset.broker) { e.stopPropagation(); e.preventDefault(); onConnectSubmit(e); }
    else if (e.target.id === "tc-auth-form") { e.stopPropagation(); e.preventDefault(); onAuthSubmit(e); }
  }, true);

  function boot() {
    var note = $("#x-engine-note");
    if (note) note.textContent = "Parsed by the TradeCognix engine";
    var sv = $("#x-save"); if (sv) sv.hidden = false;
    api("/api/meta").then(function (m) {
      document.documentElement.setAttribute("data-tc-api", "on");
      window.TC_META = m;
      loadBrokers();
      // app.js may have opened a view before this script was listening; load its account data now.
      var v = (location.hash.indexOf("#/") === 0 && !$(".view[data-v='" + location.hash.slice(2) + "']:not([hidden])")) ? "" : location.hash.slice(2);
      if (v) doc.dispatchEvent(new CustomEvent("tc:view", { detail: v }));
      if (token()) api("/api/auth/me").then(function (u) { UI().renderAccount(u.email); loadSessions(); }).catch(function () {});
    }).catch(function () { console.warn("[TradeCognix] API not reachable at " + BASE + "; demo handlers stay active on failure."); });
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot); else boot();

  window.TradeCognixAPI = { api: api, state: state, signOut: function () { localStorage.removeItem("tc_token"); toast("Signed out"); } };
})();

/*
 * Pricing CTAs: payments are disabled during beta (runs in demo and API mode).
 * Get Pro / Get Elite open an inline "Free during beta — join the waitlist" form in the card.
 * Demo mode: the email is kept in localStorage ("tc_waitlist"). API mode: POST /api/waitlist,
 * and the entry is also remembered locally so a return visit shows the joined state.
 * Razorpay checkout (backend /api/billing/checkout, /verify) stays in the backend but is not
 * called from the site. The Free CTA keeps data-open-app and is handled by app.js.
 */
(function () {
  "use strict";
  var doc = document;
  var BASE = (window.TC_API_BASE || "").replace(/\/+$/, "");
  var KEY = "tc_waitlist";
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  var NAMES = { pro: "Pro", elite: "Elite" };

  function saved() { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { return null; } }
  function save(entry) { try { localStorage.setItem(KEY, JSON.stringify(entry)); } catch (e) { /* private mode */ } }
  function esc(v) { return String(v).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function panel(btn) {
    var card = btn.closest(".plan"), n = card && card.querySelector(".plan__wait");
    if (!n && card) {
      n = doc.createElement("div"); n.className = "plan__wait"; n.hidden = true;
      btn.insertAdjacentElement("afterend", n);
    }
    return n;
  }
  function joined(n, entry) {
    // Only promise an email when the server actually stored it; demo mode keeps it on this device.
    var body = entry.synced
      ? "<strong>You&rsquo;re on the list.</strong> We&rsquo;ll write to " + esc(entry.email) + " before paid plans open. Until then it&rsquo;s free during beta."
      : "<strong>Saved on this device</strong> (" + esc(entry.email) + "). This is the demo, so nothing was sent. Paid plans aren&rsquo;t open yet: it&rsquo;s free during beta.";
    n.innerHTML = '<p class="plan__wait-msg" role="status"><span class="plan__wait-dot" aria-hidden="true"></span><span>' + body + "</span></p>";
  }
  function form(n, plan) {
    var id = "tc-wl-" + plan;
    n.innerHTML = '<p class="plan__wait-msg"><span class="plan__wait-dot" aria-hidden="true"></span><span><strong>Free during beta &mdash; join the waitlist.</strong> Payments are switched off while we&rsquo;re in beta. Leave your email and we&rsquo;ll tell you when ' + NAMES[plan] + " opens.</span></p>" +
      '<form class="plan__wait-form" novalidate data-plan-form="' + plan + '">' +
      '<label class="sr-only" for="' + id + '">Email</label>' +
      '<input id="' + id + '" name="email" type="email" inputmode="email" autocomplete="email" placeholder="you@example.com" required maxlength="254" />' +
      '<button class="btn btn--primary btn--sm" type="submit">Join</button>' +
      '<p class="plan__wait-err" aria-live="polite"></p></form>';
  }

  function open(btn) {
    var plan = btn.dataset.plan, n = panel(btn); if (!n) return;
    var entry = saved();
    if (entry && entry.email) joined(n, entry); else form(n, plan);
    n.hidden = false;
    var input = n.querySelector("input"); if (input) input.focus({ preventScroll: true });
  }

  function submit(f) {
    var plan = f.dataset.planForm, input = f.email, err = f.querySelector(".plan__wait-err"), btn = f.querySelector("button");
    var email = input.value.trim().toLowerCase();
    err.textContent = "";
    if (!EMAIL_RE.test(email)) { err.textContent = "Enter a valid email address."; input.setAttribute("aria-invalid", "true"); input.focus(); return; }
    input.removeAttribute("aria-invalid");
    var entry = { email: email, plan: plan, at: new Date().toISOString(), synced: false };
    var done = function () { save(entry); [].forEach.call(doc.querySelectorAll(".plan__wait:not([hidden])"), function (n) { joined(n, entry); }); };
    if (!BASE) { done(); return; } // demo: kept on this device only
    btn.classList.add("is-loading"); btn.disabled = true;
    fetch(BASE + "/api/waitlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: email, plan: plan, source: "web" }) })
      .then(function (r) {
        if (r.ok) { entry.synced = true; done(); return; }
        return r.json().catch(function () { return {}; }).then(function (d) {
          var msg = r.status === 422 ? "Enter a valid email address." : r.status === 429 ? "Too many tries. Please wait a minute." : (d && typeof d.detail === "string" && d.detail) || "Something went wrong. Please try again.";
          err.textContent = msg;
        });
      })
      .catch(function () { err.textContent = "Can't reach the server. Check your connection and try again."; })
      .then(function () { btn.classList.remove("is-loading"); btn.disabled = false; });
  }

  doc.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button[data-plan]");
    if (!b) return;
    e.preventDefault();
    open(b);
  });
  doc.addEventListener("submit", function (e) {
    if (!e.target || !e.target.matches("form[data-plan-form]")) return;
    e.preventDefault();
    submit(e.target);
  });
})();
