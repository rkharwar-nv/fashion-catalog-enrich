# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures."""

from io import BytesIO

import pytest
from PIL import Image


@pytest.fixture
def sample_image_bytes() -> bytes:
    """A minimal valid PNG, enough to pass image decoding."""
    image = Image.new("RGB", (1, 1), color="red")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
