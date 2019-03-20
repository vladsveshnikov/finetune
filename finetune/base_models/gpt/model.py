import os

from finetune.base_models import SourceModel
from finetune.base_models.gpt.encoder import GPTEncoder
from finetune.base_models.gpt.featurizer import gpt_featurizer
from finetune.utils import finetune_model_path

class GPTModel(SourceModel):
    encoder = GPTEncoder
    featurizer = gpt_featurizer
    settings = {
        'n_embed': 768,
        'n_heads': 12,
        'n_layer': 12,
        'act_fn': "gelu",
        "base_model_path": os.path.join("gpt", "model-lg.jl")
    }

class GPTModelSmall(GPTModel):
    settings = {
        'n_embed': 512,
        'n_heads': 8,
        'n_layer': 6,
        'act_fn': "gelu",
        'num_layers_trained': 6,
        'base_model_path': os.path.join("gpt", "model-sm.jl"),
    }
