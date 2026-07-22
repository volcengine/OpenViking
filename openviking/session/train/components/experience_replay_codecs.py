# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from typing import Any

from openviking.message import Message
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.train.domain import (
    CriterionResult,
    Policy,
    PolicySet,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.gradients import PatchSemanticGradient
from openviking.telemetry import replay
from openviking.telemetry.replay.models import EncodedValue, ReplayCodecError
from openviking_cli.session.user_id import UserIdentifier


def _encoded(payload: dict[str, Any], name: str) -> EncodedValue:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ReplayCodecError(f"Replay codec payload is missing encoded field {name!r}")
    return value


@replay.codec(Message, name="openviking.message")
class MessageReplayCodec:
    @staticmethod
    def encode(value: Message, encode):
        return {"data": encode(value.to_dict())}

    @staticmethod
    def decode(payload, decode):
        data = decode(_encoded(payload, "data"))
        if not isinstance(data, dict):
            raise ReplayCodecError("Message replay payload must decode to a dictionary")
        return Message.from_dict(data)


@replay.codec(RequestContext, name="openviking.request_context")
class RequestContextReplayCodec:
    @staticmethod
    def encode(value: RequestContext, encode):
        return {
            "user": encode(value.user.to_dict()),
            "role": encode(str(value.role)),
            "actor_peer_id": encode(value.actor_peer_id),
            "legacy_agent_id": encode(value.legacy_agent_id),
            "from_oauth": encode(value.from_oauth),
        }

    @staticmethod
    def decode(payload, decode):
        user = decode(_encoded(payload, "user"))
        if not isinstance(user, dict):
            raise ReplayCodecError("RequestContext user must decode to a dictionary")
        return RequestContext(
            user=UserIdentifier.from_dict(user),
            role=Role(decode(_encoded(payload, "role"))),
            actor_peer_id=decode(_encoded(payload, "actor_peer_id")),
            legacy_agent_id=decode(_encoded(payload, "legacy_agent_id")),
            from_oauth=decode(_encoded(payload, "from_oauth")),
        )


@replay.codec(Trajectory, name="openviking.train.trajectory")
class TrajectoryReplayCodec:
    @staticmethod
    def encode(value: Trajectory, encode):
        return {
            "name": encode(value.name),
            "uri": encode(value.uri),
            "content": encode(value.content),
            "outcome": encode(value.outcome),
            "retrieval_anchor": encode(value.retrieval_anchor),
            "metadata": encode(value.metadata),
        }

    @staticmethod
    def decode(payload, decode):
        return Trajectory(
            name=decode(_encoded(payload, "name")),
            uri=decode(_encoded(payload, "uri")),
            content=decode(_encoded(payload, "content")),
            outcome=decode(_encoded(payload, "outcome")),
            retrieval_anchor=decode(_encoded(payload, "retrieval_anchor")),
            metadata=decode(_encoded(payload, "metadata")),
        )


@replay.codec(RubricEvaluation, name="openviking.train.rubric_evaluation")
class RubricEvaluationReplayCodec:
    @staticmethod
    def encode(value: RubricEvaluation, encode):
        criteria = [
            {
                "criterion_name": item.criterion_name,
                "passed": item.passed,
                "score": item.score,
                "feedback": item.feedback,
                "evidence": item.evidence,
                "metadata": item.metadata,
            }
            for item in value.criterion_results
        ]
        return {
            "passed": encode(value.passed),
            "score": encode(value.score),
            "criterion_results": encode(criteria),
            "metadata": encode(value.metadata),
        }

    @staticmethod
    def decode(payload, decode):
        criteria = decode(_encoded(payload, "criterion_results"))
        if not isinstance(criteria, list):
            raise ReplayCodecError("RubricEvaluation criteria must decode to a list")
        return RubricEvaluation(
            passed=decode(_encoded(payload, "passed")),
            score=decode(_encoded(payload, "score")),
            criterion_results=[CriterionResult(**item) for item in criteria],
            metadata=decode(_encoded(payload, "metadata")),
        )


@replay.codec(PolicySet, name="openviking.train.policy_set")
class PolicySetReplayCodec:
    @staticmethod
    def encode(value: PolicySet, encode):
        policies = [
            {
                "name": item.name,
                "uri": item.uri,
                "version": item.version,
                "status": item.status,
                "content": item.content,
                "metadata": item.metadata,
                "links": item.links,
                "backlinks": item.backlinks,
            }
            for item in value.policies
        ]
        return {
            "root_uri": encode(value.root_uri),
            "policies": encode(policies),
            "metadata": encode(value.metadata),
        }

    @staticmethod
    def decode(payload, decode):
        policies = decode(_encoded(payload, "policies"))
        if not isinstance(policies, list):
            raise ReplayCodecError("PolicySet policies must decode to a list")
        return PolicySet(
            root_uri=decode(_encoded(payload, "root_uri")),
            policies=[Policy(**item) for item in policies],
            metadata=decode(_encoded(payload, "metadata")),
        )


@replay.codec(MemoryFile, name="openviking.memory_file")
class MemoryFileReplayCodec:
    @staticmethod
    def encode(value: MemoryFile, encode):
        return {"data": encode(value.model_dump(mode="python"))}

    @staticmethod
    def decode(payload, decode):
        data = decode(_encoded(payload, "data"))
        if not isinstance(data, dict):
            raise ReplayCodecError("MemoryFile replay payload must decode to a dictionary")
        return MemoryFile.model_validate(data)


@replay.codec(StoredLink, name="openviking.stored_link")
class StoredLinkReplayCodec:
    @staticmethod
    def encode(value: StoredLink, encode):
        return {"data": encode(value.model_dump(mode="python"))}

    @staticmethod
    def decode(payload, decode):
        data = decode(_encoded(payload, "data"))
        if not isinstance(data, dict):
            raise ReplayCodecError("StoredLink replay payload must decode to a dictionary")
        return StoredLink.model_validate(data)


@replay.codec(PatchSemanticGradient, name="openviking.train.patch_semantic_gradient")
class PatchSemanticGradientReplayCodec:
    @staticmethod
    def encode(value: PatchSemanticGradient, encode):
        return {
            "before_file": encode(value.before_file),
            "after_file": encode(value.after_file),
            "base_version": encode(value.base_version),
            "rationale": encode(value.rationale),
            "links": encode(value.links),
            "confidence": encode(value.confidence),
            "metadata": encode(value.metadata),
        }

    @staticmethod
    def decode(payload, decode):
        return PatchSemanticGradient(
            before_file=decode(_encoded(payload, "before_file")),
            after_file=decode(_encoded(payload, "after_file")),
            base_version=decode(_encoded(payload, "base_version")),
            rationale=decode(_encoded(payload, "rationale")),
            links=decode(_encoded(payload, "links")),
            confidence=decode(_encoded(payload, "confidence")),
            metadata=decode(_encoded(payload, "metadata")),
        )
