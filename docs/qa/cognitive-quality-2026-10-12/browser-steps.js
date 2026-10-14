// Reference recipe for the documented cua_repl browser API.
// Start qa-runner.py api/worker, the existing docs/qa/serve_frontend.mjs --live,
// and trace-monitor.py. Their fixed paths are the exact session setup.
// Use cua_repl, not a separate browser automation runtime.
// Sign in through the frontend with the synthetic QA account in qa-runner.py.
// Set desktop viewport only to expose New chat, then reset it after testing.
// For each condition: configure the QA core or explicit state projection first,
// click New chat, send the same corpus.json common_probes in order, waiting
// for each terminal shadow trace. Repeat personality/no-character conditions 3x.
async function sendAndVerify(tab, monitor, text, tick) {
  await tab.playwright.getByRole('textbox', { name: 'Message Columbina' }).fill(text);
  await tab.playwright.getByRole('button', { name: 'Send', exact: true }).click();
  await tab.playwright.getByRole('button', { name: 'Stop', exact: true })
    .waitFor({ state: 'hidden', timeoutMs: 55000 });
  await tab.getAXState();
  await monitor.goto('http://127.0.0.1:58002/?turn=' + tick);
  return JSON.parse(await monitor.playwright.locator('pre').innerText());
}
// Plain-Luna control: QA worker _build_inputs omits the first system message
// only while /private/tmp/cognitive-quality-1012/visible_mode is no_character.
// Its shadow is not a characterless pipeline; never score it as that baseline.
// Calm/strained use application_baseline projections, with identical action
// and interpretation profiles. Changing the projection file between fresh
// conversations uses the app's existing capture-time baseline read.
