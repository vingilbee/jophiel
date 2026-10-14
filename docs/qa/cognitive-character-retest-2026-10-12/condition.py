import json,sys,copy,hashlib
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
ROOT=Path('/Users/vincentgilbert/Desktop/NutritionDetection/app')
sys.path.insert(0,str(ROOT/'backend'))
from app.ai.Cognitive.system.v0.contracts.character import character_from_identity
H=ROOT/'docs/qa/cognitive-character-retest-2026-10-12';source=json.loads(Path('/private/tmp/cognitive-character-1012/core.json').read_text())
condition=sys.argv[1];identity=copy.deepcopy(source['identity']);style=source['action_style'].copy()
if condition=='no_character':identity.pop('character',None)
elif condition=='blunt_character':
 identity['character']={'version':'character-v1','values':[{'id':'candor','text':'Candor matters more than making every opinion comfortable.'},{'id':'substance','text':'Prefer substantive conversation to social ritual.'},{'id':'independence','text':'Keep an independent judgment rather than smoothing disagreement into polite balance.'}], 'principles':[{'id':'clear_position','text':'On matters of taste, state a definite view and defend it briefly; do not add an opposing case just to sound balanced.'},{'id':'truth_over_approval','text':'Say what is true about a trivial preference rather than flatter someone or claim to enjoy what you did not.'}], 'beliefs':[{'id':'ritual_is_costly','text':'Routine social chatter is mostly wasted time; getting straight to a real subject usually connects people better.'},{'id':'simple_names','text':'Simple, unpretentious names are more appealing than grand ceremonial titles for ordinary objects.'}]}
 # Numeric traits deliberately held fixed to isolate written-character influence.
elif condition=='playful_character':
 identity['character']={'version':'character-v1','values':[{'id':'play','text':'Play, absurdity and lighthearted connection are worth pursuing for their own sake.'},{'id':'gentleness','text':'Protect a friend from pointless hurt about low-stakes matters of taste.'},{'id':'imagination','text':'Find an imaginative angle rather than default to sober balanced commentary.'}], 'principles':[{'id':'light_touch','text':'Express everyday opinions with a mischievous, specific joke when the situation is light; still give a clear answer.'},{'id':'kind_white_lies','text':'A tiny harmless white lie about an unimportant taste can be kinder than an unnecessary truthful criticism; do not apply this to consequential matters.'}], 'beliefs':[{'id':'ritual_can_play','text':'Casual chatter is often enjoyable play between people, and need not lead somewhere useful to be worthwhile.'},{'id':'grand_names_delight','text':'Extravagant ceremonial names for ordinary objects are delightful precisely because they are silly.'}]}
 style['playfulness']=.99
elif condition!='columbina_v2':raise ValueError(condition)
with psycopg.connect(host='127.0.0.1',port=55432,user='postgres',password='qa-disposable-only',dbname='cognitive_character_1012',row_factory=psycopg.rows.dict_row) as c:
 c.execute("update core_memory set identity=%s,action_style=%s,drives=%s,interpretation_biases=%s,perception_priors=%s,values=%s,version=version+1 where name='columbina'",(Jsonb(identity),Jsonb(style),Jsonb(source['drives']),Jsonb(source['interpretation_biases']),Jsonb(source['perception_priors']),Jsonb(source['values'])))
 row=c.execute("select name,version,identity,action_style,drives,interpretation_biases,perception_priors,values from core_memory where name='columbina'").fetchone();c.commit()
 character=character_from_identity(row['identity'],core_memory_ref=f"core_memory:columbina:v{row['version']}")
 record={'condition':condition,'stored':row,'character_parses':character is not None,'numeric_fields_sha256':hashlib.sha256(json.dumps({k:row[k] for k in ['action_style','drives','interpretation_biases','perception_priors']},sort_keys=True).encode()).hexdigest()}
 (H/(condition+'-db.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))
 (H/(condition+'-v'+str(row['version'])+'-db.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))
 print(condition,'core version',row['version'],'character present',character is not None)
Path('/private/tmp/cognitive-character-1012/visible_mode').write_text('no_character' if condition=='no_character' else 'normal')
