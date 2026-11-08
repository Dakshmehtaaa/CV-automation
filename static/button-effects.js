/* Small, origin-aware feedback for primary actions and Home date filters.
   Delegated events keep buttons added by the Studio SPA working automatically. */
(() => {
  'use strict';

  const SELECTOR = 'button.button-primary, a.button-primary, .db-filter-btn';
  const root = document.documentElement;
  if (root.dataset.cvButtonEffects === 'ready') return;
  root.dataset.cvButtonEffects = 'ready';

  const canHover = window.matchMedia
    ? window.matchMedia('(hover: hover) and (pointer: fine)')
    : { matches: false };
  const states = new WeakMap();
  const managed = new Set();

  function isDisabled(element) {
    if (element.matches(':disabled')) return true;
    for (let node = element; node; node = node.parentElement) {
      if (node.getAttribute('aria-disabled')?.toLowerCase() === 'true') return true;
    }
    return false;
  }

  function buttonFrom(event) {
    const target = event.target;
    return target instanceof Element ? target.closest(SELECTOR) : null;
  }

  function stateFor(element) {
    let state = states.get(element);
    if (!state) {
      state = { hovering: false, focused: false };
      states.set(element, state);
      managed.add(element);
    }
    if (!element.classList.contains('cv-origin-target')) {
      element.classList.add('cv-origin-target');
    }
    if (!element.querySelector(':scope > .cv-origin-fill')) {
      const fill = document.createElement('span');
      fill.className = 'cv-origin-fill';
      fill.setAttribute('aria-hidden', 'true');
      element.prepend(fill);
    }
    return state;
  }

  function place(element, x, y) {
    const width = element.clientWidth;
    const height = element.clientHeight;
    const farthest = Math.hypot(Math.max(x, width - x), Math.max(y, height - y));
    element.style.setProperty('--cv-origin-x', `${x}px`);
    element.style.setProperty('--cv-origin-y', `${y}px`);
    element.style.setProperty('--cv-origin-size', `${Math.ceil(farthest * 2)}px`);
  }

  function clearState(element) {
    const state = states.get(element);
    if (state) {
      state.hovering = false;
      state.focused = false;
    }
    element.classList.remove('cv-origin-active');
    element.style.removeProperty('--cv-origin-x');
    element.style.removeProperty('--cv-origin-y');
    element.style.removeProperty('--cv-origin-size');
  }

  function dispose(element) {
    clearState(element);
    states.delete(element);
    managed.delete(element);
    element.classList.remove('cv-origin-target');
    element.querySelector(':scope > .cv-origin-fill')?.remove();
  }

  function sync(element, state) {
    if (isDisabled(element)) {
      state.hovering = false;
      state.focused = false;
    }
    element.classList.toggle('cv-origin-active', state.hovering || state.focused);
  }

  document.addEventListener('pointerover', event => {
    const element = buttonFrom(event);
    if (!element || !canHover.matches || event.pointerType === 'touch' || isDisabled(element)) return;
    if (event.relatedTarget instanceof Node && element.contains(event.relatedTarget)) return;

    const state = stateFor(element);
    if (!state.focused) {
      const rect = element.getBoundingClientRect();
      place(element, event.clientX - rect.left - element.clientLeft, event.clientY - rect.top - element.clientTop);
    }
    state.hovering = true;
    sync(element, state);
  });

  document.addEventListener('pointerout', event => {
    const element = buttonFrom(event);
    if (!element || event.pointerType === 'touch') return;
    if (event.relatedTarget instanceof Node && element.contains(event.relatedTarget)) return;

    const state = states.get(element);
    if (!state?.hovering) return;
    state.hovering = false;
    if (!state.focused) {
      const rect = element.getBoundingClientRect();
      place(element, event.clientX - rect.left - element.clientLeft, event.clientY - rect.top - element.clientTop);
    }
    sync(element, state);
  });

  document.addEventListener('focusin', event => {
    const element = buttonFrom(event);
    if (!element || isDisabled(element) || !element.matches(':focus-visible')) return;

    const state = stateFor(element);
    state.focused = true;
    if (!state.hovering) place(element, element.clientWidth / 2, element.clientHeight / 2);
    sync(element, state);
  });

  document.addEventListener('focusout', event => {
    const element = buttonFrom(event);
    const state = element && states.get(element);
    if (!state) return;
    state.focused = false;
    sync(element, state);
  });

  const cleanupDisabled = subtree => {
    if (!(subtree instanceof Element)) return;
    if (subtree.matches(SELECTOR) && isDisabled(subtree)) clearState(subtree);
    subtree.querySelectorAll(SELECTOR).forEach(element => {
      if (isDisabled(element)) clearState(element);
    });
  };

  const observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'attributes') {
        cleanupDisabled(record.target);
      } else {
        for (const removed of record.removedNodes) {
          if (!(removed instanceof Element)) continue;
          for (const element of managed) {
            if (element === removed || removed.contains(element)) dispose(element);
          }
        }
      }
    }
  });
  observer.observe(document.body, {
    attributes: true,
    attributeFilter: ['disabled', 'aria-disabled'],
    childList: true,
    subtree: true
  });
})();
