const { chromium } = require('playwright');
// Browser end-to-end test of the car screen (optional: needs Node.js + `npm i playwright`).
// Start the backend first on a fresh database, then:  node tests/e2e_car.js [screenshot_folder]
// Plays: find parking -> choose -> drive -> enter -> park -> leave, and checks that no frequent place leaves the car.
const BASE = process.env.CAR_URL || 'http://localhost:8000';
const SP = process.argv[2] || '.';
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport:{ width:1400, height:1500 }, deviceScaleFactor:1 });
  const reqs = []; p.on('request', r => { if (r.url().includes('/api/')) reqs.push({ m:r.method(), u:r.url(), b:r.postData()||'' }); });
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  await p.goto(BASE + '/car');
  await p.selectOption('#speed', '180');
  const top = async () => p.$$eval('#offers .offer h3', hs => hs.map(h => h.innerText.replace(/\s+/g,' ')));
  const ask = async (label) => { await p.click('#btnRequest'); await p.waitForSelector('#offers .offer'); console.log(label, JSON.stringify(await top())); };

  // routine effect: same trip, different day/time, and Guest (no places)
  await p.selectOption('#profile','Amine (example)'); await p.selectOption('#day','2'); await p.fill('#time','16:00'); await ask('Amine Tue 16:00 (school pickup):');
  await p.screenshot({ path: SP+'/shots/offers.png', fullPage:false });
  await p.fill('#time','10:00'); await p.dispatchEvent('#time','change'); console.log('Amine Tue 10:00 (work):', JSON.stringify(await top()));
  await p.selectOption('#day','6'); await p.fill('#time','10:00'); await p.dispatchEvent('#time','change'); console.log('Amine Sat 10:00 (cafe):', JSON.stringify(await top()));
  await p.selectOption('#profile','Guest (no places)'); console.log('Guest:', JSON.stringify(await top()));
  // full trip as Amine on Tuesday 16:00
  await p.selectOption('#profile','Amine (example)'); await p.selectOption('#day','2'); await p.fill('#time','16:00'); await p.dispatchEvent('#time','change');
  await ask('Trip ranking:');
  const lot = (await p.$eval('#offers .offer.first button', x => x.dataset.lot)); console.log('choosing', lot);
  await p.click('#offers .offer.first button');
  await p.waitForFunction(() => document.querySelector('#steps .step.on')?.innerText === 'Parked', null, { timeout:60000 });
  await p.screenshot({ path: SP+'/shots/parked.png' });
  const ov = await (await fetch(BASE + '/api/admin/overview?lot_id='+lot, { headers:{'X-Admin-Key':'dev-admin-key'} })).json();
  console.log('while parked ->', JSON.stringify(ov.sessions.map(s => [s.ticket_id.slice(0,18), s.status, s.space])), 'spaces', ov.spaces.map(s=>s.code+':'+s.status).join(' '));
  await p.click('#btnLeave');
  await p.waitForFunction(() => document.querySelector('#steps .step.on')?.innerText === 'Done', null, { timeout:60000 });
  await p.screenshot({ path: SP+'/shots/done.png' });
  const ov2 = await (await fetch(BASE + '/api/admin/overview?lot_id='+lot, { headers:{'X-Admin-Key':'dev-admin-key'} })).json();
  console.log('after exit ->', 'active sessions', ov2.sessions.length, 'recent', JSON.stringify(ov2.recent_sessions.map(s=>[s.status,s.closed_by])), 'alerts', ov2.alerts.length);
  // privacy: no request may contain any routine-place coordinate
  const places = ['36.810','10.190','36.799','10.171','36.806','10.182','36.795','10.181'];
  const leaks = reqs.filter(r => places.some(c => (r.u+r.b).includes(c)));
  console.log('requests made:', reqs.length, ' requests containing a routine-place coordinate:', leaks.length);
  const ok = leaks.length === 0 && ov.sessions.length === 1 && ov2.sessions.length === 0 && ov2.recent_sessions[0].status === 'EXITED';
  console.log(ok ? 'E2E PASSED' : 'E2E FAILED'); process.exitCode = ok ? 0 : 1;
  console.log('sent to server (non-polling):', reqs.filter(r=>r.m==='POST' && /candidates|reserve|cancel/.test(r.u)).map(r=>r.u.split('/api/')[1]+' '+r.b).join(' | '));
  await b.close();
})();
