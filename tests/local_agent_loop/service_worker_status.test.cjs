const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

const workerPath = path.resolve(__dirname, '../../browser_extension/chatgpt_local_agent_loop/service_worker.js');
const source = fs.readFileSync(workerPath, 'utf8');

function makeHarness(storageSeed) {
  let storage = JSON.parse(JSON.stringify(storageSeed));
  let listener = null;
  const chrome = {
    storage: { local: {
      async get(keys) { const out = {}; for (const k of keys) if (Object.prototype.hasOwnProperty.call(storage, k)) out[k] = storage[k]; return out; },
      async set(patch) { Object.assign(storage, patch); },
      async setAccessLevel() {}
    }},
    runtime: {
      onInstalled: { addListener() {} },
      onStartup: { addListener() {} },
      onMessage: { addListener(fn) { listener = fn; } }
    },
    tabs: { async get() { throw new Error('tabs.get not expected'); }, async sendMessage() {} }
  };
  const context = { chrome, fetch: async () => ({ ok: true, status: 200, async text() { return JSON.stringify({ok:true}); } }), AbortController, setTimeout, clearTimeout, console, Date };
  vm.createContext(context);
  vm.runInContext(source, context, {filename: workerPath});
  assert(listener, 'service worker message listener was not registered');
  async function send(message, sender={tab:{id:77}}) {
    return await new Promise((resolve, reject) => {
      const returned = listener(message, sender, response => {
        try {
          if (!response || response.status !== 'OK') reject(new Error(response?.error || 'message failed'));
          else resolve(response.value);
        } catch (e) { reject(e); }
      });
      assert.strictEqual(returned, true);
    });
  }
  return {send};
}

function seed(entries) {
  return { port:18765, enabledTabId:77, automationEnabled:true, executedResults:{}, requestJournal:entries };
}

(async () => {
  {
    const h = makeHarness(seed({done:{requestId:'done', state:'SENT', tabId:77, updatedAt:300}}));
    const s = await h.send({type:'GET_STATUS'});
    assert.strictEqual(s.activeRequest, null, 'terminal SENT must not appear as activeRequest');
  }
  {
    const h = makeHarness(seed({done:{requestId:'done', state:'SENT', tabId:77, updatedAt:400}, run:{requestId:'run', state:'EXECUTING', tabId:77, updatedAt:300, mode:'direct'}}));
    const s = await h.send({type:'GET_STATUS'});
    assert.strictEqual(s.activeRequest.requestId, 'run', 'EXECUTING request must remain active even when newer SENT exists');
  }
  {
    const h = makeHarness(seed({stalled:{requestId:'stalled', state:'STALLED', tabId:77, updatedAt:300, error:'X'}}));
    const s = await h.send({type:'GET_STATUS'});
    assert.strictEqual(s.activeRequest.requestId, 'stalled', 'unreported STALLED request must remain visible');
  }
  {
    const h = makeHarness(seed({reported:{requestId:'reported', state:'STALLED', tabId:77, updatedAt:300, error:'X', reportedAt:301}}));
    const s = await h.send({type:'GET_STATUS'});
    assert.strictEqual(s.activeRequest, null, 'reported STALLED request must not remain active');
  }
  console.log('LOCAL_AGENT_LOOP_STATUS_TESTS=4/4 PASS');
})().catch(e => { console.error(e.stack || e); process.exit(1); });
