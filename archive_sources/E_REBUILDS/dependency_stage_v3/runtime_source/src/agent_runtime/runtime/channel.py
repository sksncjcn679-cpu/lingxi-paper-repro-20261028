"""通信通道：投递消息并计量通信开销。

v0.7.0 把“逻辑消息”和“真实控制面 wire”拆开：

* text 模式仍直接发送 UTF-8 文本；
* structured 模式可选 compact JSON、MessagePack、stateful session JSON 或默认的
  session MessagePack；
* Linux/openEuler 可选 ``control_transport=uds``，每一帧真正经过独立路由进程的
  Unix Domain Socket，并在进程边界完成协议解析/校验。

Agent 业务逻辑目前仍由同一 Orchestrator 进程调度；UDS 模式只对“控制面传输路径”
作真实 OS IPC 化，不把这一事实夸大成“每个 Agent 已独立进程化”。
"""

from __future__ import annotations

from typing import Any

from agent_runtime.observability.metrics import MetricsCollector
from agent_runtime.protocol.message import AgentMessage
from agent_runtime.protocol.serialization import (
    encode_structured,
    encode_structured_msgpack,
    render_text,
    render_text_lean,
)
from agent_runtime.protocol.routine import ROUTINES
from agent_runtime.protocol.session_codec import SessionWireCodec
from agent_runtime.providers.tokenizer import Tokenizer, count_chars


