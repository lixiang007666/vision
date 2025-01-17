"""
Modified from https://github.com/clovaai/rexnet/blob/master/rexnetv1_lite.py
"""
import oneflow as flow
import oneflow.nn as nn

from .utils import load_state_dict_from_url
from .registry import ModelCreator
from flowvision.models.helpers import make_divisible


model_urls = {
    "rexnet_lite_1_0": "https://oneflow-public.oss-cn-beijing.aliyuncs.com/model_zoo/flowvision/classification/RexNet/rexnet_lite_1_0.zip",
    "rexnet_lite_1_3": None,
    "rexnet_lite_1_5": None,
    "rexnet_lite_2_0": None,
}


def _add_conv(
    out,
    in_channels,
    channels,
    kernel=1,
    stride=1,
    pad=0,
    num_group=1,
    active=True,
    relu6=True,
    bn_momentum=0.1,
    bn_eps=1e-5,
):
    out.append(
        nn.Conv2d(
            in_channels, channels, kernel, stride, pad, groups=num_group, bias=False
        )
    )
    out.append(nn.BatchNorm2d(channels, momentum=bn_momentum, eps=bn_eps))
    if active:
        out.append(nn.ReLU6(inplace=True) if relu6 else nn.ReLU(inplace=True))


class LinearBottleneck(nn.Module):
    def __init__(
        self,
        in_channels,
        channels,
        t,
        kernel_size=3,
        stride=1,
        bn_momentum=0.1,
        bn_eps=1e-5,
        **kwargs
    ):
        super(LinearBottleneck, self).__init__(**kwargs)
        self.conv_shortcut = None
        self.use_shortcut = stride == 1 and in_channels <= channels
        self.in_channels = in_channels
        self.out_channels = channels
        out = []
        if t != 1:
            dw_channels = in_channels * t
            _add_conv(
                out,
                in_channels=in_channels,
                channels=dw_channels,
                bn_momentum=bn_momentum,
                bn_eps=bn_eps,
            )
        else:
            dw_channels = in_channels

        _add_conv(
            out,
            in_channels=dw_channels,
            channels=dw_channels * 1,
            kernel=kernel_size,
            stride=stride,
            pad=(kernel_size // 2),
            num_group=dw_channels,
            bn_momentum=bn_momentum,
            bn_eps=bn_eps,
        )

        _add_conv(
            out,
            in_channels=dw_channels,
            channels=channels,
            active=False,
            bn_momentum=bn_momentum,
            bn_eps=bn_eps,
        )

        self.out = nn.Sequential(*out)

    def forward(self, x):
        out = self.out(x)

        if self.use_shortcut:
            out[:, 0 : self.in_channels] += x
        return out


class ReXNetV1_lite(nn.Module):
    def __init__(
        self,
        fix_head_stem=False,
        divisible_value=8,
        input_ch=16,
        final_ch=164,
        multiplier=1.0,
        classes=1000,
        dropout_ratio=0.2,
        bn_momentum=0.1,
        bn_eps=1e-5,
        kernel_conf="333333",
    ):
        super(ReXNetV1_lite, self).__init__()

        layers = [1, 2, 2, 3, 3, 5]
        strides = [1, 2, 2, 2, 1, 2]
        kernel_sizes = [int(element) for element in kernel_conf]

        strides = sum(
            [
                [element] + [1] * (layers[idx] - 1)
                for idx, element in enumerate(strides)
            ],
            [],
        )
        ts = [1] * layers[0] + [6] * sum(layers[1:])
        kernel_sizes = sum(
            [[element] * layers[idx] for idx, element in enumerate(kernel_sizes)], []
        )
        self.num_convblocks = sum(layers[:])

        features = []
        inplanes = input_ch / multiplier if multiplier < 1.0 else input_ch
        first_channel = 32 / multiplier if multiplier < 1.0 or fix_head_stem else 32
        first_channel = make_divisible(
            int(round(first_channel * multiplier)), divisible_value
        )

        in_channels_group = []
        channels_group = []

        _add_conv(
            features,
            3,
            first_channel,
            kernel=3,
            stride=2,
            pad=1,
            bn_momentum=bn_momentum,
            bn_eps=bn_eps,
        )

        for i in range(self.num_convblocks):
            inplanes_divisible = make_divisible(
                int(round(inplanes * multiplier)), divisible_value
            )
            if i == 0:
                in_channels_group.append(first_channel)
                channels_group.append(inplanes_divisible)
            else:
                in_channels_group.append(inplanes_divisible)
                inplanes += final_ch / (self.num_convblocks - 1 * 1.0)
                inplanes_divisible = make_divisible(
                    int(round(inplanes * multiplier)), divisible_value
                )
                channels_group.append(inplanes_divisible)

        for block_idx, (in_c, c, t, k, s) in enumerate(
            zip(in_channels_group, channels_group, ts, kernel_sizes, strides)
        ):
            features.append(
                LinearBottleneck(
                    in_channels=in_c,
                    channels=c,
                    t=t,
                    kernel_size=k,
                    stride=s,
                    bn_momentum=bn_momentum,
                    bn_eps=bn_eps,
                )
            )

        pen_channels = (
            int(1280 * multiplier) if multiplier > 1 and not fix_head_stem else 1280
        )
        _add_conv(features, c, pen_channels, bn_momentum=bn_momentum, bn_eps=bn_eps)

        self.features = nn.Sequential(*features)
        self.avgpool = nn.AdaptiveAvgPool2d(1)

        self.output = nn.Sequential(
            nn.Conv2d(pen_channels, 1024, 1, bias=True),
            nn.BatchNorm2d(1024, momentum=bn_momentum, eps=bn_eps),
            nn.ReLU6(inplace=True),
            nn.Dropout(dropout_ratio),
            nn.Conv2d(1024, classes, 1, bias=True),
        )

    def forward(self, x):
        x = self.features(x)
        x = self.avgpool(x)
        x = self.output(x).flatten(1)
        return x


def _create_rexnet_lite(arch, pretrained=False, progress=True, **model_kwargs):
    model = ReXNetV1_lite(**model_kwargs)
    if pretrained:
        state_dict = load_state_dict_from_url(model_urls[arch], progress=progress)
        model.load_state_dict(state_dict)
    return model


@ModelCreator.register_model
def rexnet_lite_1_0(pretrained=False, progress=True, **kwargs):
    """
    Constructs the ReXNet-lite model with width multiplier of 1.0.

    .. note::
        ReXNet-lite model with width multiplier of 1.0 from the `Rethinking Channel Dimensions for Efficient Model Design <https://arxiv.org/pdf/2007.00992.pdf>`_ paper.

    Args:
        pretrained (bool): Whether to download the pre-trained model on ImageNet. Default: ``False``
        progress (bool): If True, displays a progress bar of the download to stderr. Default: ``True``

    For example:

    .. code-block:: python

        >>> import flowvision
        >>> rexnet_lite_1_0 = flowvision.models.rexnet_lite_1_0(pretrained=False, progress=True)

    """
    model_kwargs = dict(multiplier=1.0, **kwargs)
    return _create_rexnet_lite(
        "rexnet_lite_1_0", pretrained=pretrained, progress=progress, **model_kwargs
    )


@ModelCreator.register_model
def rexnet_lite_1_3(pretrained=False, progress=True, **kwargs):
    """
    Constructs the ReXNet-lite model with width multiplier of 1.3.

    .. note::
        ReXNet-lite model with width multiplier of 1.3 from the `Rethinking Channel Dimensions for Efficient Model Design <https://arxiv.org/pdf/2007.00992.pdf>`_ paper.

    Args:
        pretrained (bool): Whether to download the pre-trained model on ImageNet. Default: ``False``
        progress (bool): If True, displays a progress bar of the download to stderr. Default: ``True``

    For example:

    .. code-block:: python

        >>> import flowvision
        >>> rexnet_lite_1_3 = flowvision.models.rexnet_lite_1_3(pretrained=False, progress=True)

    """
    model_kwargs = dict(multiplier=1.3, **kwargs)
    return _create_rexnet_lite(
        "rexnet_lite_1_3", pretrained=pretrained, progress=progress, **model_kwargs
    )


@ModelCreator.register_model
def rexnet_lite_1_5(pretrained=False, progress=True, **kwargs):
    """
    Constructs the ReXNet-lite model with width multiplier of 1.5.

    .. note::
        ReXNet-lite model with width multiplier of 1.5 from the `Rethinking Channel Dimensions for Efficient Model Design <https://arxiv.org/pdf/2007.00992.pdf>`_ paper.

    Args:
        pretrained (bool): Whether to download the pre-trained model on ImageNet. Default: ``False``
        progress (bool): If True, displays a progress bar of the download to stderr. Default: ``True``

    For example:

    .. code-block:: python

        >>> import flowvision
        >>> rexnet_lite_1_5 = flowvision.models.rexnet_lite_1_5(pretrained=False, progress=True)

    """
    model_kwargs = dict(multiplier=1.5, **kwargs)
    return _create_rexnet_lite(
        "rexnet_lite_1_5", pretrained=pretrained, progress=progress, **model_kwargs
    )


@ModelCreator.register_model
def rexnet_lite_2_0(pretrained=False, progress=True, **kwargs):
    """
    Constructs the ReXNet-lite model with width multiplier of 2.0.

    .. note::
        ReXNet-lite model with width multiplier of 2.0 from the `Rethinking Channel Dimensions for Efficient Model Design <https://arxiv.org/pdf/2007.00992.pdf>`_ paper.

    Args:
        pretrained (bool): Whether to download the pre-trained model on ImageNet. Default: ``False``
        progress (bool): If True, displays a progress bar of the download to stderr. Default: ``True``

    For example:

    .. code-block:: python

        >>> import flowvision
        >>> rexnet_lite_2_0 = flowvision.models.rexnet_lite_2_0(pretrained=False, progress=True)

    """
    model_kwargs = dict(multiplier=2.0, **kwargs)
    return _create_rexnet_lite(
        "rexnet_lite_2_0", pretrained=pretrained, progress=progress, **model_kwargs
    )
