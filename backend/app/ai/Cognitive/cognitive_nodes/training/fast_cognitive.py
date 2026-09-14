from typing import Optional, List, Dict , Any
from pydantic import BaseModel
import re
import json
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer, CountVectorizer
from sklearn.preprocessing import OneHotEncoder, FunctionTransformer, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.naive_bayes import MultinomialNB, ComplementNB
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.calibration import CalibratedClassifierCV
import pandas as pd
import joblib

from .....states.memory import Perception, SpeechType

POLITENESS_WORDS = {"please", "thanks", "thank you", "appreciate"}
SELF_REF_PATTERN = re.compile(r"\bI\b|\bme\b|\bmy\b|\bmine\b", re.I)
QUESTION_PATTERN = re.compile(r"\?\s*$|^wh\w|\bwhat\b|\bhow\b|\bwhy\b|\bwhere\b|\bwho\b", re.I)
DIRECTIVE_PATTERN = re.compile(r'^\s*(please\s+)?(do|send|open|give|show|create|run)\b', re.I)

def extract_simple_features(per: Perception, text: str):
    politeness_count = sum(1 for w in POLITENESS_WORDS if w in text.lower())
    has_self_ref = bool(SELF_REF_PATTERN.search(text))
    is_question = bool(QUESTION_PATTERN.search(text)) or (per['speech_act_type'] == "Question")
    is_directive = bool(DIRECTIVE_PATTERN.search(text)) or bool(per['directives'])
    exclamation_count = text.count("!")
    uppercase_ratio = sum(1 for c in text if c.isupper()) / max (1, len(text))
    length = len(text.split())

    ev = per['emotion_valence_observed'] if per['emotion_valence_observed'] is not None else 0

    return {
        "politeness_count": politeness_count,
        "has_self_ref": int(has_self_ref),
        "is_question": int(is_question),
        "is_directive": int(is_directive),
        "exclamation_count": exclamation_count,
        "uppercase_ratio": uppercase_ratio,
        "length": length,
        "emotion_valence": ev,
        "salience_score": per['salience_score'],
        "ambiguity_flag": int(per['ambiguity_flag']),
        "speech_act_type": per['speech_act_type']
    }

def build_training_frame(perceptions: list[Perception], text:list[str], labels: list[str]) -> pd.DataFrame:
    rows = []
    for p, y, t in zip(perceptions, labels, text):
        features = extract_simple_features(p, t)
        rows.append({
            "user_message": t,
            "emotion_words": p['emotion_words'],
            "politeness": features['politeness_count'],
            "self_ref": features['has_self_ref'],
            "salience": p['salience_score'],
            "ambiguity": features['ambiguity_flag'],
            "speech_act_type": p["speech_act_type"],
            "label": y.replace("\n", "")
        })
    return pd.DataFrame(rows)

text_vectorizer = TfidfVectorizer(
    ngram_range=(1, 2),
    max_features=3000,
    sublinear_tf=True
)

emotion_vectorizer = CountVectorizer(max_features=500)
# num_scaler = StandardScaler()
speech_act_encoder = OneHotEncoder(handle_unknown="ignore")
preprocessor = ColumnTransformer(
    transformers=[
        ("msg", text_vectorizer, "user_message"),
        ("emo", emotion_vectorizer, "emotion_words"),
        ("num", "passthrough", ["politeness", "self_ref", "salience", "ambiguity"]),
        ("speech", speech_act_encoder, ["speech_act_type"])
    ]
)

intent_model = Pipeline(steps=[
    ("preprocess", preprocessor),
    ("clf", MultinomialNB(alpha=0.5))
])

def fit(perceptions: list[Perception], texts: list[str], labels: list[str]):
    df = build_training_frame(perceptions=perceptions, text=texts, labels=labels)
    X_train = df.drop(columns=["label"])
    y_train = df["label"]
    intent_model.fit(X_train, y_train)

