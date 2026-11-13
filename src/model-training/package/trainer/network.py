"""
The model's "blueprint": how to build the model, preprocess images, save and load weights. Training (task.py) and deployment (the API) both use this one file.
best_model.pt only stores the trained part (last 2 blocks + linear layer), so first build_model, then load_weights. In deployment (scoring only):
    tf = eval_transform()
    model = build_model(15); load_weights(model, "best_model.pt"); model.eval()
    scores = model(tf(Image.open("tile.png").convert("RGB")).unsqueeze(0))[0]     # 15 scores, each 0 to 1
"""
import timm
import torch
from torch import nn

BACKBONE = "dinov2_s"
SOURCE = "vit_small_patch14_dinov2.lvd142m"      # name of the pretrained model DINOv2-S (downloaded automatically the first time, about 90 MB)
IMG_SIZE = 224                                    # image size the model sees: the 256x256 original is resized to 224x224
PREPROCESSING = "resize 224, timm mean/std"      # saved in config.json


# The model itself: an image goes in, the encoder turns it into 384 features, the linear layer computes 15 scores, sigmoid squashes them to 0-1
class Regressor(nn.Module):

    def __init__(self, encoder, head):
        super().__init__()
        self.encoder, self.head = encoder, head

    def forward(self, x):
        return torch.sigmoid(self.head(self.encoder(x)))


# Build the model. n_outputs = how many scores the model outputs (15 for us), returns the built model
# The encoder is the original pretrained DINOv2-S (12 blocks), then a new linear layer on top
# Freezing / unfreezing only matters for training: training only changes the last 2 blocks and the linear layer, the first 10 blocks stay put. Deployment doesn't train, so these lines do nothing there
def build_model(n_outputs, unfreeze_blocks=2):
    enc = timm.create_model(SOURCE, pretrained=True, num_classes=0, img_size=IMG_SIZE)   # original pretrained weights; drop its own classification head
    for p in enc.parameters():
        p.requires_grad = False                                                       # freeze everything
    for p in [*enc.blocks[-unfreeze_blocks:].parameters(), *enc.norm.parameters()]:
        p.requires_grad = True                                                        # unfreeze the last 2 blocks
    return Regressor(enc, nn.Linear(enc.num_features, n_outputs))


# Image preprocessing: resize to 224x224, turn into numbers, normalize (subtract mean, divide by std)
# Training and deployment must be exactly the same, otherwise scores quietly get worse
def eval_transform():
    enc = timm.create_model(SOURCE, pretrained=False, num_classes=0, img_size=IMG_SIZE)  # only to read the preprocessing settings that come with DINOv2
    cfg = timm.data.resolve_data_config({}, model=enc)
    cfg["crop_pct"] = 1.0                            # no cropping
    cfg["input_size"] = (3, IMG_SIZE, IMG_SIZE)     # default is 518, change to 224 (3 RGB channels)
    return timm.data.create_transform(**cfg, is_training=False)


# Params = the numbers in the model (about 21.63 million in total), grouped by layer into 176 matrices, each matrix has a name, e.g. head.weight (15x384)
# This function lists the names of the matrices that training changes: last 2 blocks + linear layer, 32 in total (about 3.56 million numbers)
def trainable_names(model):
    return {n for n, p in model.named_parameters() if p.requires_grad}


# Picks out the part of the weights to save (the 32 matrices above), doesn't write a file itself: task.py step 4 uses it, then torch.save writes it into best_model.pt
# The first 10 blocks were not touched in training, same as the original, so no need to save them
def trained_weights(model, names):
    return {k: v.cpu() for k, v in model.state_dict().items() if k in names}


# Put best_model.pt onto the built model, and check that none of the trained parts are missing
def load_weights(model, path):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    res = model.load_state_dict(ck["weights"], strict=False)       # strict=False: the first 10 blocks are not in the file, use the original ones
    assert not res.unexpected_keys, f"the weights do not fit the model: {res.unexpected_keys[:3]}"
    frozen = {n for n, _ in model.named_parameters() if n not in ck["weights"]}
    assert set(res.missing_keys) == frozen and all(not n.startswith("head") for n in frozen), "a trained layer is missing from the saved weights"
    return ck
