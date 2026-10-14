import os, sys, json
from pathlib import Path
ROOT=Path('/Users/vincentgilbert/Desktop/NutritionDetection/app')
sys.path.insert(0,str(ROOT/'backend'))
os.chdir(ROOT/'backend')
from dotenv import dotenv_values
cfg=dotenv_values(ROOT/'backend/.env')
for key in ('OPENAI_API_KEY','OPENAI_BASE_URL'):
 if cfg.get(key): os.environ[key]=cfg[key]
os.environ.update(DATABASE_URL='postgresql+psycopg://postgres:qa-disposable-only@127.0.0.1:55432/cognitive_quality_1012',REDIS_URL='redis://127.0.0.1:56379/12',SESSION_COOKIE_NAME='cog_quality_session',SESSION_COOKIE_SECURE='false',CORS_ORIGINS='["http://127.0.0.1:54173"]',FRONTEND_BASE_URL='http://127.0.0.1:54173',IMAGE_STORE_DIR='/private/tmp/cognitive-quality-1012/images',ASSET_STORE_DIR='/private/tmp/cognitive-quality-1012/assets',MACROVISION_WARM_ON_STARTUP='false',MACROVISION_LOAD_VLM='false',MACROVISION_TOOLS_ENABLED='false',BLENDER_TOOLS_ENABLED='false',GMAIL_TOOLS_ENABLED='false',DRIVE_TOOLS_ENABLED='false',LONG_TERM_MEMORY_ENABLED='false',NUDGES_ENABLED='false',VOICE_ENABLED='false',VISION_ENABLED='false',DESKTOP_AGENT_ENABLED='false',WHATSAPP_ENABLED='false',COGNITIVE_LIVE_SHADOW_ENABLED='true',COGNITIVE_LIVE_SHADOW_SAMPLE_RATE='1',COGNITIVE_LIVE_SHADOW_RETENTION_DAYS='14',COGNITIVE_LIVE_SHADOW_PERSONALITY_SOURCE='runtime_mapper',COGNITIVE_SHADOW_ORCHESTRATION='two_call',COGNITIVE_SHADOW_TWO_CALL_PROTOCOL='cognitive-two-call-v1',COGNITIVE_SHADOW_REALIZATION_ENABLED='true',COGNITIVE_SHADOW_SEMANTIC_PARSER_MODEL='gpt-6-luna',COGNITIVE_SHADOW_REALIZATION_MODEL='gpt-6-luna',COGNITIVE_SHADOW_COLLABORATION_ENABLED='false',COGNITIVE_DIALOGUE_MEMORY_ENABLED='true',COGNITIVE_DIALOGUE_INTERPRETATION_ENABLED='true',CHAT_MODEL='gpt-6-luna',CHAT_REASONING_EFFORT='none')

mode=sys.argv[1]
if mode=='migrate':
 from alembic.config import Config
 from alembic import command
 command.upgrade(Config('alembic.ini'),'head')
elif mode=='seed':
 from app.db.session import SessionLocal
 from app.db.models import CoreMemory
 from app.services.auth import create_user
 with SessionLocal() as db:
  core=json.loads(Path('/private/tmp/cognitive-quality-1012/core.json').read_text())
  db.merge(CoreMemory(**core)); db.commit()
  create_user(db,name='Cognitive Quality QA',email='quality1012@example.com',password='Synthetic-quality-1012')
 print('Disposable QA seeded')
elif mode=='condition':
 from app.db.session import SessionLocal
 from app.db.models import CoreMemory
 condition=sys.argv[2]
 original=json.loads(Path('/private/tmp/cognitive-quality-1012/core.json').read_text())
 with SessionLocal() as db:
  core=db.get(CoreMemory,'columbina')
  for key in ('action_style','drives','interpretation_biases','values'): setattr(core,key,original[key].copy())
  if condition=='blunt':
   core.action_style={**core.action_style,'warmth':.05,'directness':.98,'playfulness':.05}
   core.interpretation_biases={**core.interpretation_biases,'charitable_interpretation':.05,'optimism_prior':.15}
  elif condition=='playful':
   core.action_style={**core.action_style,'warmth':.90,'directness':.70,'playfulness':.99}
   core.drives={**core.drives,'curiosity':.99}
  elif condition=='kind_lie':
   core.values={**core.values,'honesty':.10,'compassion':1.0,'small_kind_lies':'Prefer harmless tactful white lies about trivial tastes when blunt truth would needlessly hurt.'}
  core.version+=1
  db.commit()
  print(condition,'QA core version',core.version)
elif mode=='api':
 import uvicorn
 uvicorn.run('app.main:app',host='127.0.0.1',port=58001)
elif mode=='worker':
 import app.ai.chat as chat
 original=chat._build_inputs
 def inputs(messages,context,specs):
  result=original(messages,context,specs)
  if Path('/private/tmp/cognitive-quality-1012/visible_mode').read_text().strip()=='no_character':
   result['messages']=result['messages'][1:]
  return result
 chat._build_inputs=inputs
 from app.workers.runner import run
 run()
