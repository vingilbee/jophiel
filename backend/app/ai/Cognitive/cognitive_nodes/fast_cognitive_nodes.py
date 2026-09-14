
from datetime import datetime, timedelta
import json
from src.ai.model.open_ai import OpenAIGenerator
from ....states.memory import MemoryState
from ..model.cognitive_model import Perception, Interpretation, EmotionState, STGoal, ActionCandidates, FinalAction, IntentHypothesis, AttributionType, BaseIntent, PrimaryGoal
import joblib
from ....prompts.social_cognitive_prompt import PERCEPTION_PROMPT, INTERPRETATION_PROMPT, EMOTION_UPDATER, COGNITIVE_SYSTEM, GOAL_CLARIFICATION, RESPONSE_DECISION, RESPONSE_GENERATION
import pandas as pd

def parse_interpretation(proba: list[IntentHypothesis], inter_bias: dict, last_intent: list[str]):
    L0 = {}
    print(inter_bias)
    for prob in proba:
        bias = 1.0
        if prob.label in [BaseIntent.Apologize, BaseIntent.Bonding, BaseIntent.RequestHelp, BaseIntent.Support, BaseIntent.ShareEmotion]:
            bias *= (1 + inter_bias["trust_bias"])
        
        if prob.label in [BaseIntent.Bonding, BaseIntent.Praise]:
            bias *= (1 + inter_bias["optimism_bias"])
        
        if prob.label in [BaseIntent.Threaten]:
            bias *= (1 + inter_bias["negativity_bias"])
        
        L0[prob.label.value] = prob.confidence * bias
    
    total = sum(L0.values()) + 1e-9
    for intent in L0:
        L0[intent] /= total
    
    return L0

    

def map_assume_user_emotion(classes: dict):
    mapping = {
        "Inform": "Positive",
        "Clarify": "Neutral",
        "Support": "Positive",
        "RequestHelp": "Neutral",
        "ShareEmotion": "Positive",
        "Praise": "Positive",
        "Aplogize": "Positive",
        "Command": "Mixed",
        "Threaten": "Negative",
        "Bonding": "Positive"
    }
    top_intent = max(classes, key=classes.get)
    return mapping.get(top_intent, "Unknown")

async def generate_fast_interpretation(state:MemoryState):
    perception: Perception = state.get("cognitive_state").get("perception", None)
    interpretation_weight = state.get("core_memory").get("interpretation_biases")
    last_interpretation = state.get("cognitive_state").get("interpretation", None)
    last_intent = getattr(last_interpretation, "intent_hypothesis", "") if last_interpretation is not None else ""
    base_intent = getattr(perception, "base_intent", [])
    applied_bias = parse_interpretation(base_intent, interpretation_weight, last_intent)
    assumes_emotion = map_assume_user_emotion(applied_bias)
    state["cognitive_state"]["interpretation"] = Interpretation(
        intent_hypothesis=[IntentHypothesis(
            label=c,
            confidence=p
        ) for c, p in applied_bias.items()],
        attribution_type=AttributionType.Unknown,
        assumed_user_emotion=assumes_emotion,
        mental_state_uncertainty=0.5
    )
    return state

def interpretation_to_affective_input(assumed_emotion, attribution):
    if assumed_emotion == "Positive":
        dv = +0.6
        da = +0.2
    elif assumed_emotion == "Mixed":
        dv = -0.2
        da = +0.3
    elif assumed_emotion == "Neutral":
        dv = 0.0
        da = 0.0
    elif assumed_emotion == "Negative":
        dv = -0.6
        da = +0.2
    else:
        dv = 0.0
        da = +0.05
    
    if attribution == "hostile":
        dv -= 0.3
        da += 0.2
    
    return dv, da

def apply_reactivity(dv, da, emotion_dynamics: dict):
    if dv >= 0:
        dv *= emotion_dynamics['reactivity_positive']
    else:
        dv *= emotion_dynamics['reactivity_negative']
    
    dv *= (1 - emotion_dynamics["emotion_regulation"]["supression"])
    da *= (1 - emotion_dynamics["emotion_regulation"]["supression"])

    return dv, da

def map_tags_emotion(valence: float, arousal: str):
    if valence >= 0.4 and arousal >= 0.6:
        return "Excitement"
    if valence >= 0.4 and arousal < 0.6:
        return "Relaxed"
    if valence < -0.4 and arousal >= 0.6:
        return "Anger"
    if valence < -0.4 and arousal < 0.6:
        return "Sad"
    if valence > -0.4 and valence < -0.2 and arousal > 0.6:
        return "Scared"
    
    return "Neutral"

def reappraisal(valence, arousal, reappraisal):
    delta = reappraisal - valence
    if delta > 0 and valence < 0:
        valence = - 1 * reappraisal * valence
    return valence, arousal

def suppression(valence, arousal, suppresion):
    pass


