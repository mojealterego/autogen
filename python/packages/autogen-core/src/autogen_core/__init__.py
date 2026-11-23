import importlib.metadata

__version__ = importlib.metadata.version("autogen_core")

from ._agent import Agent
from ._agent_id import AgentId
from ._agent_instantiation import AgentInstantiationContext
from ._agent_metadata import AgentMetadata
from ._agent_proxy import AgentProxy
from ._agent_runtime import AgentRuntime
from ._agent_type import AgentType
from ._base_agent import BaseAgent
from ._cache_store import CacheStore, InMemoryStore
from ._cancellation_token import CancellationToken
from ._closure_agent import ClosureAgent, ClosureContext
from ._component_config import (
    Component,
    ComponentBase,
    ComponentFromConfig,
    ComponentLoader,
    ComponentModel,
    ComponentSchemaType,
    ComponentToConfig,
    ComponentType,
    is_component_class,
    is_component_instance,
)
from ._constants import (
    EVENT_LOGGER_NAME as EVENT_LOGGER_NAME_ALIAS,
)
from ._constants import (
    ROOT_LOGGER_NAME as ROOT_LOGGER_NAME_ALIAS,
)
from ._constants import (
    TRACE_LOGGER_NAME as TRACE_LOGGER_NAME_ALIAS,
)
from ._default_subscription import DefaultSubscription, default_subscription, type_subscription
from ._default_topic import DefaultTopicId
from ._durable_workflow import (
    AttemptContext,
    CheckpointConflictError,
    CheckpointIntegrityError,
    DurableWorkflowExecutor,
    DurableWorkflowResult,
    RunBusyError,
    OutcomeUnknownError,
    current_attempt,
)
from ._governed_workflow import (
    AuditRecord,
    RetryableError,
    WorkflowExecutor,
    WorkflowPolicy,
    WorkflowResult,
    WorkflowStep,
    verify_audit,
)
from ._image import Image
from ._intervention import (
    DefaultInterventionHandler,
    DropMessage,
    InterventionHandler,
)
from ._message_context import MessageContext
from ._message_handler_context import MessageHandlerContext
from ._routed_agent import RoutedAgent, event, message_handler, rpc
from ._serialization import (
    JSON_DATA_CONTENT_TYPE as JSON_DATA_CONTENT_TYPE_ALIAS,
)
from ._serialization import (
    PROTOBUF_DATA_CONTENT_TYPE as PROTOBUF_DATA_CONTENT_TYPE_ALIAS,
)
from ._serialization import (
    MessageSerializer,
    UnknownPayload,
    try_get_known_serializers_for_type,
)
from ._single_threaded_agent_runtime import SingleThreadedAgentRuntime
from ._subscription import Subscription
from ._subscription_context import SubscriptionInstantiationContext
from ._telemetry import (
    trace_create_agent_span,
    trace_invoke_agent_span,
    trace_tool_span,
)
from ._topic import TopicId
from ._type_prefix_subscription import TypePrefixSubscription
from ._type_subscription import TypeSubscription
from ._types import FunctionCall
from ._workflow_agent import agent_workflow_handler

EVENT_LOGGER_NAME = EVENT_LOGGER_NAME_ALIAS
"""The name of the logger used for structured events."""

ROOT_LOGGER_NAME = ROOT_LOGGER_NAME_ALIAS
"""The name of the root logger."""

TRACE_LOGGER_NAME = TRACE_LOGGER_NAME_ALIAS
"""Logger name used for developer intended trace logging. The content and format of this log should not be depended upon."""

JSON_DATA_CONTENT_TYPE = JSON_DATA_CONTENT_TYPE_ALIAS
"""The content type for JSON data."""

PROTOBUF_DATA_CONTENT_TYPE = PROTOBUF_DATA_CONTENT_TYPE_ALIAS
"""The content type for Protobuf data."""

from ._process_worker import ProcessWorker, WorkerError, WorkerLimits
from ._cluster_coordinator import (
    ClusterCoordinator, ClusterConflict, ClusterDenied, ClusterWorker, ClusterWorkerBackend, JobFence, JobLease, JobSnapshot,
    LeaseLost, TenantSpec, WorkerSession, WorkerSpec, current_cluster_lease,
)
from ._cluster_fabric import FabricClient, FabricPrincipal, FabricServer, FabricTransportError
from ._authority_failover import AuthorityBusy, AuthorityGuard, AuthorityLeaseStore, LeadershipLease, LeadershipLost
from ._openai_chat_provider import OpenAIChatProvider
from ._adaptive_model_router import (AdaptiveModelRouter, ModelProfile, ModelResponse, RouteRequest, TenantRoutePolicy, RouteResult, RouterSnapshot, TenantUsage, ModelHealth, ModelAdmissionDenied, ModelOutcomeUnknown, RetryableModelError, NoModelAvailable)
from ._distributed_dag import distributed_step_handler, ProducerBackend, RemoteStepFailed
from ._resource_scheduler import AdmissionDenied, QueueFull, Resources, ResourceScheduler, SchedulerSnapshot, scheduled_handler

