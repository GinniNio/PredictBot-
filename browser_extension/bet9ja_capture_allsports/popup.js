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
