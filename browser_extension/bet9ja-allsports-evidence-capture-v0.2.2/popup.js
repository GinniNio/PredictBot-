/**
 * Popup controller: injects snapshot.js + content.js into the active
 * tab, runs captureSnapshot(), and downloads the result as a timestamped
 * JSON file -- mirrors the download pattern the existing
 * browser_extension/bet9ja_capture/ extension already uses (chrome.
 * downloads, no server upload, the operator attaches the file here
 * themselves).
 */

const button = document.getElementById("capture-button");
const status = document.getElementById("status");

function setStatus(text) {
  status.textContent = text;
}

button.addEventListener("click", async () => {
  button.disabled = true;
  setStatus("Capturing...");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      throw new Error("No active tab found.");
    }

    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["snapshot.js", "content.js"],
    });

    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => captureSnapshot(),
    });

    const json = JSON.stringify(result, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const stamp = result.captured_at_utc.replace(/[:.]/g, "-");
    await chrome.downloads.download({
      url,
      filename: `bet9ja-allsports-raw-snapshot-${stamp}.json`,
      saveAs: false,
    });

    setStatus(
      `Saved. ${result.html_length.toLocaleString()} characters of page HTML from:\n${result.source_url}`
    );
  } catch (err) {
    setStatus(`Failed: ${err && err.message ? err.message : String(err)}`);
  } finally {
    button.disabled = false;
  }
});

/**
 * Stage 3 -- sport walker. `catalogue_parser.js` is loaded directly by
 * popup.html (a normal extension page, no CSP issue) so its
 * CONFIRMED_SPORTS list is available here to populate the dropdown
 * without needing to inject anything into the target tab first.
 */
const sportSelect = document.getElementById("sport-select");
const maxGroupsInput = document.getElementById("max-groups");
const maxCompetitionsInput = document.getElementById("max-competitions");
const walkButton = document.getElementById("walk-button");
const walkStatus = document.getElementById("walk-status");

function setWalkStatus(text) {
  walkStatus.textContent = text;
}

(function populateSportDropdown() {
  const sports = (window.Bet9jaAllSportsCatalogueParser && window.Bet9jaAllSportsCatalogueParser.CONFIRMED_SPORTS) || [];
  const sorted = [...sports].sort((a, b) => a.label.localeCompare(b.label));
  for (const sport of sorted) {
    const option = document.createElement("option");
    option.value = JSON.stringify({ sportId: sport.sport_id, sportSlug: sport.sport_slug });
    option.textContent = `${sport.label} (id ${sport.sport_id})`;
    sportSelect.appendChild(option);
  }
  const iceHockey = sorted.findIndex((s) => s.label === "Ice Hockey");
  if (iceHockey >= 0) sportSelect.selectedIndex = iceHockey;
})();