class Channel:
    """逻辑通信通道。"""

    def __init__(
        self,
        mode: str,
        metrics: MetricsCollector,
        tokenizer: Tokenizer,
        logger=None,
        *,
        reference_first: bool = True,
        text_style: str = "verbose",
        structured_wire_codec: str = "session_msgpack",
        control_transport: str = "inproc",
        header_prediction: bool = False,
        pipelined_verification: bool = False,
        ack_chain: bool = False,
        schema_atoms: bool = False,
    ) -> None:
        if mode not in ("text", "structured"):
            raise ValueError(f"未知通信模式: {mode}")
        self.mode = mode
        self.metrics = metrics
        self.tokenizer = tokenizer
        self.logger = logger
        self.reference_first = bool(reference_first)
        if text_style not in {"verbose", "lean"}:
            raise ValueError(f"unknown text style: {text_style}")
        self.text_style = text_style
        if structured_wire_codec not in {
            "json",
            "msgpack",
            "session_json",
            "session_msgpack",
            "session_msgpack_dict",
        }:
            raise ValueError(f"unknown structured wire codec: {structured_wire_codec}")
        if control_transport not in {"inproc", "uds"}:
            raise ValueError(f"unknown control transport: {control_transport}")
        self.structured_wire_codec = structured_wire_codec
        self.control_transport = control_transport
        # Protocol 1.4 CPHC applies to structured *session* codecs only; text and stateless
        # structured codecs are untouched so the baselines keep their exact 1.3 meaning.
        self.header_prediction = bool(header_prediction) and mode == "structured" and structured_wire_codec.startswith("session_")
        self._routine_scope = f"{mode}|{structured_wire_codec}|{int(self.reference_first)}"
        routine = ROUTINES.latest(self._routine_scope) if self.header_prediction else None
        self._session_codec = SessionWireCodec(
            value_dictionary=structured_wire_codec == "session_msgpack_dict",
            predictive=self.header_prediction,
            routine=routine,
            schema_atoms=self.header_prediction and bool(schema_atoms),
        )
        self._transport = None
        router_id: Any = "inproc"
        if control_transport == "uds":
            from agent_runtime.scheduler.control_transport import UDSControlTransport

            self._transport = UDSControlTransport(
                mode=mode,
                codec=structured_wire_codec,
                reference_first=self.reference_first,
                cphc=self.header_prediction,
                routine=routine,
                ack_chain=bool(pipelined_verification) and ack_chain,
                schema_atoms=self.header_prediction and bool(schema_atoms),
            )
            router_id = ("uds", self._transport.router_id)
        if self.header_prediction and routine is not None:
            # Agora-style: a routine document crosses the wire once per peer; afterwards only its
            # digest (already inside the fixed-size hello) is referenced.  The upload is charged
            # to this session's tokens and bytes so the saving claim includes its cost.
            uploaded = bool(getattr(self._transport, "routine_uploaded", False)) if self._transport is not None else ROUTINES.needs_upload(router_id, routine.digest)
            doc = routine.to_json()
            doc_bytes = len(doc.encode("utf-8"))
            self.metrics.record_routine_negotiation(
                uploaded=uploaded,
                tokens=self.tokenizer.count(doc) if uploaded else 0,
                wire_bytes=doc_bytes if uploaded else 0,
                framed_bytes=doc_bytes if uploaded else 0,
            )
            ROUTINES.mark_uploaded(router_id, routine.digest)
        self.pipelined = bool(pipelined_verification) and self._transport is not None
        self._pending_frames = 0
        self.barrier_error: str | None = None
        self.history: list[AgentMessage] = []
        self.last_wire_by_id: dict[str, str] = {}

    def send(self, message: AgentMessage) -> AgentMessage:
        """投递并计量一条消息，返回原消息（逻辑投递）。"""
        wire, physical, is_binary, is_session = self._encode(message)
        tokens = self.tokenizer.count(wire)
        chars = count_chars(wire)
        wire_bytes = len(physical)
        self.metrics.record_message(tokens=tokens, chars=chars, wire_bytes=wire_bytes)
        self.metrics.record_wire_codec(binary=is_binary, session=is_session)
        if self._transport is not None and self.pipelined:
            elapsed, framed_bytes, socket_calls = self._transport.send_pipelined(physical)
            self._pending_frames += 1
            self.metrics.record_ipc(
                payload_bytes=wire_bytes,
                framed_bytes=framed_bytes,
                ack_bytes=0,
                roundtrip_sec=elapsed,
                socket_calls=socket_calls,
                verified=0,
            )
        elif self._transport is not None:
            elapsed, framed_bytes, ack_bytes, socket_calls, verified = self._transport.transmit(physical)
            self.metrics.record_ipc(
                payload_bytes=wire_bytes,
                framed_bytes=framed_bytes,
                ack_bytes=ack_bytes,
                roundtrip_sec=elapsed,
                socket_calls=socket_calls,
                verified=int(verified),
            )
        if message.artifact_references:
            self.metrics.record_artifact_ref(len(message.artifact_references))
        self.history.append(message)
        self.last_wire_by_id[message.message_id] = wire
        if len(self.last_wire_by_id) > 256:
            self.last_wire_by_id.pop(next(iter(self.last_wire_by_id)))
        if self.logger is not None:
            self.logger.debug(
                "channel.send",
                extra={
                    "mode": self.mode,
                    "tokens": tokens,
                    "chars": chars,
                    "wire_bytes": wire_bytes,
                    "msg": message.short(),
                },
            )
        return message

    def _encode(self, message: AgentMessage) -> tuple[str, bytes, bool, bool]:
        """Return ``(token_wire, physical_bytes, binary, session)``.

        For binary structured codecs, token/character accounting uses the reversible JSON
        representation of the *same* logical frame, while ``wire_bytes`` records the actual
        MessagePack bytes that cross the control plane.
        """
        if self.mode == "text":
            wire = render_text(message) if self.text_style == "verbose" else render_text_lean(message)
            return wire, wire.encode("utf-8"), False, False

        codec = self.structured_wire_codec
        if codec == "json":
            wire = encode_structured(message, reference_first=self.reference_first)
            return wire, wire.encode("utf-8"), False, False
        if codec == "msgpack":
            wire = encode_structured(message, reference_first=self.reference_first)
            binary = encode_structured_msgpack(message, reference_first=self.reference_first)
            return wire, binary, True, False
        if codec == "session_json":
            wire = self._session_codec.encode_json(message)
            return wire, wire.encode("utf-8"), False, True
        wire, binary = self._session_codec.encode_dual(message)
        return wire, binary, True, True

    def commit_barrier(self) -> str | None:
        """Verify every pipelined frame before the task result is committed.

        Returns ``None`` when all outstanding frames were acknowledged with the expected
        lossless re-encode digest, otherwise an error string (the caller fails the task).
        Idempotent; a no-op for synchronous transports.
        """
        if not self.pipelined or self._transport is None or self._pending_frames == 0:
            return self.barrier_error
        try:
            info = self._transport.barrier()
        except Exception as exc:  # noqa: BLE001 - any verification failure fails the task
            self.barrier_error = str(exc) or type(exc).__name__
            self._pending_frames = 0
            return self.barrier_error
        if info.get("request_bytes"):
            # Barrier request frames are real outbound control bytes: charge them.
            self.metrics.snapshot.ipc_framed_bytes += int(info["request_bytes"])
            self.metrics.snapshot.ipc_barrier_requests += int(info["request_bytes"]) // 4
        self.metrics.record_ack_batch(
            verified=info["verified"],
            ack_bytes=info["ack_bytes"],
            wait_sec=info["wait_sec"],
            socket_calls=info["socket_calls"],
        )
        self._pending_frames = 0
        return None

    def llm_view(self, message_id: str) -> str | None:
        """Wire as shown to an LLM: schema key atoms expanded to names (fable8).

        Transport tokens are accounted on the real wire; the model gets readable field names.
        """
        wire = self.last_wire_by_id.get(message_id)
        if wire is None or not getattr(self._session_codec, "schema_atoms", False):
            return wire
        import json as _json

        from agent_runtime.protocol.schema_atoms import readable_keys

        try:
            frame = _json.loads(wire)
        except ValueError:
            return wire
        if not isinstance(frame, dict):
            return wire
        frame = {k: (readable_keys(v) if k in ("ip", "rs", "e") else v) for k, v in frame.items()}
        return _json.dumps(frame, ensure_ascii=False, separators=(",", ":"))

    def close(self) -> None:
        with_barrier = self.pipelined and self._pending_frames
        if with_barrier:
            self.commit_barrier()
        if self.header_prediction:
            stats = self._session_codec.prediction_stats
            self.metrics.record_cphc(
                predicted=stats["predicted_fields"],
                explicit=stats["explicit_fields"],
                reference_hits=stats["reference_table_hits"],
            )
            observed = self._session_codec.encoder_observed
            if observed:
                ROUTINES.learn(self._routine_scope, observed)
            self.header_prediction = False  # idempotent close
        transport = self._transport
        self._transport = None
        if transport is not None:
            transport.close()


__all__ = ["Channel"]
