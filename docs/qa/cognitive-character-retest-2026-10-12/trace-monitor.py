from http.server import HTTPServer, BaseHTTPRequestHandler
import psycopg, time, html, json
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  with psycopg.connect(host='127.0.0.1',port=55432,user='postgres',password='qa-disposable-only',dbname='cognitive_character_1012',row_factory=psycopg.rows.dict_row) as c:
   deadline=time.monotonic()+50
   while time.monotonic()<deadline:
    row=c.execute("select conversation_id,turn_id,content from chat_messages where role='user' order by created_at desc limit 1").fetchone()
    trace=c.execute('select * from cognitive_shadow_traces where turn_id=%s',(row['turn_id'],)).fetchone() if row else None
    if trace: break
    c.commit();time.sleep(.25)
   if trace:
    r=trace.get('realization_trace_payload') or {}
    result={'conversation_id':str(row['conversation_id']),'turn_id':str(row['turn_id']),'question':row['content'],'status':trace['terminal_status'],'realization_status':trace['realization_status'],'shadow':(r.get('final_candidate') or {}).get('text'),'failure':(trace.get('failure_payload') or {}).get('code'),'character_present':bool((trace.get('compiled_request_payload') or {}).get('character')),'contribution_versions':[x['payload'].get('schema_version') for x in (trace.get('pipeline_trace_payload') or {}).get('orchestration',{}).get('contributions',[])]}
   else: result={'status':'timeout'}
   body=('<html><body><h1>QA shadow trace complete</h1><pre>'+html.escape(json.dumps(result,ensure_ascii=False,indent=2))+'</pre></body></html>').encode()
   self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(body)
 def log_message(self,*args): pass
HTTPServer(('127.0.0.1',58002),Handler).serve_forever()
