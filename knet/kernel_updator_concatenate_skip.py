import torch
import torch.nn as nn
import torch.nn.functional as F
from mmcv.cnn import build_activation_layer, build_norm_layer
from mmcv.cnn.bricks.transformer import TRANSFORMER_LAYER


@TRANSFORMER_LAYER.register_module()
class KernelUpdatorConcatenateSkip(nn.Module):

    def __init__(self,
                 in_channels=256,
                 feat_channels=64,
                 out_channels=None,
                 input_feat_shape=3,
                 gate_sigmoid=True,
                 gate_norm_act=False,
                 activate_out=False,
                 act_cfg=dict(type='ReLU', inplace=True),
                 norm_cfg=dict(type='LN')):
        super(KernelUpdatorConcatenateSkip, self).__init__()
        self.in_channels = in_channels
        self.feat_channels = feat_channels
        self.out_channels_raw = out_channels
        self.gate_sigmoid = gate_sigmoid
        self.gate_norm_act = gate_norm_act
        self.activate_out = activate_out
        if isinstance(input_feat_shape, int):
            input_feat_shape = [input_feat_shape] * 2
        self.input_feat_shape = input_feat_shape
        self.act_cfg = act_cfg
        self.norm_cfg = norm_cfg
        self.out_channels = out_channels if out_channels else in_channels

        assert self.feat_channels % 2 == 0, "Feature dimension has to be divisible by two."
        self.num_params_in = self.feat_channels // 2
        self.num_params_out = self.feat_channels
        self.dynamic_layer = nn.Linear(
            self.in_channels, self.num_params_in + self.num_params_out)
        self.input_layer = nn.Linear(self.in_channels,
                                     self.num_params_in,
                                     1)
        self.update_gate = nn.Linear(self.in_channels, self.feat_channels, 1)
        if self.gate_norm_act:
            self.gate_norm = build_norm_layer(norm_cfg, self.feat_channels)[1]

        self.norm_in = build_norm_layer(norm_cfg, self.feat_channels)[1]
        self.norm_out = build_norm_layer(norm_cfg, self.feat_channels)[1]

        self.activation = build_activation_layer(act_cfg)

        self.fc_layer = nn.Linear(self.feat_channels, self.out_channels, 1)
        self.fc_norm = build_norm_layer(norm_cfg, self.out_channels)[1]

    def forward(self, update_feature, input_feature):
        update_feature = update_feature.reshape(-1, self.in_channels)
        num_proposals = update_feature.size(0)
        parameters = self.dynamic_layer(update_feature)
        param_in = parameters[:, :self.num_params_in].view(
            -1, self.feat_channels // 2)
        param_out = parameters[:, -self.num_params_out:].view(
            -1, self.feat_channels)

        input_feats = self.input_layer(
            input_feature.reshape(num_proposals, -1, self.feat_channels))
        input_in = input_feats[..., :self.num_params_in]
        input_out = input_feature.reshape(num_proposals, -1, self.feat_channels)

        gate_feats = torch.cat([input_in, param_in.unsqueeze(-2)], dim=-1)
        if self.gate_norm_act:
            gate_feats = self.activation(self.gate_norm(gate_feats))

        update_gate = self.norm_in(self.update_gate(gate_feats))
        if self.gate_sigmoid:
            update_gate = update_gate.sigmoid()
        param_out = self.norm_out(param_out)

        if self.activate_out:
            param_out = self.activation(param_out)

        # param_out has shape (batch_size, feat_channels, out_channels)
        features = update_gate * param_out.unsqueeze(
            -2) + input_out

        features = self.fc_layer(features)
        features = self.fc_norm(features)
        features = self.activation(features)

        return features