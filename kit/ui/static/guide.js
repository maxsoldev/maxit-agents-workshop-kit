/* Guide side panel: read a doc next to any page. Plain JS, no framework.
   State in localStorage "kit-guide": {open, doc, scroll, width}. The doc HTML comes from /docs/fragment.
   This file loads in <head>, so the page gets its open/closed layout before the first paint. */
(function () {
  const KEY = "kit-guide";
  const root = document.documentElement;
  const onGuidePage = location.pathname.startsWith("/docs");
  const firstVisit = localStorage.getItem(KEY) === null;
  let state = {};
  try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { state = {}; }
  // First time on the Run page: open the tutorial, so people read it while they click.
  if (firstVisit && /^\/uc\/[^/]+\/run$/.test(location.pathname)) {
    state = { open: true, doc: "docs/tutorial.md", scroll: 0 };
  }
  const save = () => localStorage.setItem(KEY, JSON.stringify(state));
  const clampWidth = (w) => Math.max(320, Math.min(w || 560, window.innerWidth - 360));
  const setWidth = (w) => root.style.setProperty("--gp-w", clampWidth(w) + "px");
  setWidth(state.width);
  if (state.open && !onGuidePage) root.classList.add("gp-open");

  document.addEventListener("DOMContentLoaded", () => {
    // The full Guide page: "Open as side panel" goes back to the last kit page, with this doc in the panel.
    const asPanel = document.getElementById("gp-as-panel");
    if (asPanel) asPanel.addEventListener("click", () => {
      Object.assign(state, { open: true, doc: asPanel.dataset.doc, scroll: 0 });
      save();
      const ref = document.referrer ? new URL(document.referrer) : null;
      const back = ref && ref.origin === location.origin && !ref.pathname.startsWith("/docs");
      location.href = back ? ref.pathname + ref.search : asPanel.dataset.fallback;
    });

    const panel = document.getElementById("guide-panel");
    if (!panel) return;
    const body = panel.querySelector(".gp-body");
    const pick = panel.querySelector("select");
    const full = document.getElementById("gp-full");
    const toggle = document.getElementById("gp-toggle");

    function load(doc, scroll, anchor) {
      if (!doc || !pick.querySelector(`option[value="${CSS.escape(doc)}"]`)) doc = pick.options[0].value;
      state.doc = doc;
      pick.value = doc;
      full.href = "/docs?name=" + encodeURIComponent(doc);
      fetch("/docs/fragment?name=" + encodeURIComponent(doc))
        .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
        .then((html) => { body.innerHTML = '<div class="doc">' + html + "</div>"; })
        .catch((e) => { body.innerHTML = '<p class="alert">Could not load the doc (' + e + ").</p>"; })
        .then(() => {
          const target = anchor && body.querySelector("#" + CSS.escape(anchor));
          if (target) target.scrollIntoView(); else body.scrollTop = scroll || 0;
          state.scroll = body.scrollTop;
          save();
        });
    }
    function show(open) {
      state.open = open;
      root.classList.toggle("gp-open", open);
      toggle.setAttribute("aria-pressed", open);
      if (open && !body.firstElementChild) load(state.doc, state.scroll);
      save();
    }

    toggle.addEventListener("click", (e) => { e.preventDefault(); show(!state.open); });
    panel.querySelector(".gp-close").addEventListener("click", () => show(false));
    full.addEventListener("click", () => { state.open = false; save(); });
    pick.addEventListener("change", () => load(pick.value, 0));
    body.addEventListener("scroll", () => { state.scroll = body.scrollTop; save(); }, { passive: true });
    body.addEventListener("click", (ev) => {
      const a = ev.target.closest("a");
      if (!a) return;
      const href = a.getAttribute("href") || "";
      if (a.dataset.doc) { ev.preventDefault(); load(a.dataset.doc, 0, href.split("#")[1]); }
      else if (href.startsWith("#")) {
        const t = body.querySelector(href.length > 1 ? "#" + CSS.escape(href.slice(1)) : null);
        ev.preventDefault();
        if (t) t.scrollIntoView();
      }
    });

    // Drag the left edge to change the width (wide screens only).
    panel.querySelector(".gp-resize").addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      const move = (e) => { state.width = clampWidth(window.innerWidth - e.clientX); setWidth(state.width); };
      const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); save(); };
      addEventListener("pointermove", move);
      addEventListener("pointerup", up);
    });

    toggle.setAttribute("aria-pressed", !!state.open);
    if (state.open) load(state.doc, state.scroll);
  });
})();
