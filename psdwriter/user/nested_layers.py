# -*- coding: utf-8 -*-


"""
Convert a PSD file to/from nested layers.
"""


import sys


import numpy as np
import traitlets as t


from .. import core
from .. import enums
from .. import layers as l
from .. import tagged_block


class Layer(t.HasTraits):
    """
    Base class of all layers.
    """
    name = t.Unicode(
        help="The name of the layer"
    )
    visible = t.Bool(
        True,
        help="Is layer visible?"
    )
    opacity = t.Int(
        255, min=0, max=255,
        opacity="Opacity. 0=transparent, 255=opaque"
    )


class Group(Layer):
    """
    A `Layer` that may contain other `Layer` instances.
    """
    blend_mode = t.Enum(
        list(enums.BlendMode),
        default_value=enums.BlendMode.pass_through,
        help="blend mode"
    )
    layers = t.List(
        t.Instance(Layer),
        help="List of sublayers"
    )
    closed = t.Bool(
        True,
        help="Is layer closed in GUI?"
    )


class Image(Layer):
    """
    A `Layer` containing image data, i.e. a leaf node.
    """
    blend_mode = t.Enum(
        list(enums.BlendMode),
        default_value=enums.BlendMode.normal,
        help="blend mode"
    )
    top = t.Int(
        0,
        help="The top of the layer, in pixels."
    )
    left = t.Int(
        0,
        help="The left of the layer, in pixels."
    )
    bottom = t.Int(
        None, allow_none=True,
        help="The bottom of the layer, in pixels. If not provided, "
             "will be automatically determined from channel data."
    )
    right = t.Int(
        None, allow_none=True,
        help="The right of the layer, in pixels. If not provided, "
             "will be automatically determined from channel data."
    )
    channels = t.TraitType(
        help="""
        The channel image data. May be one of the following:
        - A dictionary from `enums.ChannelId` to 2-D numpy arrays.
        - A 3-D numpy array of the shape (num_channels, height, width)
        - A list of numpy arrays where each is a channel.
        """
    )

    @t.validate('channels')
    def _validate_channels(self, proposal):
        value = proposal['value']
        if isinstance(value, dict):
            return value

        if isinstance(value, np.ndarray):
            if len(value.shape) == 3:
                return dict((i, plane) for (i, plane) in enumerate(value))
            else:
                return {0: value}

        if isinstance(value, list):
            return dict((i, plane) for (i, plane) in enumerate(value))

        raise t.TraitError('Invalid channels')


def _iterate_all_images(layers):
    """
    Iterate over all `Image` instances in a hierarchy of `Layer`
    instances.
    """
    if isinstance(layers, list):
        for layer in layers:
            yield from _iterate_all_images(layer)
    if isinstance(layers, Group):
        yield from _iterate_all_images(layers.layers)
    elif isinstance(layers, Image):
        yield layers


def pprint_layers(layers, indent=0):
    """
    Pretty-print a hierarchy of `Layer` instances.
    """
    for layer in layers:
        if isinstance(layer, Group):
            print(('  ' * indent) + '< {}'.format(layer.name))
            pprint_layers(layer.layers, indent+1)
            print(('  ' * indent) + '>')
        elif isinstance(layer, Image):
            print(('  ' * indent) + '< {} ({}, {}, {}, {}) >'.format(
                layer.name, layer.top, layer.left, layer.bottom, layer.right))


def psd_to_nested_layers(psdfile):
    """
    Convert a `PsdFile` instance to a hierarchy of nested `Layer`
    instances.

    Parameters
    ----------
    psdfile : PsdFile
        A parsed PSD file.

    Returns
    -------
    layers : list of `Layer` instances
        A representation of the parsed hierarchy from the file.
    """
    if not isinstance(psdfile, core.PsdFile):
        raise TypeError("psdfile must be a psdwriter.core.PsdFile instance")

    layers = psdfile.layer_and_mask_info.layer_info.layer_records

    root = Group()
    group_stack = [root]

    for index, layer in reversed(list(enumerate(layers))):
        current_group = group_stack[-1]

        blocks = layer.blocks_map

        name = blocks.get(b'luni', layer).name
        divider = blocks.get(b'lsct', blocks.get(b'lsdk'))
        visible = layer.visible
        opacity = layer.opacity
        blend_mode = layer.blend_mode

        if divider is not None:
            if divider.type in (enums.SectionDividerSetting.closed,
                                enums.SectionDividerSetting.open):
                # group begins
                group = Group(
                    name=name,
                    closed=(
                        divider.type == enums.SectionDividerSetting.closed),
                    blend_mode=blend_mode,
                    visible=visible,
                    opacity=opacity
                )
                group_stack.append(group)
                current_group.layers.append(group)

            elif divider.type == enums.SectionDividerSetting.bounding:
                # group ends
                if len(group_stack) == 1:
                    layers = group_stack[0].layers
                    layer = layers[0]

                    group = Group(
                        name=layer.name,
                        closed=False,
                        blend_mode=layer.blend_mode,
                        visible=layer.visible,
                        opacity=layer.opacity,
                        layers=layers[1:]
                    )

                    group_stack[0].layers = [group]

                else:
                    finished_group = group_stack.pop()
                    assert finished_group is not root

            else:
                raise ValueError("Invalid state")

        else:
            layer = Image(
                name=name,
                top=layer.top,
                left=layer.left,
                bottom=layer.bottom,
                right=layer.right,
                channels=dict(
                    (k, v.image) for (k, v) in layer.channels.items()),
                blend_mode=blend_mode,
                visible=visible,
                opacity=opacity
            )
            current_group.layers.append(layer)

    return root.layers


