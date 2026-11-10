(() => {
  const host=window,doc=host.document;
  if(host.__dndImmersive)return;
  const $=s=>doc.querySelector(s),body=doc.body;
  const newGesture=()=>({last:0,direction:0,acc:0});
  const state={scope:null,config:null,height:host.innerHeight/2,reveal:0,right:false,gesture:newGesture(),source:null,stickBottom:true,historyIntentUntil:0,historyAnchor:null,requestedSections:new Set(),collapseSidebar:false,anchorTurn:'',anchorPinned:false,scrollWriteAt:-Infinity,firstConfig:true};
  const build=host.__dndImmersiveBuild||'';
  let entryFrame=0,leaveTimer=0,anchorFrame=0,composerNode=null,wheelLock=null,wheelLockTimer=0;
  const svg=p=>`<svg viewBox="0 0 24 24" aria-hidden="true">${p}</svg>`;
  const bg=doc.createElement('div');bg.id='dnd-immersive-bg';
  const edge=doc.createElement('canvas'),image=doc.createElement('img'),video=doc.createElement('video');image.alt='';image.crossOrigin='anonymous';video.crossOrigin='anonymous';video.muted=true;video.loop=true;video.playsInline=true;bg.append(edge,image,video);
  const veil=doc.createElement('div');veil.id='dnd-immersive-veil';
  const bar=doc.createElement('div');bar.id='dnd-immersive-topbar';
  const left=doc.createElement('button');left.type='button';left.setAttribute('aria-label','游戏设置');left.innerHTML=svg('<path d="M4 6h16M4 12h11M4 18h16"/>');
  const right=doc.createElement('button');right.type='button';right.setAttribute('aria-label','图片与视频');right.innerHTML=svg('<path d="M4 7h16M4 17h16M8 4v6M16 14v6"/>')+'<span>图片与视频</span>';const tools=doc.createElement('div');tools.style.cssText='display:flex;gap:8px;pointer-events:auto';const play=doc.createElement('button'),sound=doc.createElement('button');play.type=sound.type='button';play.hidden=sound.hidden=true;play.setAttribute('aria-label','暂停背景视频');play.title='播放／暂停背景视频';play.innerHTML=svg('<path d="M8 4v16M16 4v16"/>');sound.setAttribute('aria-label','开启背景声音');sound.title='静音／声音';sound.innerHTML=svg('<path d="m11 4-6 5H2v6h3l6 5zM15 8a6 6 0 0 1 0 8"/>');tools.append(play,sound,right);bar.append(left,tools);
  play.onclick=()=>video.paused?video.play().catch(()=>{}):video.pause();sound.onclick=()=>{video.muted=!video.muted;sound.setAttribute('aria-pressed',String(!video.muted));sound.setAttribute('aria-label',video.muted?'开启背景声音':'关闭背景声音');};
  const resize=doc.createElement('div');resize.id='dnd-chat-resize';resize.tabIndex=0;resize.setAttribute('role','separator');resize.setAttribute('aria-orientation','horizontal');resize.setAttribute('aria-label','上下拖动调整聊天记录高度');resize.innerHTML=svg('<path d="M12 3v18m-4-14 4-4 4 4M8 17l4 4 4-4"/>');
  ($('.stApp')||body).append(bg,veil,bar,resize);
  const history=()=>$('.st-key-page2_chat_history'),brain=()=>$('.st-key-page2_brain_scroll');
  const set=(key,value)=>{if(body.style.getPropertyValue('--dnd-'+key)!==value)body.style.setProperty('--dnd-'+key,value);};
  function sidebarOpen(){return $('[data-testid="stSidebar"]')?.getAttribute('aria-expanded')==='true';}
  function previewVeil(){
    const slider=$('.st-key-page2_veil_opacity input[type="range"],.st-key-page2_veil_opacity [role="slider"]');
    if(!slider)return;
    const value=Number(slider.value??slider.getAttribute('aria-valuenow'));
    if(Number.isFinite(value))set('veil',String(Math.max(0,Math.min(98,value))/100));
  }
  function nativeSidebarToggle(open){const isOpen=sidebarOpen();if(open===isOpen)return;const control=$(isOpen?'[data-testid="stSidebarCollapseButton"]':'[data-testid="stExpandSidebarButton"]');(control?.matches('button')?control:control?.querySelector('button'))?.click();}
  /* A new game visit starts with the settings drawer closed, even when
     Streamlit restored an expanded sidebar from localStorage. */
  function collapseSidebarOnce(){
    if(!state.collapseSidebar)return;
    const sidebar=$('[data-testid="stSidebar"]');if(!sidebar)return;
    if(sidebar.getAttribute('aria-expanded')!=='true'){state.collapseSidebar=false;return;}
    if(!$('[data-testid="stSidebarCollapseButton"]'))return;
    state.collapseSidebar=false;nativeSidebarToggle(false);
  }
  const idEsc=id=>host.CSS?.escape?host.CSS.escape(String(id)):String(id).replace(/[^\w-]/g,'\\$&');
  function setScroll(node,value){state.scrollWriteAt=host.performance.now();node.scrollTop=value;}
  /* A fresh exchange starts at the top of the reading surface. The first 20%
     of the box is the top fade mask, so the turn is offset past it to keep
     its opening lines legible. Re-applied while pinned so a short loading
     placeholder cannot clamp the position before the reply arrives. */
  function pinExchange(){
    const h=history(),id=state.anchorTurn;if(!h||!id)return;
    const turn=h.querySelector('.st-key-page2_chat_turn_'+idEsc(id));if(!turn)return;
    const delta=turn.getBoundingClientRect().top-h.getBoundingClientRect().top;
    const inset=Math.min(120,Math.max(24,h.clientHeight*.2));
    const target=Math.max(0,h.scrollTop+delta-inset);
    if(Math.abs(h.scrollTop-target)>1)setScroll(h,target);
  }
  left.onclick=()=>{if(host.innerWidth<1100&& !sidebarOpen())state.right=false;if(!sidebarOpen())requestSection('settings');nativeSidebarToggle(!sidebarOpen());schedule();};
  right.onclick=()=>{state.right=!state.right;if(state.right)requestSection('media');if(state.right&&host.innerWidth<1100)nativeSidebarToggle(false);schedule();};
  /* Double-click editing needs room to write, so the panel grows while the
     editor is mounted and falls back to its reading height afterwards. */
  const editingBrain=()=>!!$('.st-key-page2_brain_editor');
  function brainHeight(){
    if(!state.reveal)return 0;
    if(!editingBrain())return state.reveal;
    const room=Math.max(160,host.innerHeight-84-58-6-100);
    return Math.min(room,Math.max(state.reveal,Math.round(host.innerHeight*.52)));
  }
  function maxHeight(){return Math.max(100,host.innerHeight-84-58-6-brainHeight());}
  function sync(light=false){
    collapseSidebarOnce();
    const h=history();if(!h)return;
    body.classList.toggle('dnd-right-open',state.right);
    body.classList.toggle('dnd-brain-open',state.reveal>0);
    const openLeft=sidebarOpen(),side=Math.min(301,host.innerWidth<=850?host.innerWidth*.42:301),gap=host.innerWidth<=850?20:48,margin=host.innerWidth<=850?10:14;
    set('left',(openLeft?side+gap:margin)+'px');set('right',(state.right?side+gap:margin)+'px');
    left.setAttribute('aria-expanded',String(openLeft));right.setAttribute('aria-expanded',String(state.right));
    const height=Math.min(state.height,maxHeight()),brainH=brainHeight();
    set('chat-height',height+'px');set('brain-height',brainH+'px');
    // The composer (50), gap (8), and bottom inset (6) are fixed by CSS.
    // Derive the veil/handle position without forcing layout after each write.
    set('veil-height',(height+brainH+64)+'px');
    resize.style.left='var(--dnd-left)';resize.style.right='var(--dnd-right)';resize.style.top='calc(100dvh - var(--dnd-veil-height) - 9px)';resize.style.width='auto';
    resize.setAttribute('aria-valuemin','100');resize.setAttribute('aria-valuemax',String(Math.round(maxHeight())));resize.setAttribute('aria-valuenow',String(Math.round(height)));
    if(!light){if(state.anchorPinned)pinExchange();else if(state.stickBottom)setScroll(h,h.scrollHeight);}
  }
  let scheduled=0;
  function schedule(){if(!scheduled)scheduled=host.requestAnimationFrame(()=>{scheduled=0;sync();});}
  function reveal(open){
    state.reveal=open?Math.min(300,Math.max(140,Math.round(host.innerHeight*.36))):0;
    sync();
  }
  function requestSection(section){
    if(state.config?.sections?.includes(section)||state.requestedSections.has(section))return;
    const button=$('.st-key-page2_mount_'+section+' button');
    if(button){state.requestedSections.add(section);button.click();}
  }
  function loadEarlier(){
    const h=history(),info=state.config?.history;
    if(!h||!info?.hasMore||state.historyAnchor)return;
    const button=$('.st-key-page2_load_earlier button');
    const turn=h.querySelector('.st-key-page2_chat_turn_'+host.CSS.escape(info.firstId));
    if(!button||button.disabled||!turn)return;
    state.historyAnchor={id:info.firstId,offset:turn.getBoundingClientRect().top-h.getBoundingClientRect().top};
    state.historyIntentUntil=0;state.stickBottom=false;
    button.click();
  }
  function restoreHistory(){
    const anchor=state.historyAnchor;
    if(!anchor||state.config?.history?.firstId===anchor.id)return;
    host.cancelAnimationFrame(anchorFrame);
    anchorFrame=host.requestAnimationFrame(()=>{anchorFrame=host.requestAnimationFrame(()=>{
      anchorFrame=0;const h=history();
      const turn=h?.querySelector('.st-key-page2_chat_turn_'+host.CSS.escape(anchor.id));
      if(turn){setScroll(h,h.scrollTop+turn.getBoundingClientRect().top-h.getBoundingClientRect().top-anchor.offset);}
      state.historyAnchor=null;state.historyIntentUntil=0;
    });});
  }
  function unlockWheel(){
    host.clearTimeout(wheelLockTimer);wheelLockTimer=0;
    if(wheelLock){host.removeEventListener('wheel',wheelLock,true);wheelLock=null;}
  }
  /* A composer toggle moves the layout under a stationary pointer, so the
     tail of the trackpad gesture (including momentum) would otherwise land on
     the chat history and scroll it. Only the remaining travel of the triggering
     gesture is swallowed; a decisive contrary flick counts as a new gesture and
     is released at once, so a second swipe never needs an extra click before it
     is heard. Anything over the story brain is reading input and stays native,
     which is why the panel responds to the wheel without being clicked. */
  function lockWheel(dir){
    host.clearTimeout(wheelLockTimer);
    if(!wheelLock){
      wheelLock=e=>{
        if(e.ctrlKey||!e.deltaY)return;
        if(e.target?.closest?.('.st-key-page2_story_brain_panel')||Math.sign(e.deltaY)!==dir&&Math.abs(e.deltaY)>6){unlockWheel();return;}
        e.preventDefault();host.clearTimeout(wheelLockTimer);wheelLockTimer=host.setTimeout(unlockWheel,160);
      };
      host.addEventListener('wheel',wheelLock,{passive:false,capture:true});
    }
    wheelLockTimer=host.setTimeout(unlockWheel,160);
  }
  /* Shared threshold for both surfaces: accumulate one direction and reset on
     a reversal, so momentum noise and tiny jitter cannot flip the panel back
     and forth (which is what read as flicker). */
  function gestureToggle(e){
    const now=host.performance.now(),dir=Math.sign(e.deltaY),g=state.gesture;
    if(dir!==g.direction||now-g.last>240)g.acc=0;
    g.last=now;g.direction=dir;
    g.acc+=Math.abs(e.deltaY)*(e.deltaMode===1?16:e.deltaMode===2?host.innerHeight:1);
    if(g.acc<22)return false;
    if((dir>0&&state.reveal)||(dir<0&&!state.reveal))return false;
    g.acc=0;return true;
  }
  function composerWheel(e){
    if(e.ctrlKey||Math.abs(e.deltaX)>Math.abs(e.deltaY)||!e.deltaY)return;
    e.preventDefault();
    if(editingBrain()&&e.deltaY<0)return;
    if(gestureToggle(e)){const dir=Math.sign(e.deltaY);lockWheel(dir);reveal(dir>0);}
  }
  /* The story brain is a reading surface: swiping it only moves its own
     content and stops at the top edge instead of collapsing the panel, so
     trackpad momentum can never close it. */
  const onWheel=e=>{
    if(wheelLock)return;
    if(e.deltaY&&e.target.closest?.('.st-key-page2_chat_history'))state.anchorPinned=false;
    if(e.deltaY<0&&e.target.closest?.('.st-key-page2_chat_history')){
      state.historyIntentUntil=host.performance.now()+600;
      if(history()?.scrollTop<=16)loadEarlier();
    }
  };
  doc.addEventListener('wheel',onWheel,{passive:true});
  function bindComposer(){
    const node=$('.st-key-page2_chat_composer');if(node===composerNode)return;
    composerNode?.removeEventListener('wheel',composerWheel);
    composerNode=node;composerNode?.addEventListener('wheel',composerWheel,{passive:false});
  }
  const onHistoryScroll=e=>{
    const h=history();if(e.target!==h)return;
    // Ignore the scroll events produced by our own scrollTop writes.
    if(host.performance.now()-state.scrollWriteAt<80)return;
    state.anchorPinned=false;
    state.stickBottom=!state.historyAnchor&&h.scrollHeight-h.clientHeight-h.scrollTop<3;
    if(h.scrollTop<=16&&host.performance.now()<state.historyIntentUntil)loadEarlier();
  };
  doc.addEventListener('scroll',onHistoryScroll,true);
  const onBrainEdit=e=>{if(e.target.closest?.('.dnd-brain-viewer[data-editable="true"]'))requestSection('brain_editor');};
  doc.addEventListener('dblclick',onBrainEdit);
  const onKey=e=>{if(e.target.closest?.('.dnd-brain-viewer')&&e.key==='Enter'){onBrainEdit(e);return;}if(e.target.closest?.('.st-key-page2_brain_editor textarea')){if(e.key==='Escape'){$('.st-key-page2_brain_cancel button')?.click();e.preventDefault();return;}if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){$('.st-key-page2_brain_save button')?.click();e.preventDefault();return;}}if(e.key==='Escape'&&!e.target.closest?.('textarea,[contenteditable="true"]')){if(state.reveal)reveal(false);else if(state.right){state.right=false;sync();}}if(e.target.closest?.('.st-key-page2_chat_history')){if(['ArrowUp','PageUp','Home'].includes(e.key))state.stickBottom=false;if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key))state.anchorPinned=false;}};
  doc.addEventListener('keydown',onKey);
  const onAudioPlay=e=>{if(e.target.tagName==='AUDIO')doc.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause();});};
  doc.addEventListener('play',onAudioPlay,true);
  // React Aria updates the range's value attribute while dragging, without
  // emitting native input events. Listen to both so the veil never waits for
  // Streamlit's release-triggered server rerun.
  const onVeilInput=e=>{if(e.target.closest?.('.st-key-page2_veil_opacity'))previewVeil();};
  doc.addEventListener('input',onVeilInput,true);doc.addEventListener('change',onVeilInput,true);
  resize.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();const y=e.clientY,initial=history().getBoundingClientRect().height;state.stickBottom=history().scrollHeight-history().scrollTop-history().clientHeight<3;resize.setPointerCapture(e.pointerId);body.classList.add('dnd-chat-resizing');const move=p=>{state.height=Math.max(100,Math.min(initial-(p.clientY-y),maxHeight()));sync(true);};const end=()=>{body.classList.remove('dnd-chat-resizing');resize.removeEventListener('pointermove',move);resize.removeEventListener('pointerup',end);resize.removeEventListener('pointercancel',end);sync();};resize.addEventListener('pointermove',move);resize.addEventListener('pointerup',end);resize.addEventListener('pointercancel',end);});
  resize.addEventListener('keydown',e=>{if(!['ArrowUp','ArrowDown','Home','End'].includes(e.key))return;e.preventDefault();const step=e.shiftKey?48:16;state.height=e.key==='Home'?100:e.key==='End'?maxHeight():Math.max(100,Math.min(state.height+(e.key==='ArrowUp'?step:-step),maxHeight()));sync();});
  const onResize=()=>{state.height=Math.min(state.height,maxHeight());schedule();};host.addEventListener('resize',onResize);
  const ctx=edge.getContext('2d',{willReadFrequently:true});
  let imageRetry=0,imageRetryTimer=0;
  function isCrossOrigin(src){try{return new URL(src,host.location.href).origin!==host.location.origin;}catch(_){return false;}}
  function draw(){
    const source=state.source;if(!source||state.snapshotReady)return;const sw=source.naturalWidth||source.videoWidth,sh=source.naturalHeight||source.videoHeight;if(!sw||!sh||source===video&&video.readyState<2)return;
    // Retain one low-resolution still for the blurred surround. The original
    // image/video stays mounted and the browser handles sizing and playback.
    state.snapshotReady=true;
    const cw=640,ch=360;if(edge.width!==cw||edge.height!==ch){edge.width=cw;edge.height=ch;}
    const k=Math.min(cw/sw,ch/sh),w=sw*k,h=sh*k,x=(cw-w)/2,y=(ch-h)/2,cover=Math.max(cw/sw,ch/sh);ctx.clearRect(0,0,cw,ch);
    try{ctx.drawImage(source,(cw-sw*cover)/2,(ch-sh*cover)/2,sw*cover,sh*cover);ctx.drawImage(source,x,y,w,h);}catch(_){}
    source.classList.add('dnd-media-ready');edge.classList.add('dnd-media-ready');
  }
  image.onload=()=>{imageRetry=0;draw();};video.onloadeddata=draw;video.onplay=()=>{play.innerHTML=svg('<path d="M8 4v16M16 4v16"/>');play.setAttribute('aria-label','暂停背景视频');};video.onpause=()=>{play.innerHTML=svg('<path d="m8 4 12 8-12 8z"/>');play.setAttribute('aria-label','播放背景视频');};
  /* Remote provider URLs (Replicate temp files, signed CDN links) often omit
     CORS headers or fail transiently. Retry once without CORS, then twice more
     with the same source so a momentary outage does not blank the backdrop. */
  image.onerror=()=>{
    if(state.source!==image)return;
    const src=state.config?.background?.src||'';if(!src)return;
    if(image.crossOrigin){image.crossOrigin=null;imageRetry=1;image.src=src;return;}
    if(imageRetry>=2)return;
    imageRetry++;
    const attempt=imageRetry;
    imageRetryTimer=host.setTimeout(()=>{if(state.source!==image||state.config?.background?.src!==src)return;image.src=src;},attempt*900);
  };
  video.onerror=()=>{
    if(state.source===video&&video.crossOrigin){video.crossOrigin=null;video.src=state.config.background.src;video.play().catch(()=>{});}
  };
  function setBackground(info){
    const identity=JSON.stringify(info);if(state.backgroundKey===identity)return;play.hidden=sound.hidden=info.kind!=='video'||!info.src;video.muted=true;sound.setAttribute('aria-label','开启背景声音');sound.setAttribute('aria-pressed','false');ctx.clearRect(0,0,edge.width,edge.height);state.backgroundKey=identity;state.snapshotReady=false;state.source=null;imageRetry=0;video.pause();video.removeAttribute('src');video.load();image.removeAttribute('src');[image,video,edge].forEach(n=>{n.style.display='none';n.classList.remove('dnd-media-ready');});
    host.clearTimeout(imageRetryTimer);edge.width=640;
    const has=!!info.src&&info.kind!=='none';body.classList.toggle('dnd-has-media',has);if(!has){ctx.clearRect(0,0,edge.width,edge.height);return;}
    const source=info.kind==='video'?video:image;state.source=source;source.style.display='block';edge.style.display='block';source.crossOrigin=isCrossOrigin(info.src)?'anonymous':null;source.src=info.src;if(source===video)video.play().catch(()=>{});
  }
  /* Only react to Streamlit's own DOM. Our sync writes aria-expanded/aria-*
     on the docked controls; observing those fed the scheduler its own output
     and kept the page doing a full sync every frame while completely idle. */
  const ours=new Set([left,right,play,sound,resize,bar,veil,bg]);
  const observer=new host.MutationObserver(changes=>{
    let veilChanged=false,relayout=false;
    for(const m of changes){
      const t=m.target;
      if(ours.has(t)||t.closest?.('#dnd-immersive-topbar,#dnd-chat-resize,#dnd-immersive-bg,#dnd-immersive-veil'))continue;
      if(t.closest?.('.st-key-page2_veil_opacity'))veilChanged=true;
      if(m.type==='childList'||m.attributeName==='aria-expanded'||m.attributeName==='src')relayout=true;
    }
    if(veilChanged)previewVeil();
    if(relayout){checkLifecycle();bindComposer();schedule();if($('.st-key-page2_chat_input textarea'))ready();const editor=$('.st-key-page2_brain_editor textarea');if(editor&&editor!==state.editor){state.editor=editor;editor.focus();}}
  });observer.observe($('.stApp')||body,{childList:true,subtree:true,attributes:true,attributeFilter:['aria-expanded','src','value','aria-valuenow','aria-valuetext']});
  function hideOverlays(){[bg,veil,bar,resize].forEach(n=>{if(n.style.display!=='none')n.style.display='none';});}
  function showOverlays(){[bg,veil,bar,resize].forEach(n=>{if(n.style.display==='none')n.style.display='';});}
  function checkLifecycle(){
    const alive=$('.st-key-page2_game_mount')||$('.st-key-page2_workspace')||$('.st-key-page2_chat_history');
    if(alive){host.clearTimeout(leaveTimer);leaveTimer=0;showOverlays();return;}
    /* Do not let the media backdrop, veil or docked buttons paint over the
       next Streamlit page while its DOM is being replaced. */
    hideOverlays();
    if(!leaveTimer)leaveTimer=host.setTimeout(()=>{leaveTimer=0;if(!$('.st-key-page2_game_mount')&&!$('.st-key-page2_workspace'))dispose();},600);
  }
  function begin(){if(body.classList.contains('dnd-game-ready')||body.classList.contains('dnd-game-loading'))return;body.classList.add('dnd-game-loading');host.__dndGameTransition?.start();}
  function ready(){
    if(body.classList.contains('dnd-game-ready')||entryFrame)return;
    const pane=$('.st-key-page2_dialogue_pane');if(!pane)return;
    pane.style.opacity='0';
    // Commit the final layout before transitioning its opacity. Do not wait
    // for remote media; late media has its own fade and cannot block input.
    entryFrame=host.requestAnimationFrame(()=>{entryFrame=host.requestAnimationFrame(()=>{
      entryFrame=0;body.classList.remove('dnd-game-loading');body.classList.add('dnd-game-ready');host.__dndGameTransition?.finish();
      pane.style.removeProperty('opacity');
    });});
  }
  function dispose(){unlockWheel();composerNode?.removeEventListener('wheel',composerWheel);observer.disconnect();host.clearTimeout(leaveTimer);host.cancelAnimationFrame(entryFrame);host.cancelAnimationFrame(scheduled);host.cancelAnimationFrame(anchorFrame);host.clearTimeout(imageRetryTimer);video.pause();video.removeAttribute('src');video.load();doc.removeEventListener('dblclick',onBrainEdit);doc.removeEventListener('wheel',onWheel);doc.removeEventListener('scroll',onHistoryScroll,true);doc.removeEventListener('keydown',onKey);doc.removeEventListener('play',onAudioPlay,true);doc.removeEventListener('input',onVeilInput,true);doc.removeEventListener('change',onVeilInput,true);host.removeEventListener('resize',onResize);[bg,veil,bar,resize].forEach(n=>n.remove());['dnd-game-ready','dnd-game-loading','dnd-right-open','dnd-brain-open','dnd-has-media','dnd-chat-resizing'].forEach(c=>body.classList.remove(c));['left','right','chat-height','brain-height','veil-height','veil'].forEach(k=>body.style.removeProperty('--dnd-'+k));host.__dndGameTransition?.finish();delete host.__dndImmersive;}
  host.__dndImmersive={
    version:build,begin,
    update(config){begin();bindComposer();if(state.scope!==config.scope){
      host.cancelAnimationFrame(anchorFrame);
      if(state.scope!==null){state.height=host.innerHeight/2;state.reveal=0;state.right=false;state.requestedSections.clear();}
      state.collapseSidebar=true;
      state.scope=config.scope;state.stickBottom=true;state.gesture=newGesture();state.historyAnchor=null;state.historyIntentUntil=0;
      state.anchorTurn='';state.anchorPinned=false;state.firstConfig=true;
    }state.config=config;
    /* Pin only when the server reports a *new* anchor id while this instance
       is running. A stale id restored on reload or re-entry must keep the old
       "open at the newest message" behaviour. */
    const anchorId=String(config.history?.anchorId||'');
    if(state.firstConfig){state.firstConfig=false;state.anchorTurn=anchorId;}
    else if(anchorId!==state.anchorTurn){
      state.anchorTurn=anchorId;state.anchorPinned=!!anchorId;
      if(anchorId){state.stickBottom=false;state.historyAnchor=null;state.historyIntentUntil=0;}
    }
    for(const section of config.sections||[])state.requestedSections.delete(section);set('veil',String(config.veil));previewVeil();setBackground(config.background);sync();restoreHistory();ready();},
    schedule,dispose,
    getState(){return{height:state.height,reveal:state.reveal,right:state.right,scope:state.scope,gesture:{...state.gesture},hasBrain:!!brain(),anchor:state.anchorTurn,anchorPinned:state.anchorPinned};}
  };
})();
