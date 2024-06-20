import logging
import random
import os

import torch
from torch.cuda.amp import autocast as autocast
import torch.nn as nn

from medlvlm.common.registry import registry
from medlvlm.models.base_model import disabled_train
from medlvlm.models.medlvlm_base import MedLVLMBase

IMG_DIM_VIT_LLAMA = 5632 # 1408 * 4

@registry.register_model("medlvlm")
class MedLVLM(MedLVLMBase):
    """
    MiniGPT-v2 model
    """

    PRETRAINED_MODEL_CONFIG_DICT = {
        "pretrain": "configs/models/medlvlm.yaml",
    }

    def __init__(
            self,
            vision_model="eva_clip_g",
            img_size=448,
            drop_path_rate=0,
            use_grad_checkpoint=False,
            vit_precision="fp16",
            freeze_vision=True,
            language_model="",
            prompt_template='[INST] {} [/INST]',
            max_txt_len=300,
            end_sym='\n',
            bits=8,
            lora_r=64,
            lora_target_modules=["q_proj", "v_proj"],
            lora_alpha=16,
            lora_dropout=0.05,
            chat_template=False,
            use_grad_checkpoint_llm=False,
            max_context_len=3800,
            low_resource=False,  # use 8 bit and put vit in cpu
            device_8bit=0,  # the device of 8bit model should be set when loading and cannot be changed anymore.
    ):
        super().__init__(
            vision_model=vision_model,
            img_size=img_size,
            drop_path_rate=drop_path_rate,
            use_grad_checkpoint=use_grad_checkpoint,
            vit_precision=vit_precision,
            freeze_vision=freeze_vision,
            language_model=language_model,
            max_txt_len=max_txt_len,
            max_context_len=max_context_len,
            end_sym=end_sym,
            prompt_template=prompt_template,
            low_resource=low_resource,
            device_8bit=device_8bit,
            bits=bits,
            lora_r=lora_r,
            lora_target_modules=lora_target_modules,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
        )

        img_f_dim = self.visual_encoder.num_features * self.num_concat

        self.lin1 = nn.Linear(img_f_dim, img_f_dim)
        self.lin2 = nn.Linear(img_f_dim, 29)

        if vision_model == "eva_clip_g" and "llama" in language_model:
            self.language_proj = nn.Linear(
                img_f_dim, self.language_model.config.hidden_size
            )
        else:
            self.language_proj = nn.Sequential(
                nn.Linear(img_f_dim, IMG_DIM_VIT_LLAMA),
                nn.GELU(),
                nn.Linear(IMG_DIM_VIT_LLAMA, self.language_model.config.hidden_size)
            )

        self.chat_template = chat_template

        if use_grad_checkpoint_llm:
            self.language_model.gradient_checkpointing_enable()

    def encode_img(self, image):
        device = image.device

        if len(image.shape) > 4:
            image = image.reshape(-1, *image.shape[-3:])

        with self.maybe_autocast():
            image_embeds = self.ln_vision(self.visual_encoder(image)).to(device)
            image_embeds = image_embeds[:, 1:, :]
            bs, pn, hs = image_embeds.shape
            image_embeds = image_embeds.view(bs, int(pn / self.num_concat), int(hs * self.num_concat))

            image_embeds = self.lin1(image_embeds)
            onehot_logits = self.lin2(image_embeds)[:, 0, :]
            inputs_language = self.language_proj(image_embeds)
            atts_language = torch.ones(inputs_language.size()[:-1], dtype=torch.long).to(image.device)
        return inputs_language, atts_language, onehot_logits

    @classmethod
    def from_config(cls, cfg):
        vision_model = cfg.get("vision_model", "eva_clip_g")
        img_size = cfg.get("image_size")
        language_model = cfg.get("language_model")

        drop_path_rate = cfg.get("drop_path_rate", 0)
        use_grad_checkpoint = cfg.get("use_grad_checkpoint", False)
        vit_precision = cfg.get("vit_precision", "fp16")
        freeze_vision = cfg.get("freeze_vision", True)
        low_resource = cfg.get("low_resource", False)

        prompt_template = cfg.get("prompt_template", '[INST] {} [/INST]')
        max_txt_len = cfg.get("max_txt_len", 300)
        end_sym = cfg.get("end_sym", '\n')

        bits = cfg.get("bits", 8)
        lora_r = cfg.get("lora_r", 64)
        lora_alpha = cfg.get("lora_alpha", 16)
        chat_template = cfg.get("chat_template", False)

        use_grad_checkpoint_llm = cfg.get("use_grad_checkpoint_llm", False)
        max_context_len = cfg.get("max_context_len", 3800)

        model = cls(
            vision_model=vision_model,
            img_size=img_size,
            drop_path_rate=drop_path_rate,
            use_grad_checkpoint=use_grad_checkpoint,
            vit_precision=vit_precision,
            freeze_vision=freeze_vision,
            language_model=language_model,
            prompt_template=prompt_template,
            max_txt_len=max_txt_len,
            low_resource=low_resource,
            end_sym=end_sym,
            lora_r=lora_r,
            lora_alpha=lora_alpha,
            bits=bits,
            chat_template=chat_template,
            use_grad_checkpoint_llm=use_grad_checkpoint_llm,
            max_context_len=max_context_len,
        )

        ckpt_path = cfg.get("ckpt", "")  # load weights of MiniGPT-4
        if ckpt_path:
            print("Load Model Checkpoint: {}".format(ckpt_path))
            ckpt = torch.load(ckpt_path, map_location="cpu")
            msg = model.load_state_dict(ckpt['model'], strict=False)

            is_eva_clip_g_llama = False
            if vision_model == "eva_clip_g" and "llama" in language_model:
                is_eva_clip_g_llama = True

            if os.path.basename(ckpt_path) == "checkpoint_stage3.pth" and not is_eva_clip_g_llama:
                model.language_proj[-1].weight.data = ckpt['model']['llama_proj.weight']
                model.language_proj[-1].bias.data = ckpt['model']['llama_proj.bias']

        return model