def _flatten_layers(layers, flat_layers, compression):
    for layer in layers:
        if isinstance(layer, Group):
            if layer.closed:
                divider_type = enums.SectionDividerSetting.closed
            else:
                divider_type = enums.SectionDividerSetting.open
            flat_layers.append(
                l.LayerRecord(
                    name=layer.name,
                    blend_mode=layer.blend_mode,
                    opacity=layer.opacity,
                    visible=layer.visible,
                    blocks=[
                        tagged_block.UnicodeLayerName(name=layer.name),
                        tagged_block.SectionDividerSetting(type=divider_type),
                        tagged_block.LayerId(id=len(flat_layers))
                    ]
                )
            )

            _flatten_layers(layer.layers, flat_layers, compression)

            flat_layers.append(
                l.LayerRecord(
                    blocks=[
                        tagged_block.SectionDividerSetting(
                            type=enums.SectionDividerSetting.bounding),
                        tagged_block.LayerNameSource(id=len(flat_layers))
                    ]
                )
            )

        elif isinstance(layer, Image):
            channels = dict(
                (id, l.ChannelImageData(image=im, compression=compression))
                for (id, im) in layer.channels.items())
            if -1 not in channels:
                im = channels[0].image
                channels[-1] = l.ChannelImageData(
                    image=np.zeros(im.shape, im.dtype) - 1,
                    compression=compression)
            flat_layers.append(
                l.LayerRecord(
                    top=layer.top,
                    left=layer.left,
                    bottom=layer.bottom,
                    right=layer.right,
                    name=layer.name,
                    blend_mode=layer.blend_mode,
                    opacity=layer.opacity,
                    visible=layer.visible,
                    channels=channels,
                    blocks=[
                        tagged_block.UnicodeLayerName(name=layer.name),
                        tagged_block.LayerId(id=len(flat_layers))
                    ]
                )
            )

    return flat_layers


def _adjust_positions(layers):
    top = sys.maxsize
    left = sys.maxsize
    bottom = -sys.maxsize
    right = -sys.maxsize
    for image in _iterate_all_images(layers):
        top = min(image.top, top)
        left = min(image.left, left)
        bottom = max(image.bottom, bottom)
        right = max(image.right, right)

    yoffset = -top
    xoffset = -left

    for image in _iterate_all_images(layers):
        image.top += yoffset
        image.left += xoffset
        image.bottom += yoffset
        image.right += xoffset

    width = right - left
    height = bottom - top

    return width, height


def _determine_channels_and_depth(layers, depth):
    num_channels = 0
    for image in _iterate_all_images(layers):
        for index, channel in image.channels.items():
            num_channels = max(num_channels, index + 1)
            channel_depth = channel.dtype.itemsize * 8
            if depth is None:
                depth = channel_depth
            elif depth != channel_depth:
                raise ValueError("Different image depths in input")

    return num_channels, depth


def _update_sizes(layers):
    for image in _iterate_all_images(layers):
        if len(image.channels) == 0:
            width = 0
            height = 0
        else:
            shape = None
            for index, channel in image.channels.items():
                if shape is None:
                    shape = channel.shape
                elif shape != channel.shape:
                    raise ValueError("Channels in image have different shapes")
            height, width = shape

        if image.bottom is None:
            image.bottom = image.top + height
        elif image.bottom - image.top != height:
            raise ValueError("Channel does not match layer size")

        if image.right is None:
            image.right = image.left + width
        elif image.right - image.left != width:
            raise ValueError("Channel does not match layer size")


def nested_layers_to_psd(
        layers,
        version=enums.Version.version_1,
        compression=enums.Compression.zip,
        color_mode=enums.ColorMode.rgb,
        depth=None,
        size=None):
    """
    Convert a hierarchy of nested `Layer` instances to a `PsdFile`.

    Parameters
    ----------
    layers : list of `Layer` instances
        The hierarchy of layers we want to create.

    version : `enums.Version`, optional
        The version of the PSD spec to follow.

    compression : `enums.Compression`, optional
        The method of image compression to use for the layer image
        data.

    color_mode : `enums.ColorMode`, optional
        The color mode of the resulting PSD file (as well as the input
        image data).

    depth : `enums.ColorDepth`, optional
        The color depth of the resulting image.  Must match the color
        depth of the data passed in.  If not provided, the color depth
        will be automatically determined from the passed-in data.

    size : 2-tuple of int, optional
        The shape in the form ``(height, width)`` of the resulting PSD
        file.  If not provided, the height and width will be set to
        include all passed in layers, and the layers themselves will
        be adjusted so that none fall outside of the image.

    Returns
    -------
    psdfile : PsdFile
        The resulting PSD file.
    """
    try:
        next(_iterate_all_images(layers))
    except StopIteration:
        raise ValueError("No images found in layers")

    _update_sizes(layers)

    num_channels, depth = _determine_channels_and_depth(layers, depth)

    if size is None:
        width, height = _adjust_positions(layers)
    else:
        width, height = size

    flat_layers = _flatten_layers(layers, [], compression)[::-1]

    f = core.PsdFile(
        version=version,
        num_channels=num_channels,
        height=height,
        width=width,
        depth=depth,
        color_mode=color_mode,
        layer_and_mask_info=l.LayerAndMaskInfo(
            layer_info=l.LayerInfo(
                layer_records=flat_layers
            )
        )
    )

    return f