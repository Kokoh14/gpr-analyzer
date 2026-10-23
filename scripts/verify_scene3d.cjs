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
  await page.route('**/*', (route) => route.request().url().startsWith('http://127.0.0.1') || route.request().url().startsWith('data:') ? route.continue() : route.abort());
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
  assert.equal(await page.locator('#s3DepthValue').textContent(), '1.20 m');
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
  assert.deepEqual(errors, []); assert.deepEqual(failures, []);
  const report = { result: 'passed', lines: 8, checks: ['offline dependencies','orbit','visibility','envelope','exaggeration','depth slice','curtain pick to 2D','PNG/JSON export','missing local navigation','manual layout save/reload'], errors, failures };
  fs.writeFileSync(path.join(out, 'verification.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report)); await browser.close();
})().catch(async (e) => { console.error(e); await activeBrowser?.close(); process.exitCode = 1; });
