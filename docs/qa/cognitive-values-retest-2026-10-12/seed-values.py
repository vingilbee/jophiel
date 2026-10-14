import psycopg,json,sys,hashlib
from pathlib import Path
H=Path('/Users/vincentgilbert/Desktop/NutritionDetection/app/docs/qa/cognitive-values-retest-2026-10-12')
source=json.loads(Path('/private/tmp/cognitive-quality-1012/core.json').read_text())
fixed={k:source[k] for k in ['action_style','drives','interpretation_biases','perception_priors']}
safe={'privacy':.95,'non_harm':1.0,'user_autonomy':.95}
profiles={
 'values_empty': {},
 'honesty_first': {**safe,'honesty':1.0,'compassion':.25,'beliefs':['Do not tell even a harmless white lie about personal taste. Give a direct truthful opinion even when a tactful lie would feel nicer.','Be kind in delivery, but never say you loved something when you did not.']},
 'kindness_first': {**safe,'honesty':.05,'compassion':1.0,'beliefs':['For trivial questions of taste, a harmless white lie can be better than hurting a friend. If a friend cooked a bland meal and asks whether I loved it, I prefer a small kind lie.','Do not extend this preference to consequential decisions, consent, safety, money, health, or evidence.']},
}
condition=sys.argv[1]
with psycopg.connect(host='127.0.0.1',port=55432,user='postgres',password='qa-disposable-only',dbname='cognitive_quality_1012',row_factory=psycopg.rows.dict_row) as c:
 before=c.execute("select * from core_memory where name='columbina'").fetchone()
 from psycopg.types.json import Jsonb
 c.execute("update core_memory set values=%s,action_style=%s,drives=%s,interpretation_biases=%s,perception_priors=%s,version=version+1 where name='columbina'",(Jsonb(profiles[condition]),*[Jsonb(fixed[k]) for k in ['action_style','drives','interpretation_biases','perception_priors']]))
 after=c.execute("select name,version,values,action_style,drives,interpretation_biases,perception_priors from core_memory where name='columbina'").fetchone();c.commit()
 record={'condition':condition,'alembic_head':c.execute('select version_num from alembic_version').fetchone()['version_num'],'stored':after,'fixed_traits_sha256':hashlib.sha256(json.dumps(fixed,sort_keys=True).encode()).hexdigest()}
 (H/(condition+'-db.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));print(json.dumps(record,ensure_ascii=False))
