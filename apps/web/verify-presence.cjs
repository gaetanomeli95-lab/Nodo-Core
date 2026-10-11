const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const baseUrl = process.env.NODO_QA_URL || 'http://127.0.0.1:8124';
const artifacts = path.resolve(__dirname, '../../docs/qa');
fs.mkdirSync(artifacts, {recursive:true});
(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
    const errors=[], sent=[]; let socket;
    page.on('pageerror', e => errors.push(e.message));
    await page.routeWebSocket('**/api/v1/voice/stream*', ws => {
      socket=ws;
      ws.onMessage(raw => {
        const event=JSON.parse(raw); sent.push(event);
        if(event.type==='session.start') ws.send(JSON.stringify({type:'voice.session.started',state:'IDLE',engine:'live'}));
      });
    });
    await page.goto(baseUrl + '/#command');
    await page.waitForFunction(()=>!document.querySelector('.conversation-button').disabled);
    await page.getByRole('button',{name:'Parla con NODO',exact:true}).click();
    await page.getByRole('button',{name:'Microfono aperto',exact:true}).waitFor();
    assert(await page.getByText('MICROFONO ATTIVO',{exact:true}).isVisible());
    const send=e=>socket.send(JSON.stringify(e));
    send({type:'voice.session.state',state:'SPEAKING'});
    send({type:'tts.chunk',mime:'audio/pcm;rate=24000',audio:Buffer.alloc(24000*2*2).toString('base64')});
    send({type:'voice.turn.done'});
    await page.getByText('NODO PARLA',{exact:true}).waitFor();
    send({type:'ui.present',view:'sites_status',rows:[{name:'Sito dimostrativo',status:'ok'}]});
    await page.getByRole('heading',{name:'I tuoi siti',exact:true}).waitFor();
    send({type:'approval.required',approval_id:'shown-approval',label:'Pubblicare la nuova pagina?',site:'Sito dimostrativo',preview_url:'https://example.com'});
    await page.getByRole('region',{name:'Decisione in attesa'}).waitFor();
    await page.screenshot({path:path.join(artifacts, 'presenza-decisione.png'),fullPage:true});
    await page.getByRole('button',{name:'Conferma',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.decision-sheet .primary')?.disabled);
    assert(sent.some(e=>e.type==='approval.answer'&&e.approval_id==='shown-approval'&&e.decision==='approve'));
    send({type:'approval.resolved',approval_id:'shown-approval'});
    send({type:'voice.engine',engine:'browser',reason:'quota'});
    await page.getByRole('button',{name:'Parla con NODO',exact:true}).waitFor();
    await page.getByRole('alert').waitFor();
    assert(await page.getByText('Voce del browser',{exact:true}).isVisible());
    assert(!(await page.getByText('NODO PARLA',{exact:true}).isVisible()));
    assert.equal(await page.locator('.conversation-diary').count(),0);
    await page.evaluate(() => {
      class FakeRecognition {
        start() {
          const first = Object.assign([{transcript:'Ciao'}], {isFinal:true});
          this.onresult({results:[first]});
          const second = Object.assign([{transcript:' NODO'}], {isFinal:true});
          this.onresult({results:[first,second]});
        }
        stop() { this.onend?.(); }
      }
      window.SpeechRecognition=FakeRecognition;
    });
    await page.getByRole('button',{name:'Parla con NODO',exact:true}).click();
    await page.getByRole('button',{name:'Finito, invia',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.conversation-button')?.textContent.includes('Parla con NODO'));
    assert(sent.some(e=>e.type==='transcript.final'&&e.text==='Ciao NODO'));
    assert(!sent.some(e=>e.type==='transcript.final'&&e.text.includes('CiaoCiao')));
    assert.equal(errors.length,0);
    console.log(JSON.stringify({scenarios:['microphone activation','playback outlives server turn','contextual view','bound approval waits for response','quota fallback stops playback and mic, visible notice','browser cumulative transcript without repeated words'],pcmPackets:sent.filter(e=>e.type==='audio.chunk').length,errors}));
  } finally { await browser.close(); }
})();
