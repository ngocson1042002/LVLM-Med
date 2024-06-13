from .eva_vit import create_eva_vit_g
from .clip_vit import PubmedCLIPViT

def build_vision_encoder(vision_model, **kwargs):
    if vision_model == "eva_clip_g":
        num_concat = 4
        return create_eva_vit_g(**kwargs), num_concat
    if vision_model == "pubmed_clip_vit":
        img_size = kwargs["img_size"]
        assert img_size == 224, "The resolution of the image must be (224, 224)"
        num_concat = 7
        return PubmedCLIPViT(), num_concat