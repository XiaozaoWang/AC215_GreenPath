"""
模型的"设计图"：模型怎么搭、图片怎么处理、权重怎么存和装。训练（task.py）和部署（API）都用这一份。

best_model.pt 只存了训练过的部分（最后 2 块 + 线性层），不是完整的模型。用它要两步：
先 build_model 搭出完整的模型，再 load_weights 把训练好的部分换上去。

部署时这样用（不训练，只打分）：
    tf = eval_transform()
    model = build_model(15); load_weights(model, "best_model.pt"); model.eval()
    scores = model(tf(Image.open("tile.png").convert("RGB")).unsqueeze(0))[0]     # 15 个 0–1 的分数
"""
import timm
import torch
from torch import nn

BACKBONE = "dinov2_s"
SOURCE = "vit_small_patch14_dinov2.lvd142m"      # 预训练模型 DINOv2-S 的名字（第一次用时自动下载，约 90 MB）
IMG_SIZE = 224                                    # 模型看的图片尺寸：256×256 的原图缩到 224×224
PREPROCESSING = "resize 224, timm mean/std"      # 记进 config.json


# 模型本身：输入一张图 → 编码器把图变成 384 个特征 → 线性层算出 15 个分数 → sigmoid 压到 0–1
class Regressor(nn.Module):

    def __init__(self, encoder, head):
        super().__init__()
        self.encoder, self.head = encoder, head

    def forward(self, x):
        return torch.sigmoid(self.head(self.encoder(x)))


# 搭模型。n_outputs = 模型要输出几个分数（我们是 15），返回搭好的模型
# 编码器用原厂预训练好的 DINOv2-S（12 块），再接一个新的线性层
# 冻住 / 解冻只影响训练：训练时只改最后 2 块和线性层，前 10 块不动。部署时不训练，这几行没有作用
def build_model(n_outputs, unfreeze_blocks=2):
    enc = timm.create_model(SOURCE, pretrained=True, num_classes=0, img_size=IMG_SIZE)   # 原厂权重；不要它原来的分类头
    for p in enc.parameters():
        p.requires_grad = False                                                       # 全部冻住
    for p in [*enc.blocks[-unfreeze_blocks:].parameters(), *enc.norm.parameters()]:
        p.requires_grad = True                                                        # 解冻最后 2 块
    return Regressor(enc, nn.Linear(enc.num_features, n_outputs))


# 图片预处理：缩到 224×224 → 转成数字 → 标准化（减均值、除标准差）
# 训练和部署必须完全一样，否则分数会悄悄变差
def eval_transform():
    enc = timm.create_model(SOURCE, pretrained=False, num_classes=0, img_size=IMG_SIZE)  # 只为读出 DINOv2 自带的预处理设置
    cfg = timm.data.resolve_data_config({}, model=enc)
    cfg["crop_pct"] = 1.0                            # 不裁剪
    cfg["input_size"] = (3, IMG_SIZE, IMG_SIZE)     # 默认 518，改成 224（RGB 3 个通道）
    return timm.data.create_transform(**cfg, is_training=False)


# 参数 = 模型里的数字（共约 2,163 万个），按层分成 176 个矩阵，每个矩阵有个名字，例如 head.weight（15×384）
# 这个函数列出训练时要改的那些矩阵的名字：最后 2 块 + 线性层，共 32 个（约 356 万个数）
def trainable_names(model):
    return {n for n, p in model.named_parameters() if p.requires_grad}


# 取出要存的那部分权重（上面那 32 个矩阵），本身不存文件：task.py 第 4 步用它，再由 torch.save 写进 best_model.pt
# 前 10 块训练时没动过，和原厂一样，所以不用存
def trained_weights(model, names):
    return {k: v.cpu() for k, v in model.state_dict().items() if k in names}


# 把 best_model.pt 换到搭好的模型上，并检查训练过的部分一个不缺
def load_weights(model, path):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    res = model.load_state_dict(ck["weights"], strict=False)       # strict=False：前 10 块不在文件里，用原厂的
    assert not res.unexpected_keys, f"the weights do not fit the model: {res.unexpected_keys[:3]}"
    frozen = {n for n, _ in model.named_parameters() if n not in ck["weights"]}
    assert set(res.missing_keys) == frozen and all(not n.startswith("head") for n in frozen), "a trained layer is missing from the saved weights"
    return ck
