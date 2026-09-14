from transformers import AutoTokenizer
from optimum.onnxruntime import ORTModelForSequenceClassification
import numpy as np

class ToxicityPrediction():
    def __init__(self):
      self.model = ORTModelForSequenceClassification.from_pretrained("gravitee-io/detoxify-onnx", file_name="model.quant.onnx")
      self.tokenizer = AutoTokenizer.from_pretrained("gravitee-io/detoxify-onnx")

    def predict(self, text):
      inputs = self.tokenizer(text, return_tensors="pt", padding=True, truncation=True)
      outputs = self.model(**inputs)
      logits = outputs.logits
      probs = 1 / (1 + np.exp(np.array(-logits)))
      return {"toxicity": float(probs[0][0])}