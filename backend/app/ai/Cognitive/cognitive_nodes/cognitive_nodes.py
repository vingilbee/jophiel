
from datetime import datetime, timedelta
from src.ai.model.open_ai import OpenAIGenerator
from ....states.memory import MemoryState
from ..model.cognitive_model import Perception, Interpretation, EmotionState, STGoal, ActionCandidates, FinalAction
from ....prompts.social_cognitive_prompt import PERCEPTION_PROMPT, INTERPRETATION_PROMPT, EMOTION_UPDATER, COGNITIVE_SYSTEM, GOAL_CLARIFICATION, RESPONSE_DECISION, RESPONSE_GENERATION

async def generate_perception(state:MemoryState):
    stm_memory = state.get("stm_memory", [])
    weights = state.get("core_memory").get("perception", None)
    cognitive_prompt = PERCEPTION_PROMPT.format(
        stm_memory=stm_memory,
        perception_weights=weights
    )
    llm = OpenAIGenerator.with_structured_output(Perception)
    result = llm.invoke(COGNITIVE_SYSTEM + cognitive_prompt)
    print(result, "perception")
    state["cognitive_state"]["perception"] = result
    return state

async def generate_interpretation(state:MemoryState):
    user_impression = state.get("user_impression", None)
    theory_of_mind = state.get("core_memory").get("theory_of_mind", None)
    inter_bias = state.get("core_memory").get("interpretatoin_biases", None)
    perception = state.get("cognitive_state").get("perception", None)
    interpretation_prompt = INTERPRETATION_PROMPT.format(
        perception=perception,
        user_priors=user_impression,
        interpretation_bias=inter_bias,
        theory_of_mind=theory_of_mind
    )
    llm = OpenAIGenerator.with_structured_output(Interpretation)
    result = llm.invoke(COGNITIVE_SYSTEM + interpretation_prompt)
    print(result, "interpretation")
    state["cognitive_state"]["interpretation"] = result
    return state

async def generate_emotion(state:MemoryState):
    previous_emotion = state.get("cognitive_state").get("emotion", None)
    interpretation = state.get("cognitive_state").get("interpretation", None)
    emotional_dynamics = state.get("core_memory").get("emotional_dynamics", None)
    time_delta = datetime.now() - state.get("last_time", datetime.now())
    emotion_prompt = EMOTION_UPDATER.format(
        previous_emotion=previous_emotion,
        interpretation=interpretation,
        emotional_dynamics=emotional_dynamics,
        time_delta=time_delta
    )
    llm = OpenAIGenerator.with_structured_output(EmotionState)
    result = llm.invoke(COGNITIVE_SYSTEM + emotion_prompt)
    print(result, "emotion")
    state["cognitive_state"]["emotion"] = result
    return state

async def generate_goal_clarification(state:MemoryState):
    interpretation = state.get("cognitive_state").get("interpretation", None)
    current_emotion = state.get("cognitive_state").get("emotion", None)
    value = state.get("core_memory").get("value_system", None)
    long_term_role = state.get("core_memory").get("identity").get("role_archetype", "")
    goal_prompt = GOAL_CLARIFICATION.format(
        interpretation=interpretation,
        current_emotion=current_emotion,
        value=value,
        long_term_role=long_term_role,
    )
    llm = OpenAIGenerator.with_structured_output(STGoal)
    result = llm.invoke(COGNITIVE_SYSTEM + goal_prompt)
    print(result, "goal clarif")
    state["cognitive_state"]["short_term_goal"] = result
    return state

async def generate_response_generator(state:MemoryState):
    primary_goal = state.get("cognitive_state").get("short_term_goal", None)
    interpretation = state.get("cognitive_state").get("interpretation", None)
    decision_policy = state.get("core_memory").get("decision_policy", None)
    user_impression = state.get("user_impression", None)
    goal_prompt = RESPONSE_GENERATION.format(
        primary_goal=primary_goal,
        interpretation=interpretation,
        decision_policy=decision_policy,
        user_priors=user_impression
    )
    llm = OpenAIGenerator.with_structured_output(ActionCandidates)
    result = llm.invoke(COGNITIVE_SYSTEM + goal_prompt)
    print(result, "response generation")
    state["cognitive_state"]["action_candidates"] = result
    return state

async def generate_response_decision(state:MemoryState):
    candidates = state.get("cognitive_state").get("action_candidates", None)
    values = state.get("core_memory").get("value_system")
    user_impression = state.get("user_impression", None)
    current_emotion = state.get("cognitive_state").get("emotion", None)
    decision_policy = state.get("core_memory").get("decision_policy", None)
    goal_prompt = RESPONSE_DECISION.format(
        candidates=candidates,
        values=values,
        user_priors=user_impression,
        decision_policy=decision_policy,
        current_emotion=current_emotion
    )
    llm = OpenAIGenerator.with_structured_output(FinalAction)
    result = llm.invoke(COGNITIVE_SYSTEM + goal_prompt)
    print(result, "decision")
    state["cognitive_state"]["final_action"] = result
    return state