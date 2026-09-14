from collections import Counter
from os import path
import os
import torch
import torch.nn as nn
from transformers import RobertaTokenizer, RobertaModel
from huggingface_hub import hf_hub_download
import json
import numpy as np

local_dir = "/Users/vincentgilbert/.cache/huggingface/hub/models--Lakssssshya--roberta-large-goemotions/snapshots/e83088c44bdc2df1cd1d51726c5fd205c6293a41"

class RobertaForMultiLabelClassification(nn.Module):
    def __init__(self, model_name, num_labels, dropout_rate=0.3, use_mean_pooling=True):
        super().__init__()
        self.roberta = RobertaModel.from_pretrained(model_name)
        self.use_mean_pooling = use_mean_pooling
        hidden_size = self.roberta.config.hidden_size
        self.dropout1 = nn.Dropout(dropout_rate)
        self.fc1 = nn.Linear(hidden_size, hidden_size // 2)
        self.relu = nn.ReLU()
        self.dropout2 = nn.Dropout(dropout_rate)
        self.fc2 = nn.Linear(hidden_size // 2, num_labels)
    def mean_pooling(self, token_embeddings, attention_mask):
        input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, 1)
        sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)
        return sum_embeddings / sum_mask
    def forward(self, input_ids, attention_mask):
        outputs = self.roberta(input_ids, attention_mask=attention_mask)
        if self.use_mean_pooling:
            pooled_output = self.mean_pooling(outputs.last_hidden_state, attention_mask)
        else:
            pooled_output = outputs.pooler_output
        x = self.dropout1(pooled_output)
        x = self.fc1(x)
        x = self.relu(x)
        x = self.dropout2(x)
        logits = self.fc2(x)
        return logits


emotion_labels = [
    'admiration', 'amusement', 'anger', 'annoyance', 'approval', 
    'caring', 'confusion', 'curiosity', 'desire', 'disappointment',
    'disapproval', 'disgust', 'embarrassment', 'excitement', 'fear',
    'gratitude', 'grief', 'joy', 'love', 'nervousness',
    'optimism', 'pride', 'realization', 'relief', 'remorse',
    'sadness', 'surprise', 'neutral'
]

class EmotionPrediction():
    def __init__(self, model_name="Lakssssshya/roberta-large-goemotions"):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.tokenizer = RobertaTokenizer.from_pretrained(model_name)

        # self.config_path = hf_hub_download(repo_id=model_name, filename="config.json")
        with open(os.path.join(model_name, "config.json"), 'r') as f:
            config = json.load(f)
        self.model = RobertaForMultiLabelClassification(
            model_name='roberta-large',
            num_labels=config['num_labels'],
            dropout_rate=config.get('dropout_rate', 0.3),
            use_mean_pooling=config.get('use_mean_pooling', True)
        )
        # weights_path = hf_hub_download(model_name, filename="pytorch_model.bin")
        state_dict = torch.load(os.path.join(model_name, "pytorch_model.bin"), map_location=self.device)
        self.model.load_state_dict(state_dict)
        self.model.to(self.device)
        self.model.eval()
        # thresholds_path = hf_hub_download(repo_id=model_name, filename="optimal_thresholds.json")
        with open(os.path.join(model_name, "optimal_thresholds.json"), 'r') as f:
            self.thresholds = np.array(json.load(f))


    def predict_emotions(self, text):
        inputs = self.tokenizer(text, return_tensors='pt', truncation=True, max_length=128, padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        
        with torch.no_grad():
            logits = self.model(input_ids=inputs['input_ids'], attention_mask=inputs['attention_mask'])
            probs = torch.sigmoid(logits).cpu().numpy()[0]
        
        # Apply optimized thresholds
        predictions = (probs > self.thresholds).astype(int)
        
        # Get predicted emotions
        predicted_emotions = [emotion_labels[i] for i in range(len(predictions)) if predictions[i] == 1]
        
        # Get top emotions with scores
        top_indices = np.argsort(probs)[::-1][:5]
        top_emotions = [(emotion_labels[idx], float(probs[idx])) for idx in top_indices]
        
        return {
            'predicted_emotions': predicted_emotions,
            'top_emotions': top_emotions
        }

# ***************************************************************************************** #
#                                                                                           #
#               VAD LEXICON EXTRACTION                                                      #
#                                                                                           #
# ***************************************************************************************** #

class EmotionVADPrediction():
    def __init__(self):
        self.path = "./data/PolarSubset"
        self.arousal_path = path.join(self.path + "arousal-VAD-Lexicon.txt")
        self.dominance_path = path.join(self.path + "dominance-VAD-Lexicon.txt")
        self.valence_path = path.join(self.path + "valence-VAD-Lexicon.txt")
        self.arousal = self.populate_data(self.arousal_path)
        self.dominance = self.populate_data(self.dominance_path)
        self.valence = self.populate_data(self.valence_path)
    
    def populate_data(self, path):
        result = {}
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                key, value = line.rsplit(maxsplit=1)
                result[key] = value
        return result
    
    def extract_features(self, text):
        tokens = text.lower().split()
        counts = Counter()

        for tok in tokens:
            if tok in self.arousal:
                counts["arousal"] += float(self.arousal[tok])
            if tok in self.valence:
                counts["valence"] += float(self.valence[tok])
            if tok in self.dominance:
                counts["dominance"] += float(self.dominance[tok])
            
        total = len(tokens)
        return {k: v/total for k, v in counts.items()}

    
