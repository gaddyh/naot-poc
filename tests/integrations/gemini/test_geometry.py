import pytest

from naot_poc.integrations.gemini.geometry import (
    PixelBoundingBox,
    clamp_bbox,
    normalized_to_pixels,
)


def test_normalized_box_maps_to_pixel_coordinates():
    box = normalized_to_pixels(
        top=100,
        left=200,
        bottom=500,
        right=800,
        image_width=1000,
        image_height=2000,
    )

    assert box == PixelBoundingBox(x1=200, y1=200, x2=800, y2=1000)


def test_clamp_box_keeps_coordinates_in_frame():
    box = clamp_bbox(
        PixelBoundingBox(x1=-5, y1=10, x2=120, y2=150),
        image_width=100,
        image_height=100,
    )

    assert box == PixelBoundingBox(x1=0, y1=10, x2=100, y2=100)


@pytest.mark.parametrize("width,height", [(0, 10), (10, 0), (-1, 10)])
def test_normalized_box_rejects_non_positive_dimensions(width, height):
    with pytest.raises(ValueError):
        normalized_to_pixels(
            top=0,
            left=0,
            bottom=100,
            right=100,
            image_width=width,
            image_height=height,
        )
