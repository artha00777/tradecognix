/*
 * TradeCognix front-end configuration.
 *
 * Leave TC_API_BASE empty to run the site in demo mode (everything simulated in the browser).
 * Set it to your backend URL to wire Strategy Studio, backtests, paper/live and brokers to the API:
 *   window.TC_API_BASE = "https://api.tradecognix.in";
 * You can also open the site with ?api=https://your-api (remembered in localStorage) or ?api=off.
 */
(function () {
  var params = new URLSearchParams(window.location.search);
  var fromQuery = params.get("api");
  if (fromQuery === "off" || fromQuery === "default") {
    // "off" forces browser-only demo mode for this visit; "default" forgets any saved override.
    localStorage.removeItem("tc_api_base");
  } else if (fromQuery) {
    localStorage.setItem("tc_api_base", fromQuery.replace(/\/+$/, ""));
  }
  window.TC_API_BASE = fromQuery === "off" ? "" : (window.TC_API_BASE || localStorage.getItem("tc_api_base") || "https://tradecognix-api.onrender.com");
})();
