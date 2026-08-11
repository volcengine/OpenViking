# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

from openviking_cli.retrieve.diversity import DiversityOptions


def test_diversity_options_accepts_public_lambda_alias():
    options = DiversityOptions.model_validate({"strategy": "mmr", "lambda": 0.65})

    assert options.relevance_weight == 0.65
    assert options.model_dump(by_alias=True)["lambda"] == 0.65


def test_diversity_options_resolves_bounded_candidate_limit():
    options = DiversityOptions(candidate_multiplier=4)

    assert options.resolve_candidate_limit(10) == 40
    assert options.resolve_candidate_limit(200) == 500
    assert options.resolve_candidate_limit(0) == 0


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"strategy": "unknown"}, "strategy"),
        ({"lambda": -0.1}, "lambda"),
        ({"max_per_group": 0}, "max_per_group"),
        ({"candidate_multiplier": 11}, "candidate_multiplier"),
        ({"similarity_threshold": 0.79}, "similarity_threshold"),
        ({"unexpected": True}, "unexpected"),
    ],
)
def test_diversity_options_rejects_invalid_values(payload, field):
    with pytest.raises(ValidationError, match=field):
        DiversityOptions.model_validate(payload)