__all__ = [
    "SupervisorSpec", "SupervisorEvent", "SupervisorSnapshot", "SupervisorDenied", "ManagedProcessSupervisor", "InfrastructureSupervisor", "verify_supervisor_audit",
    "SymbolicRule", "SymbolicVerdict", "SymbolicGate", "SymbolicDenied", "symbolically_guarded",
    "WorkflowEvolutionEngine", "EvolutionCandidate", "EvolutionCase", "EvolutionPolicy", "EvolutionObservation", "EvolutionDecision", "EvolutionSnapshot", "EvolutionDenied",
    "HierarchicalSwarm", "SwarmAgent", "SwarmNode", "SwarmPolicy", "SwarmReport", "SwarmDenied",
    "OpenAIChatProvider",
    "AdaptiveModelRouter", "ModelProfile", "ModelResponse", "RouteRequest", "TenantRoutePolicy",
    "RouteResult", "RouterSnapshot", "TenantUsage", "ModelHealth", "ModelAdmissionDenied",
    "ModelOutcomeUnknown", "RetryableModelError", "NoModelAvailable",
    "AuthorityBusy", "AuthorityGuard", "AuthorityLeaseStore", "LeadershipLease", "LeadershipLost",
    "distributed_step_handler", "ProducerBackend", "RemoteStepFailed", "OutcomeUnknownError",
    "FabricClient", "FabricPrincipal", "FabricServer", "FabricTransportError",
    "ClusterCoordinator", "ClusterConflict", "ClusterDenied", "ClusterWorker",
    "ClusterWorkerBackend", "JobFence", "JobLease", "JobSnapshot", "LeaseLost", "TenantSpec", "WorkerSession", "WorkerSpec",
    "current_cluster_lease",
    "AdmissionDenied",
    "QueueFull",
    "Resources",
    "ResourceScheduler",
    "SchedulerSnapshot",
    "scheduled_handler",
    "ProcessWorker",
    "WorkerError",
    "WorkerLimits",
    "Agent",
    "AgentId",
    "AgentProxy",
    "AgentMetadata",
    "AgentRuntime",
    "BaseAgent",
    "CacheStore",
    "InMemoryStore",
    "CancellationToken",
    "AuditRecord",
    "RetryableError",
    "AttemptContext",
    "CheckpointConflictError",
    "CheckpointIntegrityError",
    "DurableWorkflowExecutor",
    "DurableWorkflowResult",
    "RunBusyError",
    "current_attempt",
    "WorkflowExecutor",
    "WorkflowPolicy",
    "WorkflowResult",
    "WorkflowStep",
    "verify_audit",
    "agent_workflow_handler",
    "AgentInstantiationContext",
    "TopicId",
    "Subscription",
    "MessageContext",
    "AgentType",
    "SubscriptionInstantiationContext",
    "MessageHandlerContext",
    "MessageSerializer",
    "try_get_known_serializers_for_type",
    "UnknownPayload",
    "Image",
    "RoutedAgent",
    "ClosureAgent",
    "ClosureContext",
    "message_handler",
    "event",
    "rpc",
    "FunctionCall",
    "TypeSubscription",
    "DefaultSubscription",
    "DefaultTopicId",
    "default_subscription",
    "type_subscription",
    "TypePrefixSubscription",
    "JSON_DATA_CONTENT_TYPE",
    "PROTOBUF_DATA_CONTENT_TYPE",
    "SingleThreadedAgentRuntime",
    "ROOT_LOGGER_NAME",
    "EVENT_LOGGER_NAME",
    "TRACE_LOGGER_NAME",
    "Component",
    "ComponentBase",
    "ComponentFromConfig",
    "ComponentLoader",
    "ComponentModel",
    "ComponentSchemaType",
    "ComponentToConfig",
    "ComponentType",
    "is_component_class",
    "is_component_instance",
    "DropMessage",
    "InterventionHandler",
    "DefaultInterventionHandler",
    "trace_create_agent_span",
    "trace_invoke_agent_span",
    "trace_tool_span",
]

from ._hierarchical_swarm import HierarchicalSwarm, SwarmAgent, SwarmNode, SwarmPolicy, SwarmReport, SwarmDenied

from ._workflow_evolution import (WorkflowEvolutionEngine, EvolutionCandidate, EvolutionCase, EvolutionPolicy, EvolutionObservation, EvolutionDecision, EvolutionSnapshot, EvolutionDenied)

from ._neurosymbolic_gate import SymbolicRule, SymbolicVerdict, SymbolicGate, SymbolicDenied, symbolically_guarded

from ._infrastructure_supervisor import (SupervisorSpec, SupervisorEvent, SupervisorSnapshot, SupervisorDenied, ManagedProcessSupervisor, InfrastructureSupervisor, verify_supervisor_audit)
