/*
 * TradeCognix marketing-site motion. Vanilla JS, no dependencies.
 * Runs only when prefers-reduced-motion is not "reduce" (the <head> script adds html.motion
 * under the same condition). Without it, every element is already in its final state.
 * Touches only the marketing markup; the app (#x-app) and app.js are left alone.
 */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  if (!root.classList.contains("motion") || !("IntersectionObserver" in window)) { root.classList.remove("motion"); return; }

  var $ = function (s, c) { return (c || doc).querySelector(s); };
  var $$ = function (s, c) { return [].slice.call((c || doc).querySelectorAll(s)); };
  var raf = window.requestAnimationFrame.bind(window);
  var now = function () { return performance.now(); };
  var easeOut = function (t) { return 1 - Math.pow(1 - t, 3); };
  var inr = function (v, dec) { return dec ? v.toFixed(dec) : Math.round(v).toLocaleString("en-IN"); };
  var finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  function once(el, fn, opts) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); fn(e.target); } });
    }, opts || { threshold: 0.2 });
    io.observe(el);
  }

  /* Tween a number into an element with Indian formatting. */
  function countTo(el, from, to, ms, opt) {
    opt = opt || {};
    var t0 = now(), dec = opt.dec || 0, pre = opt.pre || "", suf = opt.suf || "";
    (function tick() {
      var p = Math.min(1, (now() - t0) / ms), v = from + (to - from) * easeOut(p);
      el.textContent = pre + inr(v, dec) + suf;
      if (p < 1) raf(tick);
    })();
  }

  /* ---------------- hero: type -> chips -> curve -> counters ---------------- */
  var mock = $(".mock"), prompt = $("[data-type]"), status = $("[data-hero-status]");
  var kpis = $$(".mock [data-count]");
  if (mock && prompt) {
    var small = prompt.querySelector("small");
    var full = prompt.textContent.slice(small ? small.textContent.length : 0).trim();
    var typed = doc.createElement("span"), caret = doc.createElement("span"), rest = doc.createElement("span");
    caret.className = "tw-caret"; rest.className = "tw-rest"; rest.textContent = full;
    prompt.textContent = ""; if (small) prompt.appendChild(small);
    prompt.appendChild(typed); prompt.appendChild(caret); prompt.appendChild(rest);
    prompt.setAttribute("aria-label", full);
    kpis.forEach(function (k) { var d = +k.dataset.dec || 0; k.textContent = (k.dataset.pre || "") + inr(0, d) + (k.dataset.suf || ""); });
    var eq = $(".mock__chart .eq");
    if (eq && eq.getTotalLength) mock.style.setProperty("--len", Math.ceil(eq.getTotalLength()) + 1);
    var rows = $$(".spec > div", mock), logs = $$(".olog > div", mock);
    if (status) { status.textContent = "Parsing"; status.classList.add("is-busy"); }

    var startHero = function () {
      var i = 0;
      (function type() {
        if (i >= full.length) { caret.classList.add("is-done"); setTimeout(function () { caret.remove(); }, 3200); return setTimeout(chips, 260); }
        var step = Math.random() < 0.3 ? 2 : 1;
        i = Math.min(full.length, i + step);
        typed.textContent = full.slice(0, i); rest.textContent = full.slice(i);
        var ch = full.charAt(i - 1);
        setTimeout(type, /[.,]/.test(ch) ? 120 : 12 + Math.random() * 16);
      })();
      function chips() {
        if (status) status.textContent = "Backtesting";
        rows.forEach(function (r, k) { setTimeout(function () { r.classList.add("is-in"); }, k * 130); });
        setTimeout(curve, rows.length * 130 + 200);
      }
      function curve() {
        mock.classList.add("is-drawn");
        kpis.forEach(function (k) { countTo(k, 0, parseFloat(k.dataset.count), 1500, { dec: +k.dataset.dec || 0, pre: k.dataset.pre, suf: k.dataset.suf }); });
        setTimeout(function () {
          mock.classList.add("is-done");
          if (status) { status.textContent = "Backtested"; status.classList.remove("is-busy"); }
          logs.forEach(function (l, k) { setTimeout(function () { l.classList.add("is-in"); }, 200 + k * 260); });
        }, 1600);
      }
    };
    once(mock, function () { setTimeout(startHero, 350); }, { threshold: 0.1 });
  }

  /* ---------------- hero background: slow candlestick drift ---------------- */
  var cv = $(".hero__bg");
  if (cv && cv.getContext) {
    var ctx = cv.getContext("2d"), W = 0, H = 0, dpr = Math.min(2, window.devicePixelRatio || 1);
    var candles = [], STEP = 16, BW = 7, off = 0, last = 0, running = false, visible = false, colors = {};
    var seed = 7, rnd = function () { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
    var price = 0.5;
    function nextCandle() {
      var o = price, c = Math.max(0.12, Math.min(0.88, o + (rnd() - 0.48) * 0.07));
      var hi = Math.max(o, c) + rnd() * 0.03, lo = Math.min(o, c) - rnd() * 0.03;
      price = c; return { o: o, c: c, h: hi, l: lo };
    }
    function readColors() {
      var cs = getComputedStyle(root);
      colors.up = cs.getPropertyValue("--brand").trim() || "#00c46a";
      colors.down = cs.getPropertyValue("--down").trim() || "#ff6161";
      colors.grid = cs.getPropertyValue("--border").trim() || "#23272e";
    }
    function size() {
      var r = cv.getBoundingClientRect(); W = r.width; H = r.height;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      while (candles.length < Math.ceil(W / STEP) + 3) candles.push(nextCandle());
      draw();
    }
    function y(v) { return H * 0.12 + (1 - v) * H * 0.62; }
    function draw() {
      ctx.clearRect(0, 0, W, H);
      ctx.globalAlpha = 0.5; ctx.strokeStyle = colors.grid; ctx.lineWidth = 1; ctx.beginPath();
      for (var gx = (-off % 56 + 56) % 56; gx < W; gx += 56) { ctx.moveTo(Math.round(gx) + 0.5, 0); ctx.lineTo(Math.round(gx) + 0.5, H); }
      for (var gy = 0; gy < H; gy += 56) { ctx.moveTo(0, gy + 0.5); ctx.lineTo(W, gy + 0.5); }
      ctx.stroke();
      for (var i = 0; i < candles.length; i++) {
        var k = candles[i], x = i * STEP - off, up = k.c >= k.o;
        if (x < -STEP || x > W + STEP) continue;
        ctx.globalAlpha = up ? 0.28 : 0.22;
        ctx.strokeStyle = ctx.fillStyle = up ? colors.up : colors.down;
        ctx.beginPath(); ctx.moveTo(x + BW / 2, y(k.h)); ctx.lineTo(x + BW / 2, y(k.l)); ctx.stroke();
        var top = y(Math.max(k.o, k.c)), h = Math.max(1.5, Math.abs(y(k.o) - y(k.c)));
        ctx.fillRect(x, top, BW, h);
      }
      ctx.globalAlpha = 1;
    }
    function frame(t) {
      if (!running) return;
      var dt = last ? Math.min(64, t - last) : 16; last = t;
      off += dt * 0.012;
      if (off >= STEP) { off -= STEP; candles.shift(); candles.push(nextCandle()); }
      draw(); raf(frame);
    }
    function setRun() {
      var want = visible && !doc.hidden && root.getAttribute("data-theme") !== null && !$("#x-site").hidden;
      if (want && !running) { running = true; last = 0; raf(frame); } else if (!want) running = false;
    }
    readColors(); size();
    new IntersectionObserver(function (es) { visible = es[0].isIntersecting; setRun(); }).observe(cv);
    doc.addEventListener("visibilitychange", setRun);
    var rt; window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(size, 120); }, { passive: true });
    new MutationObserver(function () { readColors(); draw(); setRun(); }).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    var site = $("#x-site"); if (site) new MutationObserver(setRun).observe(site, { attributes: true, attributeFilter: ["hidden"] });
  }

  /* ---------------- section headings decode once ---------------- */
  var GLYPHS = "abcdeghknopqrsuvxz0123456789₹%";
  $$(".site .h2").forEach(function (h) {
    once(h, function () {
      var txt = h.textContent, len = txt.length, t0 = now(), dur = 520;
      var at = []; for (var i = 0; i < len; i++) at.push((i / len) * dur + Math.random() * 180);
      h.setAttribute("aria-label", txt);
      h.style.minHeight = h.offsetHeight + "px";
      (function tick() {
        var e = now() - t0, out = "", done = true;
        for (var i = 0; i < len; i++) {
          var ch = txt.charAt(i);
          if (e >= at[i] || /\s/.test(ch)) out += ch;
          else { done = false; out += GLYPHS.charAt((Math.random() * GLYPHS.length) | 0); }
        }
        h.textContent = out;
        if (!done) raf(tick); else { h.textContent = txt; h.style.minHeight = ""; h.removeAttribute("aria-label"); }
      })();
    }, { threshold: 0.6 });
  });

  /* ---------------- staggered scroll reveals ---------------- */
  var groups = [".brokers__row .wordmark", ".step", ".feat", ".plan", ".costs > .card", ".biz > .panel", ".checks li", ".faq details", ".sec__head .lede", ".cta-band .lede", ".cta-band .btns"];
  var rio = new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); rio.unobserve(e.target); } });
  }, { threshold: 0.12, rootMargin: "0px 0px -6% 0px" });
  groups.forEach(function (sel) {
    var els = $$(".site " + sel);
    els.forEach(function (el, i) { el.classList.add("rv"); el.style.setProperty("--i", sel === ".brokers__row .wordmark" ? i % 6 : Math.min(i, 7)); rio.observe(el); });
  });
  /* clear the stagger delay once revealed so hover transitions stay snappy */
  doc.addEventListener("transitionend", function (e) { var t = e.target; if (t.classList && t.classList.contains("rv") && t.classList.contains("in") && e.propertyName === "transform") t.style.transitionDelay = "0s"; });

  /* ---------------- how-it-works connector draws with scroll ---------------- */
  var steps = $(".steps");
  if (steps) {
    var line = $(".steps__line", steps), stepEls = $$(".step", steps), ticking = false, active = false;
    var update = function () {
      ticking = false;
      var r = steps.getBoundingClientRect(), vh = window.innerHeight;
      var p = Math.max(0, Math.min(1, (vh * 0.8 - r.top) / (r.height * 0.9)));
      line.style.setProperty("--p", p.toFixed(3));
      stepEls.forEach(function (s) { s.classList.toggle("is-lit", s.getBoundingClientRect().top < vh * 0.78); });
    };
    var onScroll = function () { if (!ticking && active) { ticking = true; raf(update); } };
    line.style.setProperty("--p", 0);
    new IntersectionObserver(function (es) { active = es[0].isIntersecting; if (active) update(); }, { rootMargin: "20% 0px" }).observe(steps);
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
  }

  /* ---------------- cursor spotlight on cards ---------------- */
  if (finePointer) {
    doc.addEventListener("pointermove", function (e) {
      var c = e.target.closest && e.target.closest(".feat, .plan"); if (!c) return;
      var r = c.getBoundingClientRect();
      c.style.setProperty("--mx", (e.clientX - r.left) + "px");
      c.style.setProperty("--my", (e.clientY - r.top) + "px");
    }, { passive: true });

    /* magnetic primary CTAs */
    $$("[data-magnetic]").forEach(function (b) {
      b.addEventListener("pointermove", function (e) {
        var r = b.getBoundingClientRect(), dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
        b.style.transform = "translate(" + Math.max(-8, Math.min(8, dx * 0.18)).toFixed(1) + "px," + Math.max(-6, Math.min(6, dy * 0.3)).toFixed(1) + "px)";
      });
      b.addEventListener("pointerleave", function () { b.style.transform = ""; });
    });
  }

  /* ---------------- pricing: roll the number on toggle ---------------- */
  var proPrice = $("#x-pro-price");
  if (proPrice) {
    var val = function (s) { return parseInt(String(s).replace(/[^\d]/g, ""), 10) || 0; };
    var prev = val(proPrice.textContent);
    $$("[data-bill]").forEach(function (b) {
      b.addEventListener("click", function () {
        var to = val(proPrice.textContent); if (to === prev) return;
        var from = prev; prev = to;
        proPrice.classList.remove("is-rolling"); void proPrice.offsetWidth; proPrice.classList.add("is-rolling");
        countTo(proPrice, from, to, 520, { pre: "₹" });
      });
    });
  }

  /* ---------------- costs eat into gross ---------------- */
  var eat = $(".eat"), wf = $(".waterfall"), netEl = $("[data-eat-net]");
  if (eat) {
    if (netEl) netEl.textContent = "₹1,30,000";
    once(eat, function () {
      eat.classList.add("in");
      if (netEl) setTimeout(function () { countTo(netEl, 130000, 53000, 1500, { pre: "₹" }); }, 350);
    }, { threshold: 0.5 });
  }
  if (wf) once(wf, function () { wf.classList.add("in"); }, { threshold: 0.3 });
})();
