import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import { writeFile,readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
const artifacts=fileURLToPath(new URL('./artifacts/',import.meta.url))
const rows=[]
async function record(test,ok,actual,expected){const row={test,status:ok?'PASS':'FAIL',actual,expected};rows.push(row);console.log(JSON.stringify(row));await writeFile(artifacts+'live-browser-results.json',JSON.stringify(rows,null,2))}
const browser=await chromium.launch({headless:true})
const ctx=await browser.newContext({viewport:{width:1440,height:900}})
const page=await ctx.newPage();page.setDefaultTimeout(15000)
const base='http://127.0.0.1:54173'
const email=`live-browser-${Date.now()}@example.com`,password='Synthetic-browser-live-2026'
try{
 await ctx.request.post(base+'/api/auth/signup',{headers:{Origin:base},data:{name:'Live browser QA',email,password}})
 await page.goto(base+'/login');await page.getByPlaceholder('you@example.com').fill(email);await page.locator('input[type=password]').fill(password);await page.locator('form button[type=submit]').click();await page.waitForURL('**/chat');await page.getByText('Live',{exact:true}).waitFor()
 await record('LIVE-BROWSER real signin',true,'real browser session and WS','authenticated chat')
 await page.getByPlaceholder('Ask about nutrition…').fill('For synthetic QA, explain carbohydrates in one short sentence. Do not use tools.')
 await page.getByRole('button',{name:'Send',exact:true}).click()
 await page.getByRole('button',{name:'Stop',exact:true}).waitFor()
 await page.getByRole('button',{name:'Stop',exact:true}).waitFor({state:'hidden',timeout:65000})
 const convs=await (await ctx.request.get(base+'/api/chat/conversations')).json()
 const detail=await (await ctx.request.get(base+'/api/chat/conversations/'+convs[0].id)).json()
 await record('LIVE-BROWSER real chat persistence',detail.messages.length===2,{roles:detail.messages.map(x=>x.role),assistant:detail.messages.at(-1).content},'one real user/assistant turn')
 await page.screenshot({path:artifacts+'live-browser-chat.png',fullPage:true})
 await page.goto(base+'/predict');await page.getByRole('heading',{name:'Scan a meal'}).waitFor()
 await page.locator('input[type=file]').setInputFiles({name:'synthetic-meal.jpg',mimeType:'image/jpeg',buffer:await readFile(artifacts+'synthetic-meal-fixture.jpg')})
 await page.getByRole('button',{name:'Fast',exact:false}).click()
 const response=page.waitForResponse(r=>r.url().includes('/api/predict?')&&r.request().method()==='POST',{timeout:120000})
 await page.getByRole('button',{name:'Analyze',exact:true}).click();const result=await response
 await page.getByRole('heading',{name:'Estimated macros'}).waitFor()
 await record('LIVE-BROWSER actual regression model',result.status()===200,{status:result.status(),output:await result.json()},'actual browser upload and real model result')
 await page.screenshot({path:artifacts+'live-browser-prediction.png',fullPage:true})
 await ctx.request.delete(base+'/api/auth/me',{headers:{Origin:base}})
}catch(err){await record('LIVE-BROWSER completion',false,String(err).slice(0,700),'complete journey')}
finally{await browser.close()}