walkButton.addEventListener("click", async () => {
  walkButton.disabled = true;
  setWalkStatus("Walking sport -- this can take a while, one competition at a time...");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      throw new Error("No active tab found.");
    }

    const { sportId, sportSlug } = JSON.parse(sportSelect.value);
    // `chrome.scripting.executeScript` requires every value in `args` to
    // be structured-clone serializable. `undefined` is rejected, while
    // `null` is valid and the walker already treats it as no configured cap.
    const maxGroups = maxGroupsInput.value ? Number(maxGroupsInput.value) : null;
    const maxCompetitionsPerGroup = maxCompetitionsInput.value ? Number(maxCompetitionsInput.value) : null;

    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["catalogue_parser.js", "fixture_parser.js", "sport_walker.js"],
    });

    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (sportId, sportSlug, maxGroups, maxCompetitionsPerGroup) =>
        window.Bet9jaAllSportsWalker.walkSport(document, {
          sportId,
          sportSlug,
          maxGroups,
          maxCompetitionsPerGroup,
          now: () => new Date().toISOString(),
        }),
      args: [sportId, sportSlug, maxGroups, maxCompetitionsPerGroup],
    });

    // The running PredictBot app takes the capture directly; the download
    // is only the fallback when the app is not running or is read-only.
    const sent = await chrome.runtime.sendMessage({ type: "BET9JA_WALK_DONE", run_id: "manual", sport: sportSlug,
      started: result.capture_started_at_utc, result });
    let handoff;
    if (sent && sent.uploaded) {
      handoff = `Sent to the app (${sent.message}).`;
    } else {
      const json = JSON.stringify(result, null, 2);
      const blob = new Blob([json], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      await chrome.downloads.download({
        url,
        filename: `bet9ja-allsports-walk-${sportSlug}-${stamp}.json`,
        saveAs: false,
      });
      handoff = `Not sent to the app (${sent && sent.message ? sent.message : "no reply"}); downloaded instead.`;
    }

    setWalkStatus(
      `${result.capture_status}. ${result.sport}: ${result.groups_seen}/${result.groups_discovered} groups, ` +
        `${result.competitions_seen} seen, ${result.competitions_attempted} attempted, ` +
        `${result.competitions_successful} successful, ${result.competitions_validated} validated, ` +
        `${result.competitions_failed} failed. ${handoff}`
    );
  } catch (err) {
    setWalkStatus(`Failed: ${err && err.message ? err.message : String(err)}`);
  } finally {
    walkButton.disabled = false;
  }
});


/**
 * Schedule (0.4.0): settings live in the background worker's storage; this
 * section only edits them and shows the last run.
 */
const $ = (id) => document.getElementById(id);
const scheduleSend = (message) => chrome.runtime.sendMessage(message);

function describeState(state) {
  if (!state) return "No scheduled run yet.";
  const lines = [`${state.trigger === "manual" ? "Background" : "Scheduled"} run ${state.status} (started ${state.started_at_utc.slice(11, 16)} UTC)` +
    (state.message ? `: ${state.message}` : "")];
  for (const s of state.sports || []) {
    lines.push(`  ${s.sport}: ${s.status || "walking..."}${s.detail ? " - " + s.detail : ""}` +
      (s.uploaded ? " - sent to app" : s.stored ? " - app did not take it: " + s.upload : s.upload ? " - " + s.upload : ""));
  }
  return lines.join("\n");
}

async function refreshSchedule() {
  const r = await scheduleSend({ type: "GET_BET9JA_SCHEDULE" });
  if (!r || !r.ok) return;
  const sc = r.schedule;
  $("auto-enabled").checked = !!sc.enabled;
  $("auto-hours").value = sc.every_hours;
  $("auto-start").value = sc.start_url;
  const box = $("auto-sports");
  if (!box.childElementCount) {
    for (const slug of r.sports) {
      const label = document.createElement("label");
      label.style.margin = "2px 0";
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.value = slug; cb.style.width = "auto";
      label.appendChild(cb);
      label.appendChild(document.createTextNode(" " + slug.replace(/_/g, " ")));
      box.appendChild(label);
    }
  }
  for (const cb of box.querySelectorAll("input")) cb.checked = (sc.sports || []).includes(cb.value);
  $("schedule-status").textContent = (sc.enabled ? "Schedule on. " : "Schedule off. ") + describeState(r.state);
}

$("save-schedule").addEventListener("click", async () => {
  const sports = [...$("auto-sports").querySelectorAll("input:checked")].map((cb) => cb.value);
  await scheduleSend({ type: "SET_BET9JA_SCHEDULE", schedule: { enabled: $("auto-enabled").checked,
    every_hours: Number($("auto-hours").value) || 4, start_url: $("auto-start").value.trim() || "https://sports.bet9ja.com/", sports } });
  await refreshSchedule();
});
$("run-now").addEventListener("click", async () => {
  await scheduleSend({ type: "RUN_BET9JA_WALK_NOW" });
  $("schedule-status").textContent = "Starting a background run of every selected sport...";
  setTimeout(refreshSchedule, 1500);
});
refreshSchedule();
setInterval(refreshSchedule, 3000);
