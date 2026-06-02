"""MQTT publisher for Home Assistant Discovery."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from vaillant_eebus.utils import env_bool, env_int, env_str

logger = logging.getLogger(__name__)


class HAMqttPublisher:
    """Publishes HA Discovery configs and per-sensor state to an MQTT broker.

    Enabled when ``HA_MQTT_HOST`` is set (env var or via ``mqtt_secrets.py``).
    Requires ``paho-mqtt``.
    """

    def __init__(self, *, device_id: str, device_name: str):
        self.device_id = device_id
        self.device_name = device_name
        self.base_prefix = env_str("HA_MQTT_PREFIX", "homeassistant").strip("/")
        self.state_prefix = env_str("HA_MQTT_STATE_PREFIX", "ship").strip("/")

        # Broker credentials:
        # 1) Prefer local file `mqtt_secrets.py` (keeps secrets out of main script)
        # 2) Allow environment variables to override
        secrets_host = ""
        secrets_port = 1883
        secrets_user = ""
        secrets_password = ""
        try:
            from . import mqtt_secrets as _mqtt_secrets  # type: ignore

            secrets_host = str(getattr(_mqtt_secrets, "HA_MQTT_HOST", "") or "").strip()
            secrets_port = int(getattr(_mqtt_secrets, "HA_MQTT_PORT", 1883) or 1883)
            secrets_user = str(getattr(_mqtt_secrets, "HA_MQTT_USER", "") or "").strip()
            secrets_password = str(getattr(_mqtt_secrets, "HA_MQTT_PASSWORD", "") or "")
        except Exception:
            pass

        self.host = env_str("HA_MQTT_HOST", secrets_host).strip()
        self.port = env_int("HA_MQTT_PORT", secrets_port)
        self.username = env_str("HA_MQTT_USER", secrets_user)
        self.password = env_str("HA_MQTT_PASSWORD", secrets_password)
        self.enabled = bool(self.host)
        self.debug = env_bool("SHIP_MQTT_DEBUG", False)
        self.retain_state = env_bool("SHIP_MQTT_RETAIN_STATE", False)
        self._discovered_object_ids: set[str] = set()
        self._mqtt = None

    def connect(self) -> None:
        """Connect to the MQTT broker. Raises on any misconfiguration.

        The HA bridge has no purpose without MQTT, so missing host /
        missing paho-mqtt / failed connect are all hard errors.
        Uses an LWT (will) message so HA sees us as offline if we crash.
        """
        if not self.enabled:
            raise RuntimeError(
                "MQTT broker not configured — set HA_MQTT_HOST (env var or "
                "mqtt_secrets.py). The HA bridge cannot publish without it."
            )
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "paho-mqtt is required for the HA bridge. Install with: pip3 install paho-mqtt"
            ) from e

        client_kwargs: Dict[str, Any] = {"client_id": f"ship-{self.device_id}"}
        cb_api = getattr(mqtt, "CallbackAPIVersion", None)
        if cb_api is not None:
            try:
                client_kwargs["callback_api_version"] = cb_api.VERSION2
            except Exception:
                pass

        client = mqtt.Client(**client_kwargs)
        if self.username:
            client.username_pw_set(self.username, self.password or None)
        try:
            client.will_set(self._topic_availability(), payload="offline", qos=0, retain=True)
        except Exception:
            pass
        try:
            client.connect(self.host, self.port, keepalive=30)
        except Exception as e:
            raise RuntimeError(f"MQTT connect to {self.host}:{self.port} failed: {e}") from e
        client.loop_start()
        self._mqtt = client
        self.publish_availability(True)
        logger.debug("✅ [MQTT] Connected to mqtt://%s:%d", self.host, self.port)
        if self.debug:
            logger.debug(
                "[MQTT] device_id=%s discovery_prefix=%s state_prefix=%s availability_topic=%s",
                self.device_id,
                self.base_prefix,
                self.state_prefix,
                self._topic_availability(),
            )

    def close(self) -> None:
        """Publish offline availability and close MQTT cleanly."""
        if not self.enabled or self._mqtt is None:
            return
        try:
            self.publish_availability(False)
        except Exception:
            pass
        try:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
        except Exception:
            pass
        self._mqtt = None

    def forget_discovery(self, object_id: str) -> None:
        """Drop the local discovery record so ensure_discovery() re-publishes."""
        self._discovered_object_ids.discard(object_id)

    def _topic_availability(self) -> str:
        return f"{self.state_prefix}/{self.device_id}/availability"

    def publish_availability(self, online: bool) -> None:
        if not self.enabled or self._mqtt is None:
            return
        if self.debug:
            logger.debug(
                "[MQTT] availability %s = %s (retain)",
                self._topic_availability(),
                "online" if online else "offline",
            )
        self._mqtt.publish(
            self._topic_availability(),
            payload=("online" if online else "offline"),
            qos=0,
            retain=True,
        )

    def _topic_state(self, object_id: str) -> str:
        return f"{self.state_prefix}/{self.device_id}/{object_id}/state"

    def _topic_config(self, object_id: str) -> str:
        return f"{self.base_prefix}/sensor/{self.device_id}/{object_id}/config"

    def ensure_discovery(
        self, *, object_id: str, name: str, unit: str, device_class: str, state_class: str
    ) -> None:
        """Publish Home Assistant MQTT Discovery config for a sensor (once).

        HA listens on ``<discovery_prefix>/sensor/<device>/<object_id>/config``.
        Publishing config once is enough; state updates use a separate state topic.
        """
        if not self.enabled or self._mqtt is None:
            return
        if object_id in self._discovered_object_ids:
            return

        payload: Dict[str, Any] = {
            "name": name,
            "unique_id": f"{self.device_id}_{object_id}",
            "state_topic": self._topic_state(object_id),
            "availability_topic": self._topic_availability(),
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": [self.device_id],
                "name": self.device_name,
            },
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if device_class:
            payload["device_class"] = device_class
        if state_class:
            payload["state_class"] = state_class

        if self.debug:
            logger.debug("[MQTT] discovery %s (retain)", self._topic_config(object_id))
        self._mqtt.publish(
            self._topic_config(object_id),
            json.dumps(payload, ensure_ascii=False),
            qos=0,
            retain=True,
        )
        self._discovered_object_ids.add(object_id)

    def publish_state(self, *, object_id: str, value: float) -> None:
        """Publish the current sensor value to the MQTT state topic."""
        if not self.enabled or self._mqtt is None:
            return
        if self.debug:
            logger.debug("[MQTT] state %s = %s", self._topic_state(object_id), value)
        self._mqtt.publish(
            self._topic_state(object_id),
            payload=str(value),
            qos=0,
            retain=self.retain_state,
        )

    def publish_state_text(self, *, object_id: str, value: str) -> None:
        """Publish a text-valued state (e.g., HVAC operation mode)."""
        if not self.enabled or self._mqtt is None:
            return
        if self.debug:
            logger.debug("[MQTT] state %s = %r", self._topic_state(object_id), value)
        self._mqtt.publish(
            self._topic_state(object_id),
            payload=value,
            qos=0,
            retain=self.retain_state,
        )
