// Service worker — mostly passthrough, keeps extension alive
chrome.runtime.onInstalled.addListener(() => {
  console.log("🥚 Easter Egg Hunt installed! Happy Easter~!");
});
