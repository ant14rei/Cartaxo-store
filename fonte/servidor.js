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
  let quem=null; const eu=()=>quem||(quem=fetch("/api/eu",{credentials:"same-origin",headers:H}).then(r=>r.ok?r.json():{admin:false}).catch(()=>({admin:false})));
  const assets={ async upload(blob){ const fd=new FormData(); fd.append("foto",blob,"foto"); const d=await ok(await fetch("/api/fotos",{method:"POST",credentials:"same-origin",headers:H,body:fd})); return {id:d.id,url:"/_blob/"+d.id,sizeBytes:d.bytes,contentType:d.tipo}; },
    async list(){ return {assets:[]}; }, async delete(){ return {deleted:false}; } };
  window.CARTAXO_SERVIDOR=true;
  window.claude={ use: async nome=>{
    if(nome==="db") return {doc,collection};
    if(nome==="user") return {isOwner:async()=>!!(await eu()).admin, canEdit:async()=>!!(await eu()).admin};
    if(nome==="assets") return (await eu()).admin?assets:null;
    return null; } };
})();
