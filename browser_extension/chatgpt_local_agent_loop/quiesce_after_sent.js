const TARGET = "projecthub-arm-one-shot-auto-disarm-after-result-20260912-896";
const started = Date.now();
async function tick() {
  try {
    const s = await chrome.storage.local.get(["requestJournal","automationEnabled","enabledTabId"]);
    const entry = (s.requestJournal || {})[TARGET];
    if (entry && entry.state === "SENT") {
      const r = await chrome.runtime.sendMessage({type:"DISABLE"});
      await chrome.storage.local.set({lastAutoQuiesce:{requestId:TARGET,at:Date.now(),result:r || null}});
      document.body.textContent = "Local Agent automation disabled after result delivery.";
      setTimeout(() => window.close(), 750);
      return;
    }
    if (Date.now() - started > 120000) {
      await chrome.storage.local.set({lastAutoQuiesce:{requestId:TARGET,at:Date.now(),timeout:true}});
      document.body.textContent = "Quiesce timeout; automation left unchanged.";
      return;
    }
  } catch (e) {
    document.body.textContent = "Quiesce watcher error: " + String(e && e.message || e);
  }
  setTimeout(tick, 250);
}
tick();