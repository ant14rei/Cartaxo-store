/* Liga a página ao servidor próprio da Cartaxo Store.
   O painel foi escrito para a prévia no Claude (claude.use); este adaptador
   oferece as mesmas funções usando a API do servidor (/api/...). */
(function(){
  const H={"X-Cartaxo":"1"};
  const erro=r=>{const e=new Error("http "+r.status); e.status=r.status; e.code=r.status===413?"too_large":r.status===415?"unsupported_type":r.status===401?"upstream_auth":"upstream_error"; return e;};
  const ok=async r=>{ if(!r.ok) throw erro(r); return r.status===204?null:r.json(); };
  const copia=d=>d==null?undefined:JSON.parse(JSON.stringify(d));
  const snap=(id,d)=>({id,exists:d!=null,data:()=>copia(d)});
  const url=(c,i)=>"/api/db/"+encodeURIComponent(c)+(i?"/"+encodeURIComponent(i):"");
  function doc(path){ const [col,id]=String(path).split("/");
    const me={path,id,
      get:async()=>{ const r=await fetch(url(col,id),{credentials:"same-origin",headers:H}); if(r.status===404) return snap(id,null); const d=await ok(r); return snap(id,d.data); },
      set:async data=>{ await ok(await fetch(url(col,id),{method:"PUT",credentials:"same-origin",headers:{...H,"Content-Type":"application/json"},body:JSON.stringify(data)})); },
      update:async data=>{ const s=await me.get(); await me.set({...(s.data()||{}),...data}); },
      delete:async()=>{ await ok(await fetch(url(col,id),{method:"DELETE",credentials:"same-origin",headers:H})); },
      onSnapshot:(fn,falha)=>{ let parou=false; me.get().then(s=>{ if(!parou) fn(s); },e=>{ if(falha) falha(e); }); return ()=>{ parou=true; }; }};
    return me; }
  function collection(col){ const q={_o:"",_d:"asc",_l:100,
      orderBy(f,d){ this._o=f; this._d=d||"asc"; return this; }, limit(n){ this._l=n; return this; },
      async get(){ const r=await fetch(url(col)+"?ordem="+encodeURIComponent(this._o)+"&dir="+this._d+"&limite="+this._l,{credentials:"same-origin",headers:H}); const d=await ok(r); const docs=d.docs.map(x=>snap(x.id,x.data)); return {docs,size:docs.length,empty:!docs.length}; }};
    return q; }
  let quem=null; const eu=()=>quem||(quem=fetch("/api/eu",{credentials:"same-origin",headers:H}).then(r=>r.ok?r.json():{admin:false}).catch(()=>({admin:false})).then(j=>{ if(j&&j.admin&&window.cxMarcarDono) window.cxMarcarDono(); return j; }));
  const assets={ async upload(blob){ const fd=new FormData(); fd.append("foto",blob,"foto"); const d=await ok(await fetch("/api/fotos",{method:"POST",credentials:"same-origin",headers:H,body:fd})); return {id:d.id,url:"/_blob/"+d.id,sizeBytes:d.bytes,contentType:d.tipo}; },
    async list(){ return {assets:[]}; }, async delete(){ return {deleted:false}; } };
  window.CARTAXO_SERVIDOR=true;

  /* Métricas sem dado pessoal: nenhum nome, telefone ou IP é guardado; o id do visitante é renovado a cada 90 dias. O aparelho do dono não é contado. */
  (function(){
    let dono=false; try{ dono=localStorage.getItem("cx_dono")==="1"; }catch(e){}
    const rid=()=>{ try{ return Array.from(crypto.getRandomValues(new Uint8Array(12)),b=>b.toString(16).padStart(2,"0")).join(""); }catch(e){ return ""; } };
    let v="", s="", ultimo="";
    try{ v=localStorage.getItem("cx_v")||""; const t=+(localStorage.getItem("cx_v_t")||0);
      if(!/^[0-9a-f]{24}$/.test(v)||!t||Date.now()-t>90*864e5){ v=rid(); localStorage.setItem("cx_v",v); localStorage.setItem("cx_v_t",String(Date.now())); } }catch(e){ v=v||rid(); }
    try{ s=sessionStorage.getItem("cx_s")||""; if(!/^[0-9a-f]{24}$/.test(s)){ s=rid(); sessionStorage.setItem("cx_s",s); } ultimo=sessionStorage.getItem("cx_ped")||""; }catch(e){ s=s||rid(); }
    function enviar(t,d){
      if(dono||!v||!s) return;
      const corpo=JSON.stringify(Object.assign({t,v,s},d||{}));
      try{ if(navigator.sendBeacon&&navigator.sendBeacon("/api/ev",new Blob([corpo],{type:"application/json"}))) return; }catch(e){}
      try{ fetch("/api/ev",{method:"POST",body:corpo,headers:{"Content-Type":"application/json"},keepalive:true,credentials:"same-origin"}).catch(()=>{}); }catch(e){}
    }
    window.cxTrack=function(t,d){
      if(t==="pedido"){ const sig=JSON.stringify((d&&d.itens)||[]); if(sig===ultimo) return; ultimo=sig; try{ sessionStorage.setItem("cx_ped",sig); }catch(e){} }
      enviar(t,d);
    };
    window.cxMarcarDono=function(){ dono=true; try{ localStorage.setItem("cx_dono","1"); }catch(e){} };
    let q; try{ q=new URLSearchParams(location.search); }catch(e){ q=new URLSearchParams(""); }
    const limpo=x=>String(x||"").toLowerCase().replace(/[^a-z0-9_-]/g,"").slice(0,30);
    enviar("visita",{ref:String(document.referrer||"").slice(0,300),us:limpo(q.get("utm_source")),um:limpo(q.get("utm_medium")),ad:(q.has("fbclid")||q.has("gclid"))?1:0});
  })();
  window.claude={ use: async nome=>{
    if(nome==="db") return {doc,collection};
    if(nome==="user") return {isOwner:async()=>!!(await eu()).admin, canEdit:async()=>!!(await eu()).admin};
    if(nome==="assets") return (await eu()).admin?assets:null;
    return null; } };
})();
