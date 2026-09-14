from transformers import AutoTokenizer, AutoModelForSequenceClassification, pipeline

class PolitenessPrediction():
  def __init__(self):
      model_name = "NOVA-vision-language/polite_bert"
      tokenizer = AutoTokenizer.from_pretrained(model_name)
      model = AutoModelForSequenceClassification.from_pretrained(model_name)

      self.politeness_classifier = pipeline(
          "text-classification",
          model=model,
          tokenizer=tokenizer
      )
  def predict(self, text):
      prediction = self.politeness_classifier(text)[0]
      result = {
          prediction['label']: prediction['score']
      }

      return {
          "politeness": result
      }