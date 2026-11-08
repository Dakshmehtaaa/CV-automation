const AF2_REDUCED = () =>
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const AF2_ANIMATABLE =
  typeof Element !== "undefined" && typeof Element.prototype.animate === "function";

function af2Static(el) {
  if (getComputedStyle(el).position === "static") el.style.position = "relative";
}

function af2Remove(el) {
  if (el && el.parentNode) el.parentNode.removeChild(el);
}

function playApplied(row) {
  return new Promise((resolve) => {
    if (!row || AF2_REDUCED() || !AF2_ANIMATABLE) return resolve();

    af2Static(row);

    const overlay = document.createElement("div");
    overlay.className = "af2-overlay";
    overlay.innerHTML =
      '<svg class="af2-check" viewBox="0 0 72 72" width="72" height="72" aria-hidden="true">' +
      '<circle class="af2-ring" cx="36" cy="36" r="26"></circle>' +
      '<circle class="af2-circle" cx="36" cy="36" r="26"></circle>' +
      '<path class="af2-tick" d="M24 37.5 L32.5 46 L48.5 28"></path>' +
      "</svg>";
    row.appendChild(overlay);

    const circle = overlay.querySelector(".af2-circle");
    const tick = overlay.querySelector(".af2-tick");
    const ring = overlay.querySelector(".af2-ring");

    const C = 2 * Math.PI * 26;
    circle.style.strokeDasharray = String(C);
    circle.style.strokeDashoffset = String(C);

    const L = typeof tick.getTotalLength === "function" ? tick.getTotalLength() : 40;
    tick.style.strokeDasharray = String(L);
    tick.style.strokeDashoffset = String(L);

    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      af2Remove(overlay);
      resolve();
    };

    const anims = [
      overlay.animate([{ opacity: 0 }, { opacity: 1 }], {
        duration: 110,
        easing: "ease-out",
        fill: "forwards"
      }),
      circle.animate([{ strokeDashoffset: C }, { strokeDashoffset: 0 }], {
        duration: 300,
        easing: "ease-out",
        fill: "forwards"
      }),
      tick.animate([{ strokeDashoffset: L }, { strokeDashoffset: 0 }], {
        duration: 170,
        delay: 270,
        easing: "ease-out",
        fill: "forwards"
      }),
      ring.animate(
        [
          { transform: "scale(1)", opacity: 0.6 },
          { transform: "scale(1.45)", opacity: 0 }
        ],
        { duration: 200, delay: 430, easing: "ease-out", fill: "both" }
      )
    ];

    Promise.all(
      anims.map((a) => (a.finished ? a.finished.catch(() => {}) : Promise.resolve()))
    ).then(done);

    setTimeout(done, 1200);
  });
}

