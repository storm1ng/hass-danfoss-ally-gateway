"""Tests for integration setup (__init__.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.danfoss_ally_gateway.const import (
    BACKEND_Z2M,
    BACKEND_ZHA,
    CONF_BACKEND,
    CONF_MQTT_BASE_TOPIC,
    CONF_ROOM_NAME,
    CONF_TRV_ENTITIES,
    DOMAIN,
    SUBENTRY_ROOM,
)


def _make_ally_entry(
    hass: HomeAssistant,
    backend: str = BACKEND_Z2M,
    mqtt_base_topic: str = "zigbee2mqtt",
) -> MockConfigEntry:
    """Create and add a Danfoss Ally Gateway config entry."""
    data = {CONF_BACKEND: backend}
    if backend == BACKEND_Z2M:
        data[CONF_MQTT_BASE_TOPIC] = mqtt_base_topic
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    return entry


class TestSetupDependencyValidation:
    """Verify async_setup_entry checks for required backend integrations."""

    async def test_z2m_without_mqtt_not_ready(self, hass: HomeAssistant):
        """Setup with Z2M backend should result in SETUP_RETRY if MQTT is missing."""
        entry = _make_ally_entry(hass, backend=BACKEND_Z2M)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY

    async def test_zha_without_zha_not_ready(self, hass: HomeAssistant):
        """Setup with ZHA backend should result in SETUP_RETRY if ZHA is missing."""
        entry = _make_ally_entry(hass, backend=BACKEND_ZHA)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY


class TestOrphanedEntityCleanup:
    """Test cleanup of orphaned entities when TRVs are removed from rooms."""

    async def test_orphaned_entities_cleaned_on_boot(self, hass: HomeAssistant):
        """Orphaned entities from removed TRVs should be cleaned up on boot."""
        # Setup MQTT integration
        mqtt_entry = MockConfigEntry(domain="mqtt", title="MQTT")
        mqtt_entry.add_to_hass(hass)

        # Create entry with a room containing only 1 TRV
        # (simulating state after a reconfigure that removed TRV 2)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_BACKEND: BACKEND_Z2M, CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
            title="Danfoss Ally Gateway",
            subentries_data=(
                {
                    "subentry_id": "room_1",
                    "subentry_type": SUBENTRY_ROOM,
                    "title": "Living Room",
                    "data": {
                        CONF_ROOM_NAME: "Living Room",
                        CONF_TRV_ENTITIES: ["trv_1"],  # Only 1 TRV now
                    },
                },
            ),
        )
        entry.add_to_hass(hass)

        # Manually register "orphaned" entities from a prior run with 2 TRVs
        # These simulate entities left behind after removing trv_2
        ent_reg = er.async_get(hass)
        ent_reg.async_get_or_create(
            domain="sensor",
            platform=DOMAIN,
            unique_id=f"{DOMAIN}_{entry.entry_id}_room_1_trv_2_heating_demand",
            config_entry=entry,
            config_subentry_id="room_1",
            suggested_object_id="living_room_trv_2_heating_demand",
        )
        ent_reg.async_get_or_create(
            domain="sensor",
            platform=DOMAIN,
            unique_id=f"{DOMAIN}_{entry.entry_id}_room_1_trv_2_load_estimate",
            config_entry=entry,
            config_subentry_id="room_1",
            suggested_object_id="living_room_trv_2_load_estimate",
        )
        # Also register a multi-TRV-only entity (load_room_mean)
        ent_reg.async_get_or_create(
            domain="sensor",
            platform=DOMAIN,
            unique_id=f"{DOMAIN}_{entry.entry_id}_room_1_load_room_mean",
            config_entry=entry,
            config_subentry_id="room_1",
            suggested_object_id="living_room_load_room_mean",
        )
        # And the load balancing switch
        ent_reg.async_get_or_create(
            domain="switch",
            platform=DOMAIN,
            unique_id=f"{DOMAIN}_{entry.entry_id}_room_1_load_balancing",
            config_entry=entry,
            config_subentry_id="room_1",
            suggested_object_id="living_room_load_balancing",
        )

        # Verify orphaned entities exist before setup
        entities_before = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        orphaned_before = [
            e
            for e in entities_before
            if "trv_2" in e.unique_id
            or "load_room_mean" in e.unique_id
            or "load_balancing" in e.unique_id
        ]
        assert len(orphaned_before) == 4  # 2 trv_2 sensors + load_room_mean + switch

        # Mock the backend
        with patch(
            "custom_components.danfoss_ally_gateway.Z2MBackend"
        ) as mock_backend_cls:
            mock_backend = AsyncMock()
            mock_backend.async_setup = AsyncMock()
            mock_backend.async_subscribe_trv = AsyncMock()
            mock_backend.register_state_callback = AsyncMock(return_value=lambda: None)
            mock_backend.register_announce_callback = AsyncMock(
                return_value=lambda: None
            )
            mock_backend_cls.return_value = mock_backend

            # Setup the integration (this should clean up orphaned entities)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # Verify orphaned entities are removed
        entities_after = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        orphaned_after = [
            e
            for e in entities_after
            if "trv_2" in e.unique_id
            or "load_room_mean" in e.unique_id
            or "load_balancing" in e.unique_id
        ]
        assert len(orphaned_after) == 0  # All orphans cleaned up

        # Verify entities for trv_1 still exist
        trv1_entities = [e for e in entities_after if "trv_1" in e.unique_id]
        assert len(trv1_entities) == 2  # heating_demand + load_estimate

    async def test_multi_to_single_trv_cleanup(self, hass: HomeAssistant):
        """Multi-TRV-only entities should be removed when going to single TRV."""
        mqtt_entry = MockConfigEntry(domain="mqtt", title="MQTT")
        mqtt_entry.add_to_hass(hass)

        # Entry with single TRV
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_BACKEND: BACKEND_Z2M, CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
            title="Danfoss Ally Gateway",
            subentries_data=(
                {
                    "subentry_id": "bedroom",
                    "subentry_type": SUBENTRY_ROOM,
                    "title": "Bedroom",
                    "data": {
                        CONF_ROOM_NAME: "Bedroom",
                        CONF_TRV_ENTITIES: ["trv_a"],  # Now only 1 TRV
                    },
                },
            ),
        )
        entry.add_to_hass(hass)

        # Register multi-TRV-only entities from prior 2-TRV config
        ent_reg = er.async_get(hass)
        ent_reg.async_get_or_create(
            domain="sensor",
            platform=DOMAIN,
            unique_id=f"{DOMAIN}_{entry.entry_id}_bedroom_load_room_mean",
            config_entry=entry,
            config_subentry_id="bedroom",
        )
        ent_reg.async_get_or_create(
            domain="switch",
            platform=DOMAIN,
            unique_id=f"{DOMAIN}_{entry.entry_id}_bedroom_load_balancing",
            config_entry=entry,
            config_subentry_id="bedroom",
        )

        # Mock backend
        with patch(
            "custom_components.danfoss_ally_gateway.Z2MBackend"
        ) as mock_backend_cls:
            mock_backend = AsyncMock()
            mock_backend.async_setup = AsyncMock()
            mock_backend.async_subscribe_trv = AsyncMock()
            mock_backend.register_state_callback = AsyncMock(return_value=lambda: None)
            mock_backend.register_announce_callback = AsyncMock(
                return_value=lambda: None
            )
            mock_backend_cls.return_value = mock_backend

            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # Verify multi-TRV entities are removed
        entities_after = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        multi_trv_entities = [
            e
            for e in entities_after
            if "load_room_mean" in e.unique_id or "load_balancing" in e.unique_id
        ]
        assert len(multi_trv_entities) == 0

    async def test_no_cleanup_when_config_unchanged(self, hass: HomeAssistant):
        """No entities should be removed when TRV configuration is unchanged."""
        mqtt_entry = MockConfigEntry(domain="mqtt", title="MQTT")
        mqtt_entry.add_to_hass(hass)

        # Entry with 2 TRVs
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_BACKEND: BACKEND_Z2M, CONF_MQTT_BASE_TOPIC: "zigbee2mqtt"},
            title="Danfoss Ally Gateway",
            subentries_data=(
                {
                    "subentry_id": "office",
                    "subentry_type": SUBENTRY_ROOM,
                    "title": "Office",
                    "data": {
                        CONF_ROOM_NAME: "Office",
                        CONF_TRV_ENTITIES: ["trv_x", "trv_y"],
                    },
                },
            ),
        )
        entry.add_to_hass(hass)

        # Mock backend
        with patch(
            "custom_components.danfoss_ally_gateway.Z2MBackend"
        ) as mock_backend_cls:
            mock_backend = AsyncMock()
            mock_backend.async_setup = AsyncMock()
            mock_backend.async_subscribe_trv = AsyncMock()
            mock_backend.register_state_callback = AsyncMock(return_value=lambda: None)
            mock_backend.register_announce_callback = AsyncMock(
                return_value=lambda: None
            )
            mock_backend_cls.return_value = mock_backend

            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # Count entities
        ent_reg = er.async_get(hass)
        entities_after = er.async_entries_for_config_entry(ent_reg, entry.entry_id)

        # Should have:
        # - 1 climate
        # - 3 binary_sensor (heat_required, heat_available, window_open)
        # - 4 sensor (2 TRVs * (heating_demand + load_estimate))
        # - 1 sensor (load_room_mean for multi-TRV)
        # - 1 select (programming_mode)
        # - 1 switch (load_balancing for multi-TRV)
        # Total: 11 entities
        assert len(entities_after) == 11

        # Verify all expected entity types are present
        by_domain = {}
        for entity in entities_after:
            by_domain.setdefault(entity.domain, []).append(entity)

        assert len(by_domain.get("climate", [])) == 1
        assert len(by_domain.get("binary_sensor", [])) == 3
        assert len(by_domain.get("sensor", [])) == 5  # 4 per-TRV + 1 load_room_mean
        assert len(by_domain.get("select", [])) == 1
        assert len(by_domain.get("switch", [])) == 1
