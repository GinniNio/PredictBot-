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

    const json = JSON.stringify(result, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    await chrome.downloads.download({
      url,
      filename: `bet9ja-allsports-walk-${sportSlug}-${stamp}.json`,
      saveAs: false,
    });

    setWalkStatus(
      `Saved ${result.capture_status}. ${result.sport}: ${result.groups_seen}/${result.groups_discovered} groups, ` +
        `${result.competitions_seen} seen, ${result.competitions_attempted} attempted, ` +
        `${result.competitions_successful} successful, ${result.competitions_validated} validated, ` +
        `${result.competitions_failed} failed. Please attach the downloaded JSON back.`
    );
  } catch (err) {
    setWalkStatus(`Failed: ${err && err.message ? err.message : String(err)}`);
  } finally {
    walkButton.disabled = false;
  }
});
