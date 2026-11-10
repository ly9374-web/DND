// Test the production composer handler; reading-surface scrolling is verified in Playwright.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/components/immersive_workspace/workspace.js'),'utf8');
const handler=source.slice(source.indexOf('  function unlockWheel('),source.indexOf('  const onWheel=',source.indexOf('  function unlockWheel(')));
function fixture(){
  let now=0;
  const state={reveal:0,gesture:{last:0,direction:0,acc:0}};
  const changes=[];
  const host={innerHeight:900,performance:{now:()=>now},addEventListener(){},removeEventListener(){},setTimeout(){return 1},clearTimeout(){}};
  const ctx=vm.createContext({state,host,editingBrain:()=>false,reveal:open=>{changes.push(open);state.reveal=open?300:0}});
  vm.runInContext('let wheelLock=null,wheelLockTimer=0;'+handler+';this.wheel=composerWheel;',ctx);
  return {state,changes,wheel(t,dy,options={}){now=t;let prevented=false;ctx.wheel({ctrlKey:false,deltaX:0,deltaY:dy,deltaMode:0,preventDefault(){prevented=true},...options});return prevented}};
}
test('down opens and up closes without an idle lock',()=>{
  const f=fixture();assert.ok(f.wheel(100,100));assert.equal(f.state.reveal,300);
  f.wheel(120,-100);assert.equal(f.state.reveal,0);
});
test('same-direction inertia never toggles back',()=>{
  const f=fixture();[100,120,200,400,600].forEach(t=>f.wheel(t,50));
  assert.deepEqual(f.changes,[true]);
});
test('tiny opposite deltas do not flicker the panel',()=>{
  const f=fixture();f.wheel(100,100);f.wheel(120,-2);f.wheel(140,-3);
  assert.equal(f.state.reveal,300);f.wheel(150,-20);assert.equal(f.state.reveal,0);
});
test('zoom and horizontal wheels retain their native behavior',()=>{
  const f=fixture();assert.equal(f.wheel(100,10,{ctrlKey:true}),false);
  assert.equal(f.wheel(120,10,{deltaX:20}),false);assert.deepEqual(f.changes,[]);
});
test('line wheel units work immediately',()=>{
  const f=fixture();f.wheel(100,2,{deltaMode:1});assert.equal(f.state.reveal,300);
});
