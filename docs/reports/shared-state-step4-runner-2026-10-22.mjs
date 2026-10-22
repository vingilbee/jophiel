// Explicit live E2E, not a mocked CI test. Run with local API, frontend, chat and coordinator workers.
import { chromium } from '../../frontend/node_modules/@playwright/test/index.mjs';
import { readFile,writeFile } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
const fixture=process.env.E2E_FIXTURE || 'clean';
const root=new URL('./',import.meta.url);
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage();
const evidence={fixture,started:new Date().toISOString(),turns:[],errors:[],outOfTurnFrames:[]};
let current;
page.on('pageerror',e=>evidence.errors.push(String(e)));
page.on('websocket',ws=>{
 ws.on('framesent',({payload})=>{let f;try{f=JSON.parse(String(payload))}catch{return}if(f.type==='run'&&current)current.sent=f});
 ws.on('framereceived',({payload})=>{
  if(typeof payload!=='string')return;let f;try{f=JSON.parse(payload)}catch{return}
  if(!current){if(['nudge_start','token','done','route'].includes(f.type))evidence.outOfTurnFrames.push(f);return;}
  if(f.type==='token'){current.firstTokenMs??=Date.now()-current.startMs;current.reply+=f.content;current.tokenFrames++}
  else if(['ack','meta','route','done','stopped','error','nudge_start'].includes(f.type)){
   current.frames.push(f);if(f.conversation_id)current.conversationId=f.conversation_id;
   if(['done','stopped','error'].includes(f.type))current.terminal=f.type;
  }
 });
});
const save=()=>writeFile(new URL(`shared-state-step4-${fixture}-2026-10-22.json`,root),JSON.stringify(evidence,null,2));
async function turn(label,message,expected,{fresh=false,wait=false}={}){
 if(fresh)await page.getByRole('button',{name:'New chat',exact:true}).click();
 current={label,message,expected,startMs:Date.now(),reply:'',tokenFrames:0,frames:[]};evidence.turns.push(current);
 await page.getByRole('textbox',{name:'Message Columbina',exact:true}).fill(message);
 await page.getByRole('button',{name:'Send',exact:true}).click();
 const deadline=Date.now()+120000;while(!current.terminal&&Date.now()<deadline)await page.waitForTimeout(250);
 current.immediateMs=Date.now()-current.startMs;
 if(!current.terminal)throw Error(`Turn timed out: ${label}`);
 current.body=await page.locator('body').innerText();
 current.renderedTextVisible=current.body.includes(current.reply.replace(/\[\[sticker:[a-z0-9-]+\]\]/g,'').trim());
 const route=current.frames.find(f=>f.type==='route');
 console.log(JSON.stringify({label,expected,route,reply:current.reply,firstTokenMs:current.firstTokenMs,immediateMs:current.immediateMs}));
 await save();
 const args=['run','python','../docs/reports/shared-state-step4-observe-2026-10-22.py',current.sent.turn_id];
 if(wait)args.push('--wait');
 current.observed=JSON.parse(execFileSync('/Users/vincentgilbert/.local/bin/uv',args,{cwd:new URL('../../backend/',root).pathname,encoding:'utf8',timeout:110000,maxBuffer:4e6}));
 current.afterWorkBody=await page.locator('body').innerText();
 const assistants=current.observed.messages.filter(m=>m.role==='assistant');
 current.transportPass=current.terminal==='done'&&current.renderedTextVisible&&assistants.length===1&&assistants[0].content===current.reply&&current.observed.jobs.length===1&&current.observed.jobs[0].status==='completed';
 const trace=current.observed.traces.find(r=>r.event);
 current.actual=trace?{assessment:trace.assessment,route:trace.route,recall:trace.recall,calls:trace.calls,dependencies:trace.dependencies}:null;
 current.processingMatch=trace?.assessment?.recall===expected.recall&&trace?.assessment?.thinking===expected.thinking;
 console.log(JSON.stringify({label,processingMatch:current.processingMatch,transportPass:current.transportPass,actual:current.actual,work:current.observed.work.map(w=>({kind:w.kind,status:w.status,error:w.error_code})),outputs:current.observed.outputs.map(o=>({phase:o.phase,decision:o.decision,status:o.status}))}));
 if(wait||fresh)await page.screenshot({path:new URL(`shared-state-step4-${fixture}-${label}-2026-10-22.png`,root).pathname,fullPage:true});
 await save();current=undefined;
}
const neither={recall:'not_required',thinking:'not_required'},recall={recall:'required',thinking:'not_required'},mind={recall:'not_required',thinking:'required'},both={recall:'required',thinking:'required'};
try{
 if(fixture==='synthetic' || fixture==='remaining' || fixture==='cacheprobe'){
  await context.addCookies(JSON.parse(await readFile('/tmp/shared-state-step4-clean-cookie.json','utf8')));
  await page.goto('http://127.0.0.1:5173/chat');
 }else{
  await page.goto('http://127.0.0.1:5173/login?mode=signup');
  await page.getByLabel('Name',{exact:true}).fill('Step Four E2E');
  await page.getByLabel('Email',{exact:true}).fill(`step-four-${Date.now()}@example.com`);
  await page.getByLabel('Password',{exact:true}).fill(randomBytes(18).toString('hex'));
  await page.getByRole('button',{name:'Create account',exact:true}).click();
 }
 await page.getByRole('textbox',{name:'Message Columbina',exact:true}).waitFor();
 evidence.user=await(await context.request.get('http://127.0.0.1:5173/api/auth/me')).json();
 await writeFile('/tmp/shared-state-step4-clean-cookie.json',JSON.stringify(await context.cookies()),{mode:0o600});
 if(fixture==='cacheprobe'){
  await turn('long_answer_setup','Write a gentle fictional paragraph of at least 180 words about rain falling over an empty harbor. After that paragraph, finish with one separate sentence giving the saved day, time and place of my Cobalt reading circle. Keep all meeting details out of the rain paragraph; put them only in the final sentence.',recall,{fresh:true});
  await turn('cache_beyond_clipped_context','What day, time and place is my Cobalt reading circle?',neither);
 }else if(fixture==='remaining'){
  await turn('decision_thinking','I need to choose between two venues for a 30-person workshop. A costs $200 and fits 40 but is 45 minutes away. B costs $350 and fits 30 but is 10 minutes away. My budget is $400 and several attendees cannot drive. Weigh the tradeoffs, identify any missing constraints, and recommend a defensible choice.',mind,{fresh:true,wait:true});
  await turn('synthetic_teaching','Please remember these fictional QA facts for our test story: my Cobalt reading circle meets every Thursday at 7 pm in the Glass Pavilion. I must bring the blue atlas to its next meeting. These are invented test details, not real personal information.',neither,{fresh:true});
 }else if(fixture==='synthetic'){
  await turn('uncached_history','What time and where does my Cobalt reading circle meet?',recall,{fresh:true});
  await turn('cached_followup','And what day is that meeting?',neither);
  await turn('distractor_one','New subject for a moment: I like rainy evenings.',neither);
  await turn('distractor_two','I also like hearing a train in the distance at night.',neither);
  await turn('distractor_three','A warm lamp by the window makes the room feel cozy.',neither);
  await turn('cache_without_recent_answer','Where does my Cobalt reading circle meet, and at what time?',neither);
  await turn('recall_and_thinking','Think carefully about my Cobalt reading circle meeting and the item I said I must bring. Look back at what I told you before, then work out a sensible preparation plan that fits the saved meeting time and place.',both,{fresh:true,wait:true});
  await turn('unknown_history','What was the name of my first childhood pet? I have not given it in this chat; check what you remember instead of guessing.',recall,{fresh:true});
 }else{
  await turn('greeting','Hi Columbina. Just stopping by to say hello.',neither);
  await turn('general_knowledge','What is the capital of France?',neither);
  await turn('context_setup','For a fictional story, my lighthouse is named Silver Wren. I chose it because the name feels lively.',neither);
  await turn('context_followup','What did I name the lighthouse, and why?',neither);
  await turn('context_correction','I changed my mind: the lighthouse is called Quiet Harbor now.',neither);
  await turn('corrected_followup','What is its name now?',neither);
  await turn('ambiguous_referent','Could you send it to her?',neither,{fresh:true});
  await turn('explicit_thinking','Please think carefully before answering this logic problem. Three boxes are labelled APPLES, ORANGES and MIXED. Every label is wrong. You can draw one fruit from only one box. Which box should you choose, and how can you then identify all three boxes?',mind,{fresh:true,wait:true});
  await turn('decision_thinking','I need to choose between two venues for a 30-person workshop. A costs $200 and fits 40 but is 45 minutes away. B costs $350 and fits 30 but is 10 minutes away. My budget is $400 and several attendees cannot drive. Weigh the tradeoffs, identify any missing constraints, and recommend a defensible choice.',mind,{fresh:true,wait:true});
 }
 await page.screenshot({path:new URL(`shared-state-step4-${fixture}-final-2026-10-22.png`,root).pathname,fullPage:true});
}catch(e){evidence.failure=String(e);console.error(e);process.exitCode=1;await page.screenshot({path:new URL(`shared-state-step4-${fixture}-failure-2026-10-22.png`,root).pathname,fullPage:true})}
finally{evidence.finished=new Date().toISOString();await save();await browser.close()}