############################################### CREATE DATASET ##################################

from src.ai.model.open_ai import OpenAIGenerator
from .....states.memory import MemoryState

COGNITIVE_SYSTEM= """
 - You are a cognitive submodule, not an assistant.
 - You do not explain your output
 - You do not add opinions.
 - if uncertain , use null or low confidence
"""

PERCEPTION_PROMPT = """
ROLE: PERCEPTION_ENCODER
You are a perception module.
Extract Only observable social signal only.
    - DO NOT infer intent.
    - DO NOT assume meaning
    - DO NOT evaluate correctness
    - DO NOT attach emotion causes

Input is list of user message that forms a conversation , for each message you have to Perceive and extract observable social
Input
conversations: 
{stm_memory}

perception_weights:
{perception_weights}

You are given a perception_weights , you have to follow this to extract the observable social signal
"""

class Perceptions(BaseModel):
    perceptions: list[Perception]

    class Config:
        use_enum_values = True

async def generate_perception(texts:list[str]):
    weights = {"humor_cues": 0.2, "threat_cues": 0.2, "emotional_tone": 0.1, "self_reference": 0.7, "verbal_content": 0.2, "politeness_markers": 0.7, "incosistency_detection": 0.3}
    cognitive_prompt = PERCEPTION_PROMPT.format(
        stm_memory=texts,
        perception_weights=weights
    )
    llm = OpenAIGenerator.with_structured_output(Perceptions)
    result = llm.invoke(COGNITIVE_SYSTEM + cognitive_prompt)
    return result


import asyncio



async def main():
    # conversations = []
    # perceptions = []
    # label = []
    # with open ("src/ai/graph/nodes/cognitive_nodes/perceptions.jsonl", "r") as f:
    #     for line in f.readlines():
    #         data = json.loads(line)
    #         perception = Perception(
    #             emotion_valence_observed=data['emotion_valence_observed'],
    #             emotion_words=data['emotion_words'],
    #             politeness_markers=data['politeness_markers'],
    #             directives=data['directives'],
    #             questions=data['questions'],
    #             self_references=data['self_references'],
    #             speech_act_type=data['speech_act_type'],
    #             ambiguity_flag=data['ambiguity_flag'],
    #             salience_score=data['salience_score'],
    #         )
    #         perceptions.append(perception)

    # with open ("src/ai/graph/nodes/cognitive_nodes/human_chat.txt", "r") as f:
    #     for line in f.readlines():
    #         text = line.replace("Human 1: ", "")
    #         text = text.replace("Human 2: ", "")
    #         conversations.append(text)

    # with open("src/ai/graph/nodes/cognitive_nodes/label.txt", "r") as f:
    #     for line in f.readlines():
    #         label.append(line)

    # fit(perceptions=perceptions, texts=conversations[:160], labels=label)
    # joblib.dump(intent_model, "intent_model.joblib")
    ####### INFERENCE
    model = joblib.load("src/ai/graph/nodes/cognitive_nodes/intent_model.joblib")
    data = [{
            "user_message": "Hi, how are you today",
            "emotion_words": "",
            "politeness": 0,
            "self_ref": 0,
            "salience": 0.2,
            "ambiguity": 0,
            "speech_act_type": "statement"
        }]
    X_test = pd.DataFrame(data)
    print(model.classes_)
    print(model.predict_proba(X_test))
    # try:
        # for i in range(170, len(conversations[170:]), 10):
        #     batch = conversations[i:i+10]
        #     peps = await generate_perception(batch)
        #     perceptions.perceptions.extend(peps.perceptions)
        #     print("Iteration", i)

    # except Exception as e:
    #     print("Error", e)
    # finally:
    #     with open("src/ai/graph/nodes/cognitive_nodes/perceptions.jsonl", "w") as t:
    #         for p in perceptions.perceptions:
    #             d = json.dumps(p)
    #             t.write(d + "\n")

asyncio.run(main())