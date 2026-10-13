# Future enhancements

See
[architecture.md](architecture.md) for constraints and
[compatibility.md](compatibility.md) for verified coverage.

## Simpler setup

Provide a setup command that obtains compatible ordinary and Alice engines/networks,
checks their identities and search compatibility, and saves configuration. Select
platform/CPU-compatible binaries and keep engine/network releases consistent with their
licenses and source notices. User resource ceilings and pairing remain local.

Persist settings and downloaded assets in user configuration/data directories outside
uvx's disposable environments. Subsequent `uvx pychess-external-engine` launches should
use the saved configuration. Add clear setup, upgrade and configuration diagnostics.

## Broader verification

Finish Firefox native UI and permission-denial checks with manual assistance when
necessary. Test real Safari/macOS and supported Windows/macOS/Linux architectures;
Linux WebKit does not substitute for Safari. Expand native/custom-variant tests with
exact rules and legal histories before adding compatibility claims.

If direct HTTP loopback is insufficient, evaluate trusted local TLS or a browser
extension/native-messaging transport. Preserve pairing, origin checks and normal browser
security. Desktop launchers/installers can be considered after the uvx workflow is usable.

## Additional adapters and workflows

Two-board analysis needs board scheduling, pocket-state correlation and its own
ownership model. Independent single-board scores do not constitute joint search.
Hidden-information variants need an explicit visibility/analysis policy. USI/UCCI
engines need protocol and notation adapters. Practice needs bounded search and
extension-owned engine scheduling before native integration.

Alice Chess960, custom Alice rules and additional FEN formats require matching native
engine/rules contracts and separate capability tests. Preserve required Alice rules
support in ordinary package installations.

## Release maintenance

Provide repeatable release builds and consider PyPI Trusted Publishing. Keep dependency
pins and the rule-name schema in agreement, test supported Python versions and retain
immutable release artifacts. Automatic engine/network updates must preserve reproducible
versions and recover safely from incomplete downloads or incompatible upgrades.
