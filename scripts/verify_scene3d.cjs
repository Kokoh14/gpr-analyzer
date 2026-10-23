// Run against a disposable local test app. Never point this at a production job store.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
let activeBrowser;

(async () => {
  const out = process.env.QA_OUTPUT || 'tmp/scene3d-ui'; fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true,
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}),
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });
  activeBrowser = browser;
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 1 });
  const errors = [], failures = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('response', (r) => { if (r.url().startsWith('http://127.0.0.1') && r.status() >= 400) failures.push(`${r.status()} ${r.url()}`); });
  await page.route('**/static/survey3d.js', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: await response.text() + `\nwindow.__s3Qa = {
      data: () => state.data,
      geometryRows,
      info: () => ({triangles:renderer.info.render.triangles, geometries:renderer.info.memory.geometries, textures:renderer.info.memory.textures}),
      build: data => { state.data=data; build(data); },
      pick: (x,y) => pick({clientX:x,clientY:y},false), updateSlice
    };` });
  });
  await page.route('**/*', (route) => route.request().url().startsWith('http://127.0.0.1') || route.request().url().startsWith('data:') ? route.fallback() : route.abort());
  await page.goto(process.env.QA_URL || 'http://127.0.0.1:8766/');
  console.log('Creating synthetic grid through the user interface');
  await page.locator('#demo3dBtn').click();
  await page.waitForFunction(() => document.querySelector('#survey3dCard').dataset.loaded === 'true', null, { timeout: 180000 });
  assert.equal(await page.locator('.s3-line').count(), 8);
  assert.match(await page.locator('#s3Status').textContent(), /8 scan lines/);
  await page.locator('#s3Full').click();
  await page.waitForFunction(() => !!document.fullscreenElement);
  await page.locator('#s3Fit').click();
  await page.screenshot({ path: path.join(out, '01-fence-grey.png') });
  const before = await page.locator('#s3Stage canvas').screenshot();
  const rect = await page.locator('#s3Stage canvas').boundingBox();
  await page.mouse.move(rect.x + rect.width * 0.5, rect.y + rect.height * 0.5);
  await page.mouse.down(); await page.mouse.move(rect.x + rect.width * 0.65, rect.y + rect.height * 0.58, { steps: 12 }); await page.mouse.up();
  const after = await page.locator('#s3Stage canvas').screenshot();
  assert(!before.equals(after), 'Orbit must change the rendered scene');
  await page.locator('#s3None').click(); assert.equal(await page.locator('.s3-line input:checked').count(), 0);
  await page.locator('#s3All').click(); assert.equal(await page.locator('.s3-line input:checked').count(), 8);
  const load = page.waitForResponse((r) => r.url().includes('/scene3d?') && r.url().includes('track=envelope'));
  await page.locator('#s3Track').selectOption('envelope'); await load;
  await page.waitForFunction(() => document.querySelector('#s3Status').textContent.includes('ENVELOPE'));
  await page.locator('#s3Exag').selectOption('2');
  await page.locator('#s3Depth').fill('1.2'); await page.locator('#s3Depth').dispatchEvent('input');
  await page.waitForFunction(() => document.querySelector('#s3DepthValue').textContent === '1.20 m');
  await page.screenshot({ path: path.join(out, '02-fence-envelope.png') });
  await page.locator('#s3Curtains').uncheck(); await page.locator('#s3Top').click();
  await page.screenshot({ path: path.join(out, '03-depth-strips.png') });
  await page.locator('#s3Curtains').check(); await page.locator('#s3Fit').click();
  await page.locator('#s3Stage canvas').scrollIntoViewIfNeeded();
  const pickRect = await page.locator('#s3Stage canvas').boundingBox();
  await page.evaluate(() => { window.__scenePick = null; window.addEventListener('gpr:3d-pick', (e) => window.__scenePick = e.detail); });
  let picked = false;
  for (const [fx, fy] of [[.5,.5],[.4,.5],[.6,.5],[.5,.6],[.4,.6],[.6,.6],[.5,.4],[.4,.7],[.6,.7]]) {
    await page.mouse.click(pickRect.x + pickRect.width * fx, pickRect.y + pickRect.height * fy);
    if (await page.evaluate(() => !!window.__scenePick)) { picked = true; break; }
  }
  if (!picked) console.log(JSON.stringify({ errors, rect: pickRect, readout: await page.locator('#s3Readout').textContent(), centreElement: await page.evaluate(({x,y}) => document.elementFromPoint(x,y)?.outerHTML.slice(0,300), {x:pickRect.x+pickRect.width*.5,y:pickRect.y+pickRect.height*.5}) }));
  assert(picked, 'A visible curtain must be pickable');
  await page.waitForFunction(() => document.querySelectorAll('#panes .guide.vline').length > 0);
  const pngEvent = page.waitForEvent('download'); await page.locator('#s3Png').click();
  const png = await pngEvent; await png.saveAs(path.join(out, png.suggestedFilename()));
  const jsonEvent = page.waitForEvent('download'); await page.locator('#s3Json').click();
  const json = await jsonEvent; await json.saveAs(path.join(out, json.suggestedFilename()));
  const exported = JSON.parse(fs.readFileSync(path.join(out, json.suggestedFilename()), 'utf8'));
  assert.equal(exported.lines.length, 8); assert.equal(exported.frame, 'gps');
  await page.locator('#s3Full').click();
  const change = page.waitForResponse((r) => r.url().includes('/scene3d?frame=local'));
  await page.locator('#s3Frame').selectOption('local'); await change;
  await page.waitForFunction(() => document.querySelectorAll('.s3-line').length === 0);
  await page.locator('.s3-placement summary').click();
  const row = page.locator('#s3LayoutRows tr').first();
  for (const [i, value] of ['0','0','16','0'].entries()) await row.locator('input[type=number]').nth(i).fill(value);
  const saved = page.waitForResponse((r) => r.url().includes('/scene3d/layout') && r.request().method() === 'PUT');
  await page.locator('#s3LayoutForm button[type=submit]').click(); await saved;
  await page.waitForFunction(() => document.querySelector('#s3Status').textContent.includes('1 scan lines'));
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#survey3dCard').dataset.loaded === 'true');
  const local = page.waitForResponse((r) => r.url().includes('/scene3d?frame=local'));
  await page.locator('#s3Frame').selectOption('local'); await local;
  await page.waitForFunction(() => document.querySelector('#s3Status').textContent.includes('1 scan lines'));
  assert.match(await page.locator('#s3Datum').textContent(), /local E\/N/);
  assert.equal(await page.locator('#s3LayoutRows tr').first().locator('input[type=number]').nth(2).inputValue(), '16');
  // Regression: drafts must survive both a scale change and a scene/sidebar reload.
  const draft = page.locator('#s3LayoutRows tr').first().locator('input[type=number]').first();
  await page.locator('.s3-placement').evaluate(el => el.open = true);
  await draft.fill('12.34');
  await page.locator('#s3Exag').selectOption('4');
  assert.equal(await draft.inputValue(), '12.34');
  const gpsLoad = page.waitForResponse(r => r.url().includes('/scene3d?frame=gps'));
  await page.locator('#s3Frame').selectOption('gps'); await gpsLoad;
  await page.waitForFunction(() => window.__s3Qa.data()?.frame === 'gps');
  assert.equal(await draft.inputValue(), '12.34');
  // Regression: reprocess while the card is closed, including unchanged options/dimensions.
  const initial = await page.evaluate(() => window.__s3Qa.data());
  const baseUrl = new URL(process.env.QA_URL || 'http://127.0.0.1:8766/').origin;
  for (const velocity of [0.14, 0.14]) {
    const previousRevision = await page.evaluate(() => window.getGprSceneContext().job.lines[0].revision);
    await page.locator('#survey3dCard').evaluate(el => el.open = false);
    const response = await page.request.post(`${baseUrl}/api/jobs/${initial.job_id}/lines/L1/reprocess`, { data: { options: { velocity_m_per_ns: velocity } } });
    assert(response.ok(), await response.text());
    const updated = await response.json();
    assert.notEqual(updated.lines[0].revision, previousRevision);
    await page.evaluate(job => {
      Object.assign(window.getGprSceneContext().job, job);
      window.dispatchEvent(new CustomEvent('gpr:job', { detail: {job, line:'L1'} }));
    }, updated);
    const refresh = page.waitForResponse(r => r.url().includes('/scene3d?'));
    await page.locator('#survey3dCard').evaluate(el => el.open = true); await refresh;
    await page.waitForFunction(v => window.__s3Qa.data()?.lines.find(l => l.line_id === 'L1')?.velocity_m_per_ns === v, velocity);
  }
  // Bound every original sample's depth error, including a strongly nonlinear bistatic mapping.
  await page.evaluate(() => {
    for (const separation of [0, 0.4, 2]) {
      const ds = Array.from({length:160}, (_,i) => Math.sqrt((i * 0.0025 + separation/2) ** 2 - (separation/2) ** 2));
      const rows = window.__s3Qa.geometryRows(ds);
      const tolerance = Math.max(0.001, Math.min(0.005, (ds.at(-1)-ds[0])*0.001));
      for (let k=0;k<rows.length-1;k++) for (let i=rows[k];i<=rows[k+1];i++) {
        const estimate=ds[rows[k]]+(ds[rows[k+1]]-ds[rows[k]])*(i-rows[k])/(rows[k+1]-rows[k]);
        if (Math.abs(estimate-ds[i]) > tolerance + 1e-12) throw new Error('Depth mapping exceeds tolerance');
      }
      if (!separation && rows.length !== 2) throw new Error('Linear depth needs only boundary rows');
    }
  });
  const stress = structuredClone(initial);
  await page.locator('#s3Exag').selectOption('2');
  stress.lines = Array.from({length:40}, (_,i) => {
    const l=structuredClone(initial.lines[i%initial.lines.length]); l.line_id='SYNTHETIC_'+i;
    l.xy_m=l.xy_m.map(([x,n])=>[x,n+Math.floor(i/8)*2]); return l;
  });
  const buildMs = await page.evaluate(data => { const t=performance.now(); window.__s3Qa.build(data); return performance.now()-t; }, stress);
  await page.waitForFunction(() => window.__s3Qa.info().textures >= 40);
  const performanceResult = await page.evaluate(() => {
    const r=document.querySelector('#s3Stage canvas').getBoundingClientRect(), times=[], slices=[];
    for(let i=0;i<12;i++) { let t=performance.now(); window.__s3Qa.pick(r.x+r.width*(.35+i%4*.1),r.y+r.height*(.4+Math.floor(i/4)*.1)); times.push(performance.now()-t); }
    for(let i=0;i<12;i++) { let t=performance.now(); window.__s3Qa.updateSlice(); slices.push(performance.now()-t); }
    times.sort((a,b)=>a-b); slices.sort((a,b)=>a-b);
    return {...window.__s3Qa.info(), raycast_median_ms:times[6], raycast_max_ms:times.at(-1), slice_median_ms:slices[6]};
  });
  await page.screenshot({path:path.join(out,'04-review-fixes-stress.png')});
  assert.deepEqual(errors, []); assert.deepEqual(failures, []);
  const report = { result: 'passed', lines: 8, checks: ['offline dependencies','orbit','visibility','envelope','exaggeration','depth slice','curtain pick to 2D','PNG/JSON export','missing local navigation','manual layout save/reload','draft coordinates survive scale and reload','closed-card reprocess including same options','nonlinear mesh error bound'], performance:{lines:40, build_ms:buildMs,...performanceResult}, errors, failures };
  fs.writeFileSync(path.join(out, 'verification.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report)); await browser.close();
})().catch(async (e) => { console.error(e); await activeBrowser?.close(); process.exitCode = 1; });
