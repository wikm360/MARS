"""
Shared data models and message schema for MRAS protocol.
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MessageType(str, Enum):
    # Handshake
    AUTH = "auth"
    AUTH_OK = "auth_ok"
    AUTH_FAIL = "auth_fail"

    # Heartbeat
    PING = "ping"
    PONG = "pong"

    # Commands (admin → server → client)
    COMMAND = "command"
    COMMAND_RESULT = "command_result"
    COMMAND_ERROR = "command_error"

    # File transfer
    FILE_UPLOAD = "file_upload"
    FILE_DOWNLOAD = "file_download"
    FILE_DATA = "file_data"

    # Management
    CLIENT_LIST = "client_list"
    CLIENT_CONNECTED = "client_connected"
    CLIENT_DISCONNECTED = "client_disconnected"
    REDIRECT = "redirect"
    UPDATE_SERVERS = "update_servers"

    # Errors
    ERROR = "error"


class Role(str, Enum):
    CLIENT = "client"
    ADMIN = "admin"


@dataclass
class Message:
    type: MessageType
    payload: dict[str, Any] = field(default_factory=dict)
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    target_id: Optional[str] = None   # client_id the admin targets
    sender_id: Optional[str] = None   # filled by server

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "payload": self.payload,
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "target_id": self.target_id,
            "sender_id": self.sender_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            type=MessageType(data["type"]),
            payload=data.get("payload", {}),
            msg_id=data.get("msg_id", str(uuid.uuid4())),
            timestamp=data.get("timestamp", time.time()),
            target_id=data.get("target_id"),
            sender_id=data.get("sender_id"),
        )


@dataclass
class ClientInfo:
    client_id: str
    hostname: str
    os: str
    username: str
    ip: str
    connected_at: float
    last_seen: float
    version: str = "1.0.0"

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "hostname": self.hostname,
            "os": self.os,
            "username": self.username,
            "ip": self.ip,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClientInfo":
        return cls(**data)
