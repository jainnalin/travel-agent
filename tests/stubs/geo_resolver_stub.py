# tests/stubs/geo_resolver_stub.py
def resolve_city(city_name: str):
    # Return dummy coordinates for testing
    return 28.5383, -81.3792  # Orlando city center

def resolve_airport(airport_code: str):
    # Return dummy coordinates for testing
    return 28.4312, -81.3081  # MCO airport