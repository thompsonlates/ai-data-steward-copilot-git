from app.services.databricks_connector import DatabricksConnector


def main() -> None:
    connector = DatabricksConnector()

    # Test 1: OAuth token acquisition.
    token = connector._get_oauth_token()
    print("OAuth M2M token acquired successfully.")
    print("Token preview:", f"{token[:8]}...{token[-4:]}")

    # Test 2: Authenticated Unity Catalog request.
    result = connector.list_catalogs()

    catalogs = [
        catalog.get("name")
        for catalog in result.get("catalogs", [])
    ]

    print("Accessible catalogs:")
    for catalog in catalogs:
        print(f" - {catalog}")


if __name__ == "__main__":
    main()