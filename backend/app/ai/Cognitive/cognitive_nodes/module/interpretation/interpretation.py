from .label_intent import PredictIntent
import numpy as np

class Interpretation():
  def __init__(self):
    self.weight_trait = np.array([
        [0.3, 0.4, -0.9, 0.1, 0.3, -0.8, 0.5],
        [0.6, 0.5, -0.8, 0.1, 0.4, -0.9, 0.5],
        [-0.4, -0.2, 0.6, 0.5, 0, 0.7, -0.2],
        [0.2, 0.6, -0.7, 0.2, 0.5, -0.6, 0.5],
        [0.3, 0.3, -0.4, 0.5, 0.4, 0, 0.5]
    ])
    self.predict_intent = PredictIntent()

  def parsed_cues(self, cues):
    emotion = cues['emotions']
    valence = cues['vad_emotion'].get("valence", 0.0)
    arousal = cues['vad_emotion'].get("arousal", 0.0)
    dominance = cues['vad_emotion'].get("dominance", 0.0)
    toxicity = cues.get('toxicity', 0)
    joy_grad = max(
        emotion.get('joy', 0.0),
        emotion.get('gratitude', 0.0),
        emotion.get('caring', 0.0),

    )
    admiration = emotion.get('admiration', 0.0)
    anger_disgust = max(
        emotion.get('anger', 0.0),
        emotion.get('disgust', 0.0)
    )

    love_caring = max(
        emotion.get('love', 0.0),
        emotion.get('caring', 0.0)
    )
    if 'POLITE' in cues['politeness']:
      politeness = 1
      politeness_w = cues['politeness']['POLITE']
    elif 'NOT_POLITE' in cues['politeness']:
      politeness = -1
      politeness_w = cues['politeness']['NOT_POLITE']
    elif 'SOMEWHAT_POLITE' in cues['politeness']:
      politeness = 0.5
      politeness_w = cues['politeness']['SOMEWHAT_POLITE']
    else:
      politeness = 0
      politeness_w = 0
    return np.array([
        politeness * politeness_w,
        valence,
        toxicity,
        arousal,
        joy_grad,
        anger_disgust,
        love_caring
    ])
  def map_user_emotion(self, cue):
    c_positive = 0
    c_negative = 0
    c_neutral = 0
    for emotion in cue['emotions'].keys():
      if emotion in ["joy", "gratitude", "amusement", "love", "optimism", "caring"]:
        c_positive += 1
      if emotion in ["anger", "annoyance", "disgust", "fear", "grief"]:
        c_negative += 1
      else:
        c_neutral += 1

    if c_positive / (c_positive + c_negative + 1e-8) > 0.5:
      return "Positive"
    if c_negative / (c_positive + c_negative + 1e-8) > 0.5:
      return "Negative"
    if c_neutral > (c_positive + c_negative ):
      return "Neutral"
    return "Mixed"

  def compute_raw_trait(self, cue):
    e_raw = self.weight_trait @ cue
    e_raw = np.clip(e_raw, -1, 1)
    return {
        "trust_level": e_raw[0],
        "warmth": e_raw[1],
        "dominance": e_raw[2],
        "friendliness": e_raw[3],
        "engagement_level": e_raw[4]
    }

  def compute_confidence(self, e_raw, confidence):

    sigma_x = {}
    for trait, e in e_raw.items():
        # Combine relevant cue confidences per trait
        if trait in ["trust_level", "warmth"]:
            cue_conf = (confidence[0] + confidence[4]) / 2
        elif trait in ["dominance"]:
            cue_conf = confidence[5]
        elif trait in ["friendliness", "engagement_level"]:
            cue_conf = confidence[4]
        else:
            cue_conf = 0.5  # fallback

        sigma_x[trait] = np.clip(1 - cue_conf, 0.05, 1.0)  # higher sigma_x = less confident
    return sigma_x

  def sign(self, x):
      return 1 if x > 0 else -1 if x < 0 else 0

  def compute_evidence_biased(self, e_raw, impression, bias, threat_level=0.0):
      e_biased = {}

      for label, e in e_raw.items():
          I = impression[label][0]

          # alignment
          alignment = 1 if self.sign(e) == self.sign(I) and self.sign(I) != 0 else 0

          # belief strength
          belief_strength = abs(I)  # if [-1,1]

          # biases
          A_confirmation = 1 + bias["confirmation_bias"] * alignment * belief_strength
          A_hostile = 1 + bias["hostility_bias"] * threat_level * (1 if e < 0 else 0)

          e_biased[label] = e * A_confirmation * A_hostile

      return e_biased

  def interpret(self, text, cues, impression, bias):
    result = {}
    parsed_cue = self.parsed_cues(cues)
    raw_evidence = self.compute_raw_trait(parsed_cue)
    confidence = self.compute_confidence(raw_evidence, parsed_cue)
    bias_evidence = self.compute_evidence_biased(raw_evidence, impression, bias)
    result["bias_evidence"] = bias_evidence
    label_intent = self.predict_intent.predict(text)
    result["label_intent"] = label_intent
    result["assumed_user_emotion"] = self.map_user_emotion(cues)
    result["confidence"] = confidence
    return result



