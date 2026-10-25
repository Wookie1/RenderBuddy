// RenderBuddy's tab in STEVE: shows whether the connector on the Mac is on, and what it's doing.
let next = 0;
const waiting = new Map();
const allowed = {};
const busyButtons = new Set();
let runButton = {};
let lastState = null;

function call(method, params = {}) {
  const id = ++next;
  window.parent.postMessage({ steve: 1, id, method, params }, '*');
  return new Promise((resolve, reject) => waiting.set(id, { resolve, reject }));
}

const $ = (id) => document.getElementById(id);

function applyTheme(theme) {
  document.body.classList.toggle('dark', theme === 'dark');
}

function showStatus(data) {
  const state = (data && data.state) || 'off';
  lastState = state;
  $('dot').className = 'dot ' + state;
  $('off-help').hidden = state !== 'off';
  $('progress').hidden = state !== 'rendering';
  if (state === 'idle') {
    $('state').textContent = 'On and ready';
    const minutes = data.idle_quit_minutes;
    $('detail').textContent = minutes ? `Turns itself off after ${minutes} minutes with no jobs.` : 'Stays on until you turn it off.';
  } else if (state === 'rendering') {
    $('state').textContent = 'Rendering';
    $('bar').style.width = Math.round((data.progress || 0) * 100) + '%';
    $('detail').textContent = data.message || '';
  } else {
    $('state').textContent = 'Off';
    $('detail').textContent = data && data.why ? `It turned off: ${data.why}.` : '';
  }
  updateButtons();
}

function updateButtons() {
  const on = lastState === 'idle' || lastState === 'rendering';
  $('check').disabled = !on || !allowed.blender_info || busyButtons.has('check');
}

function showRun(run) {
  const button = runButton[run.id];
  if (run.state === 'awaiting_confirmation') {
    $('message').textContent = 'Waiting for you to confirm in STEVE.';
  } else if (run.message || run.error) {
    $('message').textContent = run.error ? (run.error.message || String(run.error)) : run.message;
  }
  if (run.state === 'succeeded' && run.result && run.result.blender) {
    const r = run.result;
    $('message').textContent = `Blender ${r.blender.version || r.blender}` + (r.gpus ? `, ${r.gpus.length} GPU device(s)` : '');
  }
  if (['succeeded', 'failed', 'rejected', 'cancelled', 'expired'].includes(run.state) && button) {
    busyButtons.delete(button);
    delete runButton[run.id];
    updateButtons();
  }
}

async function start(button, action) {
  busyButtons.add(button);
  updateButtons();
  try {
    const run = await call('run', { action, inputs: {}, note: 'From the RenderBuddy tab' });
    runButton[run.id] = button;
    showRun(run);
  } catch (error) {
    busyButtons.delete(button);
    $('message').textContent = error.message || String(error);
    updateButtons();
  }
}

$('check').addEventListener('click', () => start('check', 'blender_info'));

window.addEventListener('message', (event) => {
  const message = event.data;
  if (event.source !== window.parent || !message || message.steve !== 1) return;
  if (message.id !== undefined && waiting.has(message.id)) {
    const { resolve, reject } = waiting.get(message.id);
    waiting.delete(message.id);
    message.error ? reject(message.error) : resolve(message.result);
  } else if (message.event === 'event' && message.name === 'status') {
    showStatus(message.data);
  } else if (message.event === 'run') {
    showRun(message.run);
  } else if (message.event === 'context') {
    applyTheme(message.context.theme);
  }
});

(async () => {
  const context = await call('context');
  applyTheme(context.theme);
  const events = await call('events');
  const status = (events || []).find((e) => e.name === 'status');
  // If STEVE says the connector isn't connected, it's off whatever its last event said.
  const connected = context.connector && context.connector.connected;
  showStatus(connected === false ? { state: 'off' } : status && status.data);
  for (const action of await call('actions')) {
    allowed[action.name] = action.allowed;
  }
  updateButtons();
})();
