from .emotions_extractions import EmotionPrediction, EmotionVADPrediction
from .politeness_extractions import PolitenessPrediction
from .toxicity_extractions import ToxicityPrediction

class Perception():
  def __init__(self):
    self.emotion_prediction = EmotionPrediction()
    self.politeness_prediction = PolitenessPrediction()
    self.emotion_vad_prediction = EmotionVADPrediction()
    self.toxicity_prediction = ToxicityPrediction()

  def predict(self, text):
    polite = self.politeness_prediction.predict(text)
    emotion = self.emotion_prediction.predict_emotions(text)
    vad = self.emotion_vad_prediction.extract_features(text)
    toxicity = self.toxicity_prediction.predict(text)
    return polite | emotion | vad |toxicity