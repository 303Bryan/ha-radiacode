# Project specification

The initial design has been superseded by the implemented integration.

- [Current architecture and design constraints](docs/architecture.md)
- [Development and testing](CONTRIBUTING.md)
- [Agent instructions](AGENTS.md)
- [User documentation](docs/README.md)
- [Original specification — historical only](docs/archive/initial-project-spec.md)

Use the current guides when changing the integration. In particular, the integration **keeps the BLE connection open between polls**; the original connect/read/disconnect proposal is no longer applicable.
