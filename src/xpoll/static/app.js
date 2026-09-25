(() => {
  "use strict";

  const TURNSTILE_SRC =
    "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=xpollTurnstileReady";
  const REFRESH_MS = 10000;
  const BASE = document.documentElement.dataset.base || "";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  // --- Turnstile ------------------------------------------------------------
  const widgets = new Map(); // container -> {id, token, onChange}

  function registerTurnstile(container, onChange) {
    widgets.set(container, { id: null, token: null, onChange });
  }

  function turnstileToken(container) {
    const w = widgets.get(container);
    return w ? w.token : null;
  }

  function resetTurnstile(container) {
    const w = widgets.get(container);
    if (!w) return;
    w.token = null;
    w.onChange();
    if (w.id !== null && window.turnstile) window.turnstile.reset(w.id);
  }

  window.xpollTurnstileReady = () => {
    widgets.forEach((w, container) => {
      const setToken = (token) => {
        w.token = token;
        w.onChange();
      };
      w.id = window.turnstile.render(container, {
        sitekey: container.dataset.sitekey,
        action: container.dataset.action,
        theme: container.dataset.theme || "auto",
        callback: setToken,
        "expired-callback": () => setToken(null),
        "error-callback": () => setToken(null),
      });
    });
  };

  function loadTurnstile() {
    if (widgets.size === 0) return;
    const script = document.createElement("script");
    script.src = TURNSTILE_SRC;
    script.async = true;
    script.defer = true;
    document.head.appendChild(script);
  }

  // --- helpers --------------------------------------------------------------
  async function postJSON(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      /* non-JSON error page */
    }
    return { status: response.status, data };
  }

  function errorMessage(result) {
    if (result.status === 429) return "Too many attempts from your network. Please wait a few minutes.";
    return (result.data && result.data.detail) || "Something went wrong. Please try again.";
  }

  function localizeTimes(root = document) {
    $$("time.localtime", root).forEach((el) => {
      const date = new Date(el.getAttribute("datetime"));
      if (!Number.isNaN(date.getTime())) {
        el.textContent = date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
      }
    });
  }

  // --- results --------------------------------------------------------------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderResults(section, data) {
    const list = $("#result-list", section);
    const fragment = document.createDocumentFragment();
    data.results.forEach((row) => {
      const rank = 1 + data.results.filter((r) => r.respondents > row.respondents).length;
      const li = el("li", "result");
      if (row.respondents === 0) li.classList.add("is-zero");
      else if (rank === 1) li.classList.add("is-leader");
      const name = el("a", "result-name", row.name);
      if (typeof row.url === "string" && row.url.startsWith("https://")) name.href = row.url;
      name.target = "_blank";
      name.rel = "noopener noreferrer";
      const bar = el("progress", "result-bar");
      bar.max = 100;
      bar.value = row.percentage;
      bar.setAttribute("aria-hidden", "true");
      li.append(
        el("span", "result-rank", String(rank)),
        name,
        el("span", "result-pct", `${Number(row.percentage).toFixed(1)}%`),
        bar,
        el("span", "result-count", `${row.respondents} ${row.respondents === 1 ? "vote" : "votes"}`),
      );
      fragment.appendChild(li);
    });
    list.replaceChildren(fragment);
    $("#total-ballots", section).textContent = String(data.total_ballots);
    $("#ballots-label", section).textContent = data.total_ballots === 1 ? "ballot" : "ballots";
    $("#refreshed-at", section).textContent = new Date().toLocaleTimeString();
  }

  async function refreshResults(section) {
    try {
      const response = await fetch(`${BASE}/api/results`, { cache: "no-cache", credentials: "same-origin" });
      if (response.ok) renderResults(section, await response.json());
    } catch (_) {
      /* keep showing the last good data */
    }
  }

  function startLiveResults() {
    const section = $("#results");
    if (!section || section.dataset.live !== "true") return;
    let timer = null;
    const schedule = () => {
      clearInterval(timer);
      timer = null;
      if (document.visibilityState === "visible" && !section.closest("[hidden]")) {
        timer = setInterval(() => refreshResults(section), REFRESH_MS);
      }
    };
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") refreshResults(section);
      schedule();
    });
    section.addEventListener("xpoll:shown", () => {
      refreshResults(section);
      schedule();
    });
    const refreshed = $("#refreshed-at", section);
    if (refreshed) refreshed.textContent = new Date().toLocaleTimeString();
    schedule();
  }

  function showResults() {
    const slot = $("#results-slot");
    if (!slot || slot.dataset.visible !== "true") return;
    slot.hidden = false;
    const section = $("#results", slot);
    if (section) section.dispatchEvent(new Event("xpoll:shown"));
  }

  // --- ballot ---------------------------------------------------------------
  function setupBallot() {
    const form = $("#ballot");
    if (!form) return;
    const min = Number(form.dataset.min);
    const max = Number(form.dataset.max);
    const boxes = $$('input[name="option"]', form);
    const counter = $("#selected-count", form);
    const hint = $("#bar-hint", form);
    const slots = $$(".slot", form);
    const submit = $("#ballot-submit", form);
    const errorBox = $("#ballot-error", form);
    const widget = $("[data-turnstile]", form);
    let submitting = false;

    const hintFor = (selected, hasToken) => {
      if (submitting) return "Sending your vote…";
      if (selected === 0) return min > 1 ? `Pick ${min} to ${max}` : `Pick up to ${max}`;
      if (selected < min) return `Pick ${min - selected} more`;
      if (!hasToken) return "Complete the bot check above";
      return selected >= max ? "All picks used · ready" : "Ready to vote";
    };

    const update = () => {
      const selected = boxes.filter((b) => b.checked).length;
      const hasToken = Boolean(turnstileToken(widget));
      counter.textContent = String(selected);
      slots.forEach((slot, i) => slot.classList.toggle("is-filled", i < selected));
      boxes.forEach((b) => {
        b.disabled = !b.checked && selected >= max;
        const card = b.closest(".option");
        card.classList.toggle("is-selected", b.checked);
        card.classList.toggle("is-disabled", b.disabled);
      });
      hint.textContent = hintFor(selected, hasToken);
      submit.disabled = submitting || selected < min || !hasToken;
    };

    registerTurnstile(widget, update);
    form.addEventListener("change", update);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submit.disabled) return;
      submitting = true;
      errorBox.textContent = "";
      update();
      const optionIds = boxes.filter((b) => b.checked).map((b) => Number(b.value));
      let result;
      try {
        result = await postJSON(`${BASE}/api/ballots`, {
          option_ids: optionIds,
          turnstile_token: turnstileToken(widget),
        });
      } catch (_) {
        result = { status: 0, data: { detail: "Network error. Check your connection and try again." } };
      }
      submitting = false;
      if (result.status === 201 || result.status === 409) {
        form.hidden = true;
        const thanks = $("#thanks");
        if (result.status === 409) {
          $("h2", thanks).textContent = "You have already voted from this browser.";
          $("p", thanks).textContent = "Each browser gets one vote. Results update live below.";
        }
        thanks.hidden = false;
        thanks.focus();
        showResults();
        return;
      }
      errorBox.textContent = errorMessage(result);
      resetTurnstile(widget);
    });
    update();
  }

  // --- suggestions ----------------------------------------------------------
  function setupSuggestions() {
    const form = $("#suggestion");
    if (!form) return;
    const submit = $("#suggest-submit", form);
    const errorBox = $("#suggest-error", form);
    const successBox = $("#suggest-success", form);
    const widget = $("[data-turnstile]", form);
    const name = $("#suggest-name", form);
    let submitting = false;

    const update = () => {
      submit.disabled = submitting || name.value.trim().length < 2 || !turnstileToken(widget);
    };
    registerTurnstile(widget, update);
    form.addEventListener("input", update);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submit.disabled) return;
      submitting = true;
      errorBox.textContent = "";
      successBox.textContent = "";
      update();
      let result;
      try {
        result = await postJSON(`${BASE}/api/suggestions`, {
          name: name.value,
          url: $("#suggest-url", form).value || null,
          notes: $("#suggest-notes", form).value,
          turnstile_token: turnstileToken(widget),
        });
      } catch (_) {
        result = { status: 0, data: { detail: "Network error. Check your connection and try again." } };
      }
      submitting = false;
      if (result.status === 201) {
        form.reset();
        successBox.textContent = "Thanks! Your suggestion was sent to the organizer.";
      } else {
        errorBox.textContent = errorMessage(result);
      }
      resetTurnstile(widget);
    });
    update();
  }

  document.addEventListener("DOMContentLoaded", () => {
    localizeTimes();
    setupBallot();
    setupSuggestions();
    startLiveResults();
    loadTurnstile();
  });
})();
