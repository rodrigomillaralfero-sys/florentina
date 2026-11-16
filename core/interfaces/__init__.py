"""Las costuras del motor. Todo lo que una instancia tiene que implementar pasa por aqui."""

from core.interfaces.channel import Channel, InboundEvent, InboundHandler, OutboundMessage
from core.interfaces.interaction_policy import (
    ControlKind,
    InteractionPolicy,
    MechanicalCommand,
)
from core.interfaces.model_adapter import (
    EmbeddingResponse,
    ModelAdapter,
    ModelBadOutput,
    ModelError,
    ModelIdentity,
    ModelMessage,
    ModelRateLimited,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelUnavailable,
)
from core.interfaces.subject_key import SubjectKey, SubjectResolver

__all__ = [
    "Channel",
    "InboundEvent",
    "InboundHandler",
    "OutboundMessage",
    "ControlKind",
    "InteractionPolicy",
    "MechanicalCommand",
    "EmbeddingResponse",
    "ModelAdapter",
    "ModelBadOutput",
    "ModelError",
    "ModelIdentity",
    "ModelMessage",
    "ModelRateLimited",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "ModelUnavailable",
    "SubjectKey",
    "SubjectResolver",
]
