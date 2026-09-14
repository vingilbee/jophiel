import numpy as np

class Emotion():
  def __init__(self):
    pass

  def interpretation_to_affective_input(self, assumed_emotion, attribution = ""):
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

  def pull_to_baseline(self, x, baseline, strength=0.1):
    return x + strength * (baseline - x)

  def apply_reactivity(self, dv, da, emotion_dynamics: dict):
      if dv >= 0:
          dv *= emotion_dynamics['reactivity_positive']
      else:
          dv *= emotion_dynamics['reactivity_negative']

      dv *= (1 - emotion_dynamics["emotion_regulation"]["supression"])
      da *= (1 - emotion_dynamics["emotion_regulation"]["supression"])

      return dv, da

  def map_tags_emotion(self, valence: float, arousal: str):
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

  def reappraisal(self, valence, arousal, reappraisal):
      valence += reappraisal * (1 - abs(valence))
      return valence, arousal

  def supression(self, valence, arousal, suppresion):
      valence *= (1 - suppresion)
      arousal *= (1 - suppresion)
      return valence, arousal


  def generate_fast_emotion(self, emotion, interpretation , emotion_dynamics):
      dv, da = self.interpretation_to_affective_input(interpretation['assumed_user_emotion'])
      dv, da = self.apply_reactivity(dv, da, emotion_dynamics)

      valence = emotion['valence'] + dv
      arousal = emotion['arousal'] + da
      valence, arousal = self.reappraisal(valence=valence, arousal=arousal, reappraisal=emotion_dynamics["emotion_regulation"]["reappraisal"])
      if emotion['valence'] < 0:
          valence -= emotion_dynamics["rumination_tandecy"] * abs(emotion['valence'])


      decay = emotion_dynamics["decay_rate"]
      valence = (1 - decay) * valence
      arousal = (1 - decay) * arousal
      valence = self.pull_to_baseline(valence, emotion_dynamics["baseline_valence"])
      arousal = self.pull_to_baseline(valence, emotion_dynamics["baseline_arousal"])
      valence, arousal = self.supression(valence, arousal, emotion_dynamics["emotion_regulation"]["supression"])
      valence = max(-1, min(1, valence))
      arousal = max(0, min(1, arousal))
      return {
          "valence": valence,
          "arousal": arousal,
          "dominance": 0.5,
          "primary_emotion_tag": self.map_tags_emotion(valence, arousal)
      }