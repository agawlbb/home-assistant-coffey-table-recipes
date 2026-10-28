"""HA integration test fixtures."""
import pytest
from unittest.mock import patch

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def isolate_shopping_network(request):
    # Existing lifecycle/adapter fixtures mock Client.read; never start a real
    # external connection behind those mocks. Wire tests use loopback sockets.
    if request.module.__name__.endswith('test_shopping_socket'):
        yield
    else:
        with patch('custom_components.coffey_table_recipes.shopping_socket.ShoppingSocket.start'):
            yield