function playWont(row) {
  return new Promise((resolve) => {
    if (!row || AF2_REDUCED() || !AF2_ANIMATABLE) return resolve();
    af2Static(row);
    const overlay = document.createElement("div");
    overlay.className = "af2-overlay";
    overlay.innerHTML =
      '<svg class="af2-check" viewBox="0 0 72 72" width="72" height="72" aria-hidden="true">' +
      '<circle class="af2-ring af2-wont" cx="36" cy="36" r="26"></circle>' +
      '<circle class="af2-circle af2-wont" cx="36" cy="36" r="26"></circle>' +
      '<path class="af2-tick af2-wont" d="M27 27 L45 45"></path>' +
      '<path class="af2-tick af2-wont" d="M45 27 L27 45"></path>' +
      "</svg>";
    row.appendChild(overlay);
    const circle = overlay.querySelector(".af2-circle");
    const ticks = overlay.querySelectorAll(".af2-tick");
    const ring = overlay.querySelector(".af2-ring");
    const C = 2 * Math.PI * 26;
    circle.style.strokeDasharray = String(C);
    circle.style.strokeDashoffset = String(C);
    ticks.forEach((t) => {
      const L = typeof t.getTotalLength === "function" ? t.getTotalLength() : 26;
      t.style.strokeDasharray = String(L);
      t.style.strokeDashoffset = String(L);
    });
    let settled = false;
    const done = () => { if (settled) return; settled = true; af2Remove(overlay); resolve(); };
    const anims = [
      overlay.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 110, easing: "ease-out", fill: "forwards" }),
      circle.animate([{ strokeDashoffset: C }, { strokeDashoffset: 0 }], { duration: 300, easing: "ease-out", fill: "forwards" }),
      ...[...ticks].map((t, i) => {
        const L = Number(t.style.strokeDasharray);
        return t.animate([{ strokeDashoffset: L }, { strokeDashoffset: 0 }], { duration: 130, delay: 270 + i * 110, easing: "ease-out", fill: "forwards" });
      }),
      ring.animate([{ transform: "scale(1)", opacity: 0.6 }, { transform: "scale(1.45)", opacity: 0 }], { duration: 200, delay: 500, easing: "ease-out", fill: "both" })
    ];
    Promise.all(anims.map((a) => (a.finished ? a.finished.catch(() => {}) : Promise.resolve()))).then(done);
    setTimeout(done, 1200);
  });
}

function af2PlayShapes(row, cls, paths) {
  return new Promise((resolve) => {
    if (!row || AF2_REDUCED() || !AF2_ANIMATABLE) return resolve();
    af2Static(row);
    const overlay = document.createElement("div");
    overlay.className = "af2-overlay";
    overlay.innerHTML =
      '<svg class="af2-check" viewBox="0 0 72 72" width="72" height="72" aria-hidden="true">' +
      `<circle class="af2-ring ${cls}" cx="36" cy="36" r="26"></circle>` +
      `<circle class="af2-circle ${cls}" cx="36" cy="36" r="26"></circle>` +
      paths.map((d) => `<path class="af2-tick ${cls}" d="${d}"></path>`).join("") +
      "</svg>";
    row.appendChild(overlay);
    const circle = overlay.querySelector(".af2-circle");
    const ticks = overlay.querySelectorAll(".af2-tick");
    const ring = overlay.querySelector(".af2-ring");
    const C = 2 * Math.PI * 26;
    circle.style.strokeDasharray = String(C);
    circle.style.strokeDashoffset = String(C);
    const lens = [...ticks].map((t) => {
      const L = typeof t.getTotalLength === "function" ? t.getTotalLength() : 20;
      t.style.strokeDasharray = String(L);
      t.style.strokeDashoffset = String(L);
      return L;
    });
    let settled = false;
    const done = () => { if (settled) return; settled = true; af2Remove(overlay); resolve(); };
    const anims = [
      overlay.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 110, easing: "ease-out", fill: "forwards" }),
      circle.animate([{ strokeDashoffset: C }, { strokeDashoffset: 0 }], { duration: 300, easing: "ease-out", fill: "forwards" }),
      ...[...ticks].map((t, i) => t.animate([{ strokeDashoffset: lens[i] }, { strokeDashoffset: 0 }], { duration: 130, delay: 270 + i * 110, easing: "ease-out", fill: "forwards" })),
      ring.animate([{ transform: "scale(1)", opacity: 0.6 }, { transform: "scale(1.45)", opacity: 0 }], { duration: 200, delay: 500, easing: "ease-out", fill: "both" })
    ];
    Promise.all(anims.map((a) => (a.finished ? a.finished.catch(() => {}) : Promise.resolve()))).then(done);
    setTimeout(done, 1200);
  });
}

function playLater(row) {
  return af2PlayShapes(row, "af2-later", ["M36 23 V36", "M36 36 L45 42"]);
}

function playInactive(row) {
  return af2PlayShapes(row, "af2-inactive", ["M36 22 V39", "M36 47 V47.5"]);
}

/* ------------------------------------------------------------------ */
/* Popovers                                                            */
/* ------------------------------------------------------------------ */

