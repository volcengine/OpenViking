# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ModalContent and Vectorize media extension."""
from openviking.core.context import ModalContent, Vectorize


def test_modal_content_fields():
    mc = ModalContent(mime_type="image/jpeg", uri="viking://agent/resources/img.jpg")
    assert mc.mime_type == "image/jpeg"
    assert mc.uri == "viking://agent/resources/img.jpg"
    assert mc.data is None


def test_vectorize_default_no_media():
    v = Vectorize(text="hello")
    assert v.text == "hello"
    assert v.media is None


def test_vectorize_with_media():
    mc = ModalContent(mime_type="image/png", uri="viking://agent/resources/shot.png")
    v = Vectorize(text="screenshot of dashboard", media=mc)
    assert v.text == "screenshot of dashboard"
    assert v.media is mc
    assert v.media.mime_type == "image/png"


def test_vectorize_get_vectorization_text_still_works():
    v = Vectorize(text="doc text", media=ModalContent(mime_type="image/png", uri="img.png"))
    assert v.text == "doc text"


def test_modal_content_with_data_bytes():
    mc = ModalContent(mime_type="image/jpeg", uri="viking://img.jpg", data=b"\xff\xd8\xff")
    assert mc.data == b"\xff\xd8\xff"
