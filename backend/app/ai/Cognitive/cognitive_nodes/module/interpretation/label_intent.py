from transformers import AutoTokenizer, AutoModelForSequenceClassification

class PredictIntent():
  def __init__(self):
    model_path = "/content/checkpoint-1119"
    self.tokenizer = AutoTokenizer.from_pretrained(model_path)
    self.model = AutoModelForSequenceClassification.from_pretrained(model_path)
    self.model.eval()

  def predict(self, text):
    input = self.tokenizer(text, return_tensors="pt")
    logits = self.model(**input).logits
    pred = logits.argmax(dim=-1)
    label = self.model.config.id2label[pred.item()]
    return {
        "LabelIntent": label
    }