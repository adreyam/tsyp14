const { chromium } = require('playwright');
// Browser test of the duplicate-entry demo (optional: Node.js + `npm i playwright`, backend running on a fresh database).
//   node tests/e2e_duplicate.js [screenshot_folder]
const SP = process.argv[2] || '.', BASE = process.env.CAR_URL || 'http://localhost:8000';
const adm = async (path) => (await fetch(BASE+'/api/admin/'+path, { headers:{'X-Admin-Key':'dev-admin-key'} })).json();
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport:{ width:1450, height:1100 } });
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  p.on('console', m => { if (m.type()==='error') console.log('CONSOLE', m.text()); });
  await p.goto(BASE+'/duplicate'); await p.waitForTimeout(1500);
  await p.screenshot({ path: SP+'/dup_0_start.png' });
  // ---- scenario A, stepwise to capture the refusal
  await p.selectOption('#scenario','A');
  await p.click('#btnS1'); await p.waitForFunction(() => document.querySelector('#say b.t')?.innerText.includes('parked in P02'), null, {timeout:60000});
  await p.click('#btnS2');
  await p.waitForFunction(() => document.querySelector('#say b.t')?.innerText.includes('REFUSES'), null, {timeout:60000});
  await p.screenshot({ path: SP+'/dup_A_refused.png' });
  await p.waitForFunction(() => document.querySelector('#say b.t')?.innerText.includes('What the system did'), null, {timeout:60000});
  let o2 = await adm('overview?lot_id=P02'), o1 = await adm('overview?lot_id=P01'), al = await adm('alerts');
  console.log('A: P02 session', o2.sessions.map(s=>s.status+'/'+s.space), '| P01 sessions', o1.sessions.length, '| P01 lane', o1.lanes[0].state, '| alerts', al.map(a=>a.type+':'+a.severity+':lot '+a.lot_id));
  await p.click('#aBlock'); await p.waitForTimeout(800);
  await p.click('#aRetryB'); await p.waitForFunction(() => document.querySelector('#say b.t')?.innerText.includes('Still refused'), null, {timeout:60000});
  al = await adm('alerts'); console.log('A after block+retry alerts:', al.map(a=>a.type));
  await p.click('#aExitA'); await p.waitForFunction(() => document.querySelector('#say b.t')?.innerText.includes('P02 session closed'), null, {timeout:60000});
  o2 = await adm('overview?lot_id=P02'); console.log('A end: P02 active', o2.sessions.length, 'recent', o2.recent_sessions.map(s=>s.status+'/'+s.closed_by));
  await p.screenshot({ path: SP+'/dup_A_end.png' });
  // ---- scenario B auto-play
  await p.selectOption('#scenario','B'); await p.click('#btnPlay');
  await p.waitForFunction(() => document.querySelector('#say b.t')?.innerText.includes('Scenario complete'), null, {timeout:150000});
  o2 = await adm('overview?lot_id=P02'); o1 = await adm('overview?lot_id=P01'); al = await adm('alerts');
  console.log('B end: P02 active', o2.sessions.length, 'recent', o2.recent_sessions.map(s=>s.status+'/'+s.closed_by), '| P01 sessions', o1.sessions.map(s=>s.status+'/'+s.space), '| open alerts', al.map(a=>a.type));
  await p.screenshot({ path: SP+'/dup_B_end.png' });
  // ---- scenario C: refused car waits, administrator approves a visitor RFID card
  await p.selectOption('#scenario','C'); await p.click('#btnPlay');
  await p.waitForFunction(() => document.querySelector('#say b.t')?.innerText.includes('Administrator approves'), null, {timeout:90000});
  await p.screenshot({ path: SP+'/dup_C_approved.png' });
  await p.waitForFunction(() => document.querySelector('#say b.t')?.innerText.includes('Scenario complete'), null, {timeout:200000});
  o2 = await adm('overview?lot_id=P02'); o1 = await adm('overview?lot_id=P01'); al = await adm('alerts');
  console.log('C end: P02', o2.sessions.map(s=>s.status+'/'+s.space), '| P01 active', o1.sessions.length, 'recent', o1.recent_sessions.map(s=>s.vehicle_id+'/'+s.status+'/'+s.closed_by), '| open alerts', al.map(a=>a.type+(a.message.includes('Visitor RFID card approved')?' (approval recorded)':'')), '| cards not available', o1.cards.filter(c=>c.status!=='AVAILABLE').length);
  await p.screenshot({ path: SP+'/dup_C_end.png' });
  console.log('DONE (check the printed states above)');
  await b.close();
})();
