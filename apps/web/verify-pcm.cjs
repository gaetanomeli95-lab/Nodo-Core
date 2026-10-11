// Packet timing matters: per-quantum rounding used to lose samples at 44.1/48 kHz.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
for (const sampleRate of [16000, 44100, 48000]) {
  const packets=[]; let Worklet;
  vm.runInNewContext(readFileSync(__dirname+'/public/pcm-worklet.js','utf8'), {
    sampleRate, Int16Array, Math,
    AudioWorkletProcessor: class { constructor() { this.port={postMessage:(b)=>packets.push(new Int16Array(b))}; } },
    registerProcessor: (_, processor)=>{Worklet=processor;},
  });
  const worklet=new Worklet();
  for(let n=0;n<sampleRate;n+=128) worklet.process([[new Float32Array(Math.min(128,sampleRate-n)).fill(.25)]]);
  assert.equal(packets.length,50,`${sampleRate}: expected 50 packets per second`);
  assert.equal(packets.reduce((n,p)=>n+p.length,0),16000);
  assert.ok(packets.every(p=>p.every(n=>Math.abs(n-8192)<=1)));
}
console.log('PCM verified: 16 / 44.1 / 48 kHz -> 50 packets, 16000 samples per second, stable amplitude.');
