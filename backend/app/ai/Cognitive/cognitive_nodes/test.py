import json
import asyncio
from .fast_cognitive_nodes import generate_fast_response_generator
from .module.perception.emotions_extractions import EmotionPrediction, EmotionVADPrediction
from ....states.memory import AttributionType, BaseIntent, CognitiveSystem, EmotionState, IntentHypothesis, Interpretation, MemoryState, AssumedUserEmotion, PrimaryGoal, STGoal
async def main():
    emotion_prediction = EmotionPrediction(model_name="/Users/vincentgilbert/.cache/huggingface/hub/models--Lakssssshya--roberta-large-goemotions/snapshots/e83088c44bdc2df1cd1d51726c5fd205c6293a41")
    vad_prediction = EmotionVADPrediction()
    text = "I told you to move"
    result = emotion_prediction.predict_emotions(text)
    vad = vad_prediction.extract_features(text)
    print(result, vad)

asyncio.run(main())