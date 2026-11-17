'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

class Element{
 constructor(tag='div'){this.tag=tag;this.dataset={};this.children=[];this.listeners={};this.hidden=false;this.className='';this._text='';this.value='';this.parent=null;this.classList={add:name=>this.className+=' '+name,remove:name=>this.className=this.className.split(/\s+/).filter(x=>x!==name).join(''),contains:name=>this.className.split(/\s+/).includes(name)};}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
 append(...nodes){for(const node of nodes){if(node.tag==='#fragment'){this.append(...node.children);continue;}this.children.push(node);node.parent=this;}}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 addEventListener(name,callback){this.listeners[name]=callback;}
 closest(selector){for(let n=this;n;n=n.parent)if(selector==='.convo'&&n.classList.contains('convo'))return n;return null;}
 contains(target){for(let n=target;n;n=n.parent)if(n===this)return true;return false;}
}
const nodes=new Map();
const document={
 getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},
 createElement(tag){return new Element(tag);},
 createDocumentFragment(){return new Element('#fragment');},
 querySelectorAll(selector){
  if(selector==='.convo.active')return (nodes.get('convos')?.children||[]).filter(n=>n.classList.contains('active'));
  return [];
 }
};
const html=fs.readFileSync(path.join(__dirname,'..','viewer.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script,'local viewer JavaScript must be present');
const fixture={userId:1,people:{'1':{name:'Me'},'2':{name:'Alice'}},conversations:{
 first:{title:'First group',messages:[{senderTargetId:2,content:'A hidden keyword',sent:'2026-01-01'}]},
 second:{title:'Second group',messages:[{senderTargetId:1,content:'Another note',sent:'2026-01-02'}]}
}};
const alerts=[];
class FileReader{readAsText(file){this.result=file.text;this.onload();}}
const context=vm.createContext({document,window:{innerWidth:1200},FileReader,alert(message){alerts.push(message);},console,fixture});
vm.runInContext(script,context,{filename:'viewer.html'});
vm.runInContext('archive=fixture;conversationEntries=Object.entries(archive.conversations);',context);
context.buildConversationList();
const root=document.getElementById('convos');
assert.equal(root.children.length,3,'two conversations and the empty state are created once');
const [first,second,empty]=root.children;
assert.ok(!first.listeners.click&&!second.listeners.click,'individual rows must not allocate listeners');
assert.equal(typeof root.listeners.click,'function','event delegation must handle conversation clicks');

context.renderConversationList('hidden KEYword');
assert.equal(first.hidden,false,'message previews remain searchable without a rebuild');
assert.equal(second.hidden,true);
assert.equal(empty.hidden,true);
context.renderConversationList('SECOND');
assert.equal(first.hidden,true);
assert.equal(second.hidden,false,'titles are searched case-insensitively');
context.renderConversationList('missing');
assert.equal(first.hidden,true);
assert.equal(second.hidden,true);
assert.equal(empty.hidden,false,'empty search result remains visible');

context.renderConversationList();
assert.equal(root.children[0],first,'filtering must reuse existing DOM nodes');
root.listeners.click({target:first.children[0]});
assert.match(document.getElementById('chatTitle').textContent,/First group/,'delegated click opens the correct conversation');
assert.ok(first.classList.contains('active'));
assert.match(html,/\.archive-warning\{[^}]*position:sticky/, 'incomplete-archive warning must remain visible while scrolling');
vm.runInContext('archive.conversations.first._archiveStatus="partial-error"',context);
context.openConversation('first',first);
assert.equal(document.getElementById('messages').children[0].className,'archive-warning');
context.renderConversationList('second');
context.renderConversationList();
assert.ok(first.classList.contains('active'),'search preserves the current selection');
context.renderConversationList('missing');
context.buildConversationList();
context.renderConversationList();
assert.equal(root.children.length,3,'loading another archive replaces old rows instead of accumulating them');
const title=vm.runInContext('archive.conversations.first.title',context);
for(const invalid of [
 {people:[],conversations:{}},
 {people:{},conversations:{bad:null}},
 {people:{},conversations:'not a conversation collection'},
 {people:{},conversations:{bad:{messages:[null]}}},
 {people:{},conversations:{bad:{messages:{id:3}}}}
]){
 context.loadArchive({text:JSON.stringify(invalid)});
 assert.equal(vm.runInContext('archive.conversations.first.title',context),title,'invalid imports must not discard the current archive');
 assert.equal(root.children[0].dataset.id,'first','invalid imports must not replace visible conversation rows');
}
assert.equal(alerts.length,5,'invalid archive imports must report failure');
context.loadArchive({text:JSON.stringify({userId:1,people:{'1':{name:'Me'}},conversations:{third:{title:'New archive',messages:[]}},archiveMeta:{conversationListIncomplete:true}})});
assert.match(document.getElementById('stats').textContent,/Incomplete conversation list/,'partial conversation archives need a visible warning');
assert.equal(root.children.length,2,'valid replacement archive resets the visible conversation list');
assert.equal(root.children[0].dataset.id,'third');
assert.equal(vm.runInContext('archive.conversations.third.title',context),'New archive');
const dated={sent:'2026-01-01T00:00:00Z'};
const stamp=context.messageTime(dated);
assert.equal(stamp,Date.parse(dated.sent),'message timestamps must retain their ordering');
dated.sent='2029-01-01T00:00:00Z';
assert.equal(context.messageTime(dated),stamp,'timestamp parsing should be cached per immutable archive message');
context.loadArchive({text:JSON.stringify({userId:1,people:{'1':{name:'Me'}},conversations:{
 big:{title:'Out-of-order',messages:[{content:'later',sent:'2026-02-01'},{content:'earlier',sent:'2026-01-01'}]}
}})});
context.openConversation('big',root.children[0]);
assert.equal(document.getElementById('messages').children[0].children[1].textContent,'earlier');
console.log('PASS: viewer search reuses DOM, preserves selection and delegates clicks; invalid imports are atomic; timestamps are cached');
