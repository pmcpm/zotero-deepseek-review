/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Native note conversion adapted from Zotero integration.js,
 * copyright Corporation for Digital Scholarship. See LICENSE.
 * Local modifications: 2026-10-25. Source: https://github.com/zotero/zotero
 */
var reviewBridge;
function install() {}
function uninstall() {}
async function startup({rootURI,id}) {
  await Zotero.initializationPromise;
  const {AddonManager}=ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
  const addon=await AddonManager.getAddonByID(id);
  if(addon) addon.applyBackgroundUpdates=AddonManager.AUTOUPDATE_DISABLE;
  const dir=PathUtils.join(Services.dirsvc.get('ProfD',Ci.nsIFile).path,'codex-literature-bridge');
  await IOUtils.makeDirectory(dir,{ignoreExisting:true});
  const configPath=PathUtils.join(dir,'config.json'),statePath=PathUtils.join(dir,'state.json');
  if(!await IOUtils.exists(configPath)) {
    const bytes=crypto.getRandomValues(new Uint8Array(32));
    await IOUtils.writeJSON(configPath,{schema_version:1,token:[...bytes].map(x=>x.toString(16).padStart(2,'0')).join('')});
  }
  const config=await IOUtils.readJSON(configPath);
  const state=await IOUtils.exists(statePath)?await IOUtils.readJSON(statePath):{tasks:{},ledger:{},styles:{}};
  const command='codexReviewCommand',route='/codex-bridge/v1';
  if(Zotero.Server.Endpoints[route]||Zotero.Integration.Interface.prototype[command])throw Error('Bridge collision');
  reviewBridge={state,config,busy:false,active:null};
  function canonical(x){if(Array.isArray(x))return '['+x.map(canonical).join(',')+']';if(x&&typeof x==='object')return '{'+Object.keys(x).sort().filter(k=>x[k]!==undefined).map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';return JSON.stringify(x);}
  async function hash(x){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(x))))].map(v=>v.toString(16).padStart(2,'0')).join('');}
  async function persist(){await IOUtils.writeJSON(statePath,state);}
  function file(path){if(typeof path!=='string')throw Error('Path required');let f=Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);f.initWithPath(path);f.normalize();return f;}
  function under(path,root){const f=file(path),r=file(root);return r.path.toLowerCase()===f.path.toLowerCase()||r.contains(f,true);}
  function task(id){const t=state.tasks[id];if(!t)throw Error('Unknown task');return t;}
  function scopedPath(t,path,kind){
    const f=file(path);
    if(kind==='document') {if(!t.documents.some(p=>file(p).path.toLowerCase()===f.path.toLowerCase()))throw Error('Document outside task scope');}
    else if(!(kind==='attachment'?t.attachmentRoots:[t.taskRoot]).some(r=>under(path,r)))throw Error('File outside task scope');
    if(!f.exists()||!f.isFile())throw Error('File missing');
    if(kind==='attachment'&&!f.path.toLowerCase().endsWith('.pdf'))throw Error('PDF required');
    if(kind==='document'&&!f.path.toLowerCase().endsWith('.docx'))throw Error('DOCX required');
    return f.path;
  }
  async function item(t,key){const it=await Zotero.Items.getByLibraryAndKeyAsync(t.libraryID,key);if(!it||!t.allowedItemKeys.includes(key))throw Error('Item outside task scope');return it;}
  async function snapshot(it){const data=it.toJSON();return {key:it.key,data,uri:Zotero.URI.getItemURI(it),csl:it.isRegularItem()?Zotero.Utilities.Item.itemToCSLJSON(it):null,metadataSnapshot:CodexReviewCSL.metadataSnapshot(data),snapshotToken:await hash(data)};}
  function applyFields(it,data){
    const allowed=['title','publicationTitle','date','volume','issue','pages','DOI','url','extra','language','abstractNote','journalAbbreviation','publisher','place','ISBN','ISSN','edition','series','seriesNumber','bookTitle','conferenceName','university','thesisType','reportNumber','institution','accessDate'];
    for(const [k,v] of Object.entries(data)){
      if(k==='creators'){if(!Array.isArray(v))throw Error('Creators array required');it.setCreators(v);}
      else {if(!allowed.includes(k)||typeof v!=='string')throw Error('Unsupported metadata field: '+k);it.setField(k,v);}
    }
  }
  function fields(it,data){
    const scratch=new Zotero.Item(it.itemTypeID);
    applyFields(scratch,data);
    applyFields(it,data);
  }
  Services.scriptloader.loadSubScript(rootURI+'csl-runtime.js',globalThis);
  const uninstallCSL=CodexReviewCSL.installHooks(styleID=>state.styles[styleID]?.policy);
  reviewBridge.uninstallCSL=uninstallCSL;
  Zotero.Integration.Interface.prototype[command]=async function(){
    const job=reviewBridge.active;if(!job)throw new Zotero.Exception.UserCancelled('Missing task command');
    job.session=this._session;job.oldActivate=this._session._dontActivateDocument;this._session._dontActivateDocument=true;
    try{
      if(job.op==='configureDocument'){
        if((await this._doc.getFields(this._app.primaryFieldType)).length)throw Error('Initialize only an empty native document');
        const d=new Zotero.Integration.DocumentData();d.dataVersion=4;
        d.style={styleID:job.styleID,locale:job.locale||'zh-CN',hasBibliography:true,bibliographyStyleHasBeenSet:false};
        d.prefs={fieldType:this._app.primaryFieldType,noteType:0,delayCitationUpdates:false,dontAskDelayCitationUpdates:true,automaticJournalAbbreviations:false};
        await this._session.setData(d,true);job.result={ok:true,sessionID:this._session.sessionID};return;
      }
      if(!await this._session.init(false,true))throw Error('needs_preferences');
      this._session.data.prefs.delayCitationUpdates=false;this._session.data.prefs.dontAskDelayCitationUpdates=true;
      if(job.op==='refreshDocument'){await this.refresh();job.result={ok:true};return;}
      if(job.op==='addBibliography'){
        const fs=await this._session.getFields(true);
        for(const f of fs)if((await f.getCode()).includes('ZOTERO_BIBL')){job.result={ok:true,alreadyPresent:true};return;}
        await this.addBibliography();job.result={ok:true};return;
      }
      const note=await item(job.task,job.noteKey);if(!note.isNote())throw Error('Expected note');
      const parsed=new DOMParser().parseFromString(note.getNote(),'text/html');
      const spans=[...parsed.querySelectorAll('.citation[data-citation]')];if(!spans.length)throw Error('Note has no citations');
      for(const span of spans){
        const c=JSON.parse(decodeURIComponent(span.dataset.citation));
        if(!c.citationItems?.length)throw Error('Malformed citation');
        for(const entry of c.citationItems){const it=entry.uris?.length?await Zotero.URI.getURIItem(entry.uris[0]):null;if(!it||it.libraryID!==job.task.libraryID||!job.task.allowedItemKeys.includes(it.key))throw Error('Unresolved or out-of-task citation');}
      }
      if(!await this._doc.canInsertField(this._session.data.prefs.fieldType))throw Error('cannot_insert_here');
      let field=new Zotero.Integration.CitationField(await this._session.addField(true));
      const initial=await this._session.getFields();let index=-1;
      for(let i=0;i<initial.length;i++)if(await initial[i].equals(field._field)){index=i;break;}
      if(index<0)throw Error('Temporary field missing');
      await this._session.updateFromDocument(0);
      const citations=await this._session._insertNoteIntoDocument(index,field,note);
      if(citations.length!==spans.length)throw Error('Citation conversion count mismatch');
      const fs=await this._session.getFields(true);await this._session.updateFromDocument(0);
      for(const c of citations){c.field=new Zotero.Integration.CitationField(fs[c.fieldIndex]);await this._session.addCitation(c.fieldIndex,await c.field.getNoteIndex(),c);}
      await this._session.updateDocument(0,false,false);job.result={ok:true,converted:citations.length};
    }catch(e){job.result={ok:false,error:String(e)};throw new Zotero.Exception.UserCancelled('Bridge failure recorded in task result');}
  };
  async function dispatch(d){
    if(d.op==='registerTask'){
      if(!/^[a-zA-Z0-9_-]{3,80}$/.test(d.taskID||''))throw Error('Invalid taskID');
      const library=Zotero.Libraries.get(d.libraryID);if(!library?.editable)throw Error('Library is read-only or missing');
      const root=file(d.taskRoot);if(!root.exists()||!root.isDirectory()||!root.parent)throw Error('Task directory missing or invalid');
      const roots=d.attachmentRoots||[d.taskRoot];for(const p of roots){const r=file(p);if(!r.parent||!r.exists()||!r.isDirectory())throw Error('Attachment directory missing or invalid');}
      if(!Array.isArray(d.documents)||!d.documents.length||d.documents.some(p=>!p.toLowerCase().endsWith('.docx')))throw Error('Explicit DOCX paths required');
      const previous=state.tasks[d.taskID];
      if(previous&&(previous.libraryID!==d.libraryID||file(previous.taskRoot).path!==root.path))throw Error('Task identity already belongs to another directory or library');
      const keys=[...new Set(d.allowedItemKeys||[])];for(const key of keys)if(!await Zotero.Items.getByLibraryAndKeyAsync(d.libraryID,key))throw Error('Requested item missing');
      const collectionKey=d.collectionKey||previous?.collectionKey;
      let collection=collectionKey?await Zotero.Collections.getByLibraryAndKeyAsync(d.libraryID,collectionKey):null;
      if(d.collectionKey&&!collection)throw Error('Collection missing');
      if(!collection){if(!d.collectionName)throw Error('Collection name required');collection=new Zotero.Collection();collection.libraryID=d.libraryID;collection.name=d.collectionName;await collection.saveTx();}
      state.tasks[d.taskID]={libraryID:d.libraryID,collectionKey:collection.key,collectionID:collection.id,taskRoot:root.path,attachmentRoots:roots,documents:d.documents,allowedItemKeys:[...new Set([...keys,...(previous?.allowedItemKeys||[])])]};
      return {ok:true,taskID:d.taskID,collectionKey:collection.key};
    }
    const t=task(d.taskID);
    if(d.op==='getItem')return {ok:true,...await snapshot(await item(t,d.key))};
    if(d.op==='linkItem'){
      const it=await item(t,d.key);let changed=false;
      try{await Zotero.DB.executeTransaction(async()=>{if((await snapshot(it)).snapshotToken!==d.expectedSnapshotToken)throw Error('snapshot_conflict');changed=true;it.addToCollection(t.collectionID);await it.save();});}
      catch(e){if(changed)await it.reload(null,true);throw e;}
      return {ok:true,...await snapshot(it)};
    }
    if(d.op==='createItem'){
      if(!d.metadataChecked)throw Error('Pre-import metadata check required');
      const it=new Zotero.Item(d.itemType);it.libraryID=t.libraryID;fields(it,d.fields);it.setCollections([t.collectionID]);await it.saveTx();t.allowedItemKeys.push(it.key);return {ok:true,...await snapshot(it)};
    }
    if(d.op==='updateItem'){
      if(!d.metadataChecked)throw Error('Pre-import metadata check required');
      const it=await item(t,d.key);let changed=false;
      try{await Zotero.DB.executeTransaction(async()=>{if((await snapshot(it)).snapshotToken!==d.expectedSnapshotToken)throw Error('snapshot_conflict');const scratch=new Zotero.Item(it.itemTypeID);applyFields(scratch,d.fields);changed=true;fields(it,d.fields);await it.save();});}
      catch(e){if(changed)await it.reload(null,true);throw e;}
      return {ok:true,...await snapshot(it)};
    }
    if(d.op==='attachPDF'){
      const parent=await item(t,d.key),path=scopedPath(t,d.path,'attachment');
      const att=await Zotero.Attachments.importFromFile({file:path,parentItemID:parent.id});return {ok:true,key:att.key,parentKey:parent.key};
    }
    if(d.op==='createNote'){
      if(typeof d.html!=='string'||d.html.length>1000000||/<script|<iframe|\bon[a-z]+\s*=|javascript\s*:/i.test(d.html))throw Error('Invalid note HTML');
      const note=new Zotero.Item('note');note.libraryID=t.libraryID;note.setNote(d.html);note.setCollections([t.collectionID]);await note.saveTx();t.allowedItemKeys.push(note.key);return {ok:true,key:note.key};
    }
    if(d.op==='installStyle'){
      if(!['resource_only','all'].includes(d.policy?.url_policy))throw Error('Explicit URL policy required; regenerate the task style and English cache');
      await Zotero.Styles.init();
      const path=scopedPath(t,d.path,'style');if(!path.toLowerCase().endsWith('.csl'))throw Error('CSL required');
      const xml=await Zotero.File.getContentsAsync(path);
      const parsed=new DOMParser().parseFromString(xml,'application/xml');
      const sid=parsed.getElementsByTagNameNS('http://purl.org/net/xbiblio/csl','id')[0]?.textContent;
      if(!sid?.startsWith('http://www.zotero.org/styles/codex-review-'))throw Error('Task style ID required');
      if(state.styles[sid]&&state.styles[sid].taskID!==d.taskID)throw Error('Style owned by another task');
      if(d.policy?.bilingual&&Object.values(d.policy.english_entries||{}).some(x=>x.style_fingerprint!==d.policy.style_fingerprint))throw Error('English entry style fingerprint mismatch');
      state.styles[sid]={taskID:d.taskID,policy:d.policy||{},xml};
      const installed=Zotero.Styles.get(sid);if(!installed||installed.getXML()!==xml)await Zotero.Styles.install(xml,null,true);
      for(const s of Object.values(Zotero.Integration.sessions))if(s.data?.style?.styleID===sid)await s.setData(s.data,true);
      return {ok:true,styleID:sid};
    }
    if(d.op==='renderEnglish'){
      const record=state.styles[d.styleID];if(!record||record.taskID!==d.taskID)throw Error('Style outside task');
      if(d.sourceKey)await item(t,d.sourceKey);
      const policy={...record.policy,url_required:!!(d.sourceKey&&record.policy.url_required_item_keys?.includes(d.sourceKey))};
      return {ok:true,...await CodexReviewCSL.renderEnglish(d.xml||record.xml,d.englishCSL,policy)};
    }
    if(d.op==='renderBibliography'){
      const record=state.styles[d.styleID];if(!record||record.taskID!==d.taskID)throw Error('Style outside task');
      if(!Array.isArray(d.keys)||!d.keys.length||d.keys.length>500)throw Error('Item keys required');
      const items=[];for(const key of d.keys){const it=await item(t,key);if(!it.isRegularItem())throw Error('Bibliography needs regular items');items.push(it);}
      if(d.format&&!['text','html'].includes(d.format))throw Error('Unsupported bibliography format');
      await Zotero.Styles.init();const engine=Zotero.Styles.get(d.styleID).getCiteProc('zh-CN',d.format||'text');
      try{engine.updateItems(items.map(x=>x.id));const bib=engine.makeBibliography();if(!bib)throw Error('Empty bibliography');return {ok:true,params:bib[0],entries:bib[1]};}
      finally{engine.free();}
    }
    if(['configureDocument','insertNote','addBibliography','refreshDocument'].includes(d.op)){
      const path=scopedPath(t,d.documentPath,'document');
      if(d.op==='configureDocument'&&(state.styles[d.styleID]?.taskID!==d.taskID||!['resource_only','all'].includes(state.styles[d.styleID]?.policy?.url_policy)))throw Error('Install a task style with an explicit URL policy first');
      const job=reviewBridge.active={...d,task:t};
      try{await Zotero.Integration.execCommand('WinWord',command,path,1);return job.result||{ok:false,error:'Integration command did not run'};}
      finally{if(job.session)job.session._dontActivateDocument=job.oldActivate;reviewBridge.active=null;}
    }
    throw Error('Unknown operation');
  }
  function Endpoint(){}
  Endpoint.prototype={supportedMethods:['POST'],supportedDataTypes:['application/json'],permitBookmarklet:false,
    async init(request){
      if(request.headers.authorization!=='Bearer '+config.token)return [403,'application/json','{"ok":false,"error":"unauthorized"}'];
      const d=request.data||{};
      const reply=x=>[200,'application/json',JSON.stringify(x)];
      if(d.op==='health')return reply({ok:true,version:'0.2.1',zotero:Zotero.version,busy:reviewBridge.busy,capabilities:['tasks','snapshot','native-word','style-policy','resource-url-policy']});
      if(d.op==='status')return reply(state.ledger[d.requestID]||{state:'unknown'});
      if(!/^[a-zA-Z0-9][a-zA-Z0-9_-]{7,119}$/.test(d.requestID||''))return [400,'application/json','{"error":"requestID required"}'];
      const signature=await hash(d),old=state.ledger[d.requestID];
      if(old){if(old.signature!==signature)return [409,'application/json','{"error":"requestID payload conflict"}'];return reply(old);}
      if(reviewBridge.busy||Zotero.Integration.currentDoc)return [409,'application/json','{"error":"busy"}'];
      reviewBridge.busy=true;
      const row=state.ledger[d.requestID]={state:'started',signature,op:d.op,taskID:d.taskID};
      try{await persist();row.result=await dispatch(d);row.state=row.result.ok?'completed':'failed';}
      catch(e){row.state='failed';row.result={ok:false,error:String(e)};}
      finally{try{await persist();}finally{reviewBridge.busy=false;}}
      return reply(row);
    }
  };
  Zotero.Server.Endpoints[route]=Endpoint;
}
function shutdown(){
  if(!reviewBridge)return;
  delete Zotero.Server.Endpoints['/codex-bridge/v1'];
  delete Zotero.Integration.Interface.prototype.codexReviewCommand;
  reviewBridge.uninstallCSL?.();reviewBridge=null;
}