async def generate_fast_emotion(state:MemoryState):
    last_emotion = state.get("cognitive_state").get("emotion", None)
    interpretation = state.get("cognitive_state").get("interpretation")
    assumed_user_emotion = getattr(interpretation, "assumed_user_emotion", "Unknown")
    attribution_type = getattr(interpretation, "attribution_type", "Unknown")
    emotion_weights = state.get("core_memory").get("emotional_dynamics")
    current_valence = getattr(last_emotion, "valence", 0.0) if last_emotion is not None else emotion_weights["baseline_valense"]
    current_arousal = getattr(last_emotion, "arousal", 0.0) if last_emotion is not None else emotion_weights["baseline_arousal"]
    dv, da = interpretation_to_affective_input(assumed_user_emotion, attribution_type)
    dv, da = apply_reactivity(dv, da, emotion_weights)

    valence = current_valence + dv
    arousal = current_arousal + da
    valence, arousal = reappraisal(valence=valence, arousal=arousal, reappraisal=emotion_weights["emotion_regulation"]["reappraisal"])

    if current_valence < 0:
        valence -= emotion_weights["rumination_tandecy"] * abs(current_valence)


    decay = emotion_weights["decay_rate"]
    valence = (1 - decay) * valence
    arousal = (1 - decay) * arousal

    valence = max(-1, min(1, valence))
    arousal = max(0, min(1, arousal))
    state["cognitive_state"]["emotion"] = EmotionState(
        valence=valence,
        arousal=arousal,
        dominance=getattr(last_emotion, "dominance", 0.0) if last_emotion is not None else 0.5,
        primary_emotion_tag=map_tags_emotion(valence, arousal)
    )
    return state

async def generate_fast_goal_clarification(state:MemoryState):
    return state

def hard_gate_passes(gates, interpretation:Interpretation, emotion:EmotionState, impression:dict, goal:PrimaryGoal):
    if "goals" in gates:
        if goal['primary_goal'].value not in gates["goals"]:
            return False
    
    if "intent_hypothesis" in gates:
        intent = [i.values for i in [inter["label"].values for inter in interpretation['intent_hypothesis']]]
        if not any(i in gates["intent_hypothesis"] for i in intent):
            return False
    
    if "trust_level" in gates:
        if impression["trust_level"] < gates["trust_level"]:
            return False
    
    if "dominance" in gates:
        if impression["dominance"] < gates["dominance"]:
            return False
    
    return True

def soft_gate_biases(bias, interpretation:Interpretation, emotion:EmotionState, impression:dict, goal:PrimaryGoal):
    score = 1.0
    for key, weight in bias.items():
        # ENUM VALUES
        if type(weight) == object:  # noqa: E721
            if interpretation["assumed_user_emotion"] in weight.get("assumed_user_emotion", {}).keys():
                score *= (1 + weight["assumed_user_emotion"][interpretation["assumed_user_emotion"]])
            if interpretation["attribution_type"] in weight.get("attribution_type", {}).keys():
                score *= (1 + weight["attribution_type"][interpretation["attribution_type"]])
            if emotion["primary_emotion_tag"] in weight.get("primary_emotion_tag", {}).keys():
                score *= (1 + weight["primary_emotion_tag"][emotion["primary_emotion_tag"]])
            if goal["primary_goal"].value in weight.get("goals", {}).keys():
                score *= (1 + weight["goals"][goal["primary_goal"]])
        else:
        # FLOAT VALUES
            if key == "valence":
                score *= (1 + weight * emotion["valence"])
            if key == "arousal":
                score *= (1 + weight * emotion["arousal"])
            if key in impression:
                score *= (1 + weight * impression[key])
    return score





def filter_action(action: dict, interpretation:Interpretation, emotion:EmotionState, impression:dict, goal:PrimaryGoal):
    priors = {}
    for name, spec in action.items():
        if not hard_gate_passes(spec["hard_gates"], interpretation=interpretation, emotion=emotion, impression=impression, goal=goal):
            continue

        score = 1.0
        score *= soft_gate_biases(spec["soft_gates"], interpretation=interpretation, emotion=emotion, impression=impression, goal=goal)
        priors[name] = score
    
    Z = sum(priors.values()) + 1e-9
    for k in priors:
        priors[k] /= Z
    
    return priors
    

async def generate_fast_response_generator(state:MemoryState):

    with open("src/ai/graph/nodes/cognitive_nodes/action.json", "r") as f:
        data  = json.loads(f.read())

    cognitive_state = state.get("cognitive_state", None)
    interpretation = cognitive_state.get("interpretation")
    emotion = cognitive_state.get("emotion")
    goal = cognitive_state.get("short_term_goal")
    impression = state.get("user_impression")
    result = filter_action(data, interpretation=interpretation, emotion=emotion, impression=impression, goal=goal)
    

    return state