let af2OpenPopover = null;

function af2Popover(anchor, opts) {
  if (af2OpenPopover) af2OpenPopover(null);

  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "af2-popover";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-label", opts.label);

    const head = document.createElement("div");
    head.className = "af2-pop-head";

    const title = document.createElement("span");
    title.className = "af2-pop-title";
    title.textContent = opts.title;

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "af2-pop-close";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.textContent = "\u00d7";

    head.appendChild(title);
    head.appendChild(closeBtn);

    const chips = document.createElement("div");
    chips.className = "af2-chips";

    let settled = false;
    let firstChip = null;

    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (af2OpenPopover === finish) af2OpenPopover = null;
      document.removeEventListener("mousedown", onOutside, true);
      document.removeEventListener("keydown", onKey, true);
      af2Remove(wrap);
      if (anchor && typeof anchor.focus === "function") {
        try {
          anchor.focus({ preventScroll: true });
        } catch (e) {
          anchor.focus();
        }
      }
      resolve(value);
    };

    opts.chips.forEach((chip) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "af2-chip" + (opts.variant === "wont" ? " af2-chip--wont" : "");
      b.textContent = chip.label;
      b.addEventListener("click", () => finish(chip.value));
      chips.appendChild(b);
      if (!firstChip) firstChip = b;
    });

    wrap.appendChild(head);
    wrap.appendChild(chips);
    document.body.appendChild(wrap);

    // Position under the anchor, clamped inside the viewport.
    const rect = anchor
      ? anchor.getBoundingClientRect()
      : { left: 8, right: 8, top: 8, bottom: 8 };
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const bw = wrap.offsetWidth;
    const bh = wrap.offsetHeight;
    const pad = 8;

    const left = Math.max(pad, Math.min(rect.left, vw - bw - pad));
    let top = rect.bottom + 6;
    if (top + bh + pad > vh) top = Math.max(pad, rect.top - bh - 6);

    wrap.style.left = Math.round(left) + "px";
    wrap.style.top = Math.round(top) + "px";

    if (AF2_ANIMATABLE && !AF2_REDUCED()) {
      wrap.animate(
        [
          { opacity: 0, transform: "translateY(4px)" },
          { opacity: 1, transform: "translateY(0px)" }
        ],
        { duration: 120, easing: "ease-out", fill: "both" }
      );
    }

    const onOutside = (e) => {
      if (wrap.contains(e.target)) return;
      if (anchor && anchor.contains(e.target)) return;
      finish(null);
    };

    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        finish(null);
      }
    };

    closeBtn.addEventListener("click", () => finish(null));
    document.addEventListener("mousedown", onOutside, true);
    document.addEventListener("keydown", onKey, true);

    af2OpenPopover = finish;

    requestAnimationFrame(() => {
      if (!settled && firstChip) firstChip.focus();
    });
  });
}

const AF2_WONT_REASONS = [
  { label: "Internship", value: "internship" },
  { label: "Not a fit", value: "not_a_fit" },
  { label: "Already applied", value: "already_applied" },
  { label: "Job closed", value: "job_closed" },
  { label: "Location", value: "location" },
  { label: "Other", value: "other" }
];

const AF2_LATER_CHOICES = [
  { label: "1 day", value: 1 },
  { label: "2 days", value: 2 },
  { label: "3 days", value: 3 },
  { label: "4 days", value: 4 }
];

function askWont(anchor) {
  return af2Popover(anchor, {
    label: "Why won't you apply?",
    title: "Why not?",
    chips: AF2_WONT_REASONS,
    variant: "wont"
  });
}

function askLater(anchor) {
  return af2Popover(anchor, {
    label: "Remind me in",
    title: "Remind me in",
    chips: AF2_LATER_CHOICES
  });
}

window.studioApplyFlow = { playApplied, playWont, playInactive, playLater, askWont, askLater };
