const countEl = document.getElementById("count");
const spawnBtn = document.getElementById("spawnBtn");
const resetBtn = document.getElementById("resetBtn");

// Get current egg count from content script
function refreshCount() {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]?.id) return;
    chrome.tabs.sendMessage(tabs[0].id, { type: "getCount" }, (res) => {
      if (chrome.runtime.lastError) {
        // Content script not loaded on this page — read from storage
        chrome.storage.local.get("easterEggCount", (data) => {
          countEl.textContent = data.easterEggCount || 0;
        });
        return;
      }
      countEl.textContent = res?.count ?? 0;
    });
  });
}

spawnBtn.addEventListener("click", () => {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]?.id) return;
    chrome.tabs.sendMessage(tabs[0].id, { type: "spawnNow" });
    spawnBtn.textContent = "🐣 Spawned!";
    setTimeout(() => { spawnBtn.textContent = "🥚 Spawn Egg Now"; }, 1200);
  });
});

resetBtn.addEventListener("click", () => {
  if (!confirm("Reset your egg count to 0?")) return;
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]?.id) return;
    chrome.tabs.sendMessage(tabs[0].id, { type: "resetCount" }, () => {
      countEl.textContent = "0";
    });
  });
  chrome.storage.local.set({ easterEggCount: 0 });
  countEl.textContent = "0";
});

refreshCount();
