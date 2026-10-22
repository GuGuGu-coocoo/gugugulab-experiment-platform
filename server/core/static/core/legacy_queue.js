// Trusted old-origin compatibility page. Never load uploaded experiment code.
import {GEC} from '/legacy-data/sdk.js';

const trusted = JSON.parse(document.getElementById('legacy-config').textContent);
const status = document.getElementById('legacy-status');
const count = document.getElementById('legacy-count');
const inspect = document.getElementById('legacy-inspect');
const upload = document.getElementById('legacy-upload');
let client;
const message = (key, error = false) => {
  status.textContent = trusted.labels[key];
  status.className = error ? 'error' : '';
};

function safeTarget(config) {
  if (config?.api_url !== trusted.api_url || config?.instance_id !== trusted.instance_id) {
    throw new Error('legacy_binding_unavailable');
  }
}

async function open() {
  if (client) return;
  const candidate = new GEC({config_version:'1',protocol_version:'gep/1',sdk_version:'0.1.0',
    purpose:'synthetic',api_url:trusted.api_url,instance_id:trusted.instance_id});
  const request = candidate.request.bind(candidate);
  candidate.request = (config, ...args) => {
    // A corrupt or stale local row cannot redirect data to a different server.
    safeTarget(config);
    return request(config, ...args);
  };
  try {
    await candidate.prepare();
    clearInterval(candidate.timer); // Explicit, one bounded round per click.
    client = candidate;
  } catch (error) {
    candidate.close();
    throw error;
  }
}

async function summarize() {
  const rows = await client.all();
  const sessions = rows.filter(row => row.kind === 'session');
  const pending = sessions.reduce((total, row) => total + (row.pending?.length || 0), 0);
  count.textContent = trusted.labels.count + ': ' + sessions.length + ' / ' + pending;
  upload.disabled = !sessions.some(row => row.pending?.length || (row.completion && !row.complete_ack));
  return {retained:rows.some(row => ['session','admission','local'].includes(row.kind)),
    pending:pending > 0 || sessions.some(row => row.completion && !row.complete_ack)};
}

async function check(automatic = false) {
  inspect.disabled = true;
  try {
    await open();
    const summary = await summarize();
    message(summary.retained ? 'found' : 'empty');
    if (automatic && !summary.retained && trusted.forward) location.replace(trusted.forward);
  } catch (error) {
    message(error.message === 'writer_busy' ? 'busy' : 'failed', true);
  } finally { inspect.disabled = false; }
}

inspect.addEventListener('click', () => check());
upload.addEventListener('click', async () => {
  upload.disabled = true;
  inspect.disabled = true;
  try {
    await client.flush(true); // Retains backoff/paused state; existing ACK checks and cleanup only.
    const summary = await summarize();
    message(summary.pending ? 'waiting' : 'done');
  } catch { message('failed', true); }
  finally {
    try { await summarize(); } catch { message('failed', true); }
    inspect.disabled = false;
  }
});
addEventListener('pagehide', () => client?.close());
if (trusted.forward) check(true);
