(() => {
  const host=window,doc=host.document;
  if(host.__dndGameTransition)return;
  const cover=doc.createElement('div');cover.id='dnd-game-transition';cover.setAttribute('aria-hidden','true');
  doc.body.append(cover);
  let timeout=0;
  const finish=()=>{host.clearTimeout(timeout);cover.classList.remove('dnd-transition-active');};
  const start=()=>{
    host.clearTimeout(timeout);cover.classList.add('dnd-transition-active');
    // A server error must never leave navigation covered indefinitely.
    timeout=host.setTimeout(()=>{if(doc.querySelector('.st-key-page2_game_mount'))doc.body.classList.add('dnd-game-ready');finish();},10000);
  };
  doc.addEventListener('click',event=>{
    if(event.target.closest?.('.st-key-home_start_button button,.st-key-home_continue_button button,.st-key-records_open_button button')?.disabled===false)start();
  },true);
  host.__dndGameTransition={start,finish};
})